"""FastAPI router for the filebox plugin backend.

Mounted by Hermes Dashboard at /api/plugins/filebox/. Every handler guards
paths against the root whitelist and opens only the canonical return value
of guard_path (TOCTOU-safe). No network I/O, no model tokens.
"""
from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from send2trash import send2trash

# The dashboard loader imports this file as a standalone module
# (spec_from_file_location) WITHOUT adding the dashboard/ dir to sys.path, so
# sibling modules (guard, roots) are not importable by bare name. Add our own
# directory first — the same technique tests/ uses to import this module.
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))

from guard import GuardError, guard_path, guard_tree
from roots import RootStore

router = APIRouter()

TEXT_LIMIT = 64 * 1024
TEXT_FULL = 1024 * 1024


def _state_dir() -> Path:
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return Path(home) / "state" / "filebox"


_STORE: RootStore | None = None


def reset_store() -> None:
    global _STORE
    _STORE = None


def _get_store() -> RootStore:
    global _STORE
    if _STORE is None:
        _STORE = RootStore(state_dir=_state_dir())
    return _STORE


def _guard(path: str, roots: list[str]) -> str:
    try:
        return guard_path(path, roots)
    except GuardError as e:
        raise HTTPException(status_code=403, detail=str(e))


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


class OpenRequest(BaseModel):
    path: str


class RenameRequest(BaseModel):
    path: str
    new_name: str


class TrashRequest(BaseModel):
    paths: list[str]


class BulkRenameRequest(BaseModel):
    items: list[str]
    mode: str  # replace | prefix | suffix | seq
    find: str | None = None
    replace: str | None = None
    prefix: str | None = None
    suffix: str | None = None
    seq_start: int = 1
    seq_pad: int = 0


class AddRootRequest(BaseModel):
    path: str


class CopyMoveRequest(BaseModel):
    sources: list[str]
    target_dir: str
    on_conflict: str = "ask"
    decisions: list[dict] | None = None


class SymlinkRequest(BaseModel):
    sources: list[str]
    target_dir: str
    link_type: str


@router.get("/list")
def list_dir(
    path: str,
    sort: str = "name",
    order: str = "asc",
    page: int = 0,
    limit: int = Query(100, le=1000),
    store: RootStore = Depends(_get_store),
):
    real = _guard(path, store.roots())
    if not os.path.isdir(real):
        raise HTTPException(status_code=404, detail="not a directory")
    entries = []
    with os.scandir(real) as it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            is_dir = e.is_dir(follow_symlinks=False)
            entries.append({
                "name": e.name,
                "path": e.path,
                "is_dir": is_dir,
                "size": None if is_dir else st.st_size,
                "mtime": st.st_mtime,
            })
    reverse = order == "desc"
    if sort == "size":
        keyfn = lambda x: x["size"] or 0
    elif sort == "mtime":
        keyfn = lambda x: x["mtime"]
    else:
        keyfn = lambda x: x["name"].lower()
    entries.sort(key=keyfn, reverse=reverse)
    # Stable second pass: directories always first, preserving the inner order.
    entries.sort(key=lambda x: 0 if x["is_dir"] else 1)
    total = len(entries)
    start = page * limit
    return {"entries": entries[start:start + limit], "total": total, "page": page, "limit": limit}


@router.get("/read")
def read_file(
    path: str,
    full: int = 0,
    store: RootStore = Depends(_get_store),
):
    real = _guard(path, store.roots())
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="not a file")
    size = os.path.getsize(real)
    limit = TEXT_FULL if full else TEXT_LIMIT
    if size > limit:
        raise HTTPException(status_code=413, detail="file too large for text read")
    with open(real, "rb") as f:
        data = f.read(limit)
    if b"\x00" in data[:8192]:
        raise HTTPException(status_code=415, detail="binary file")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    return {"path": real, "content": text, "size": size}


@router.post("/rename")
def rename(req: RenameRequest, store: RootStore = Depends(_get_store)):
    real = _guard(req.path, store.roots())
    if not os.path.exists(real):
        raise HTTPException(status_code=404, detail="not found")
    new_name = os.path.basename(req.new_name)
    if (not new_name) or new_name in (".", "..") or "/" in req.new_name or "\\" in req.new_name:
        raise HTTPException(status_code=400, detail="invalid name")
    target = os.path.join(os.path.dirname(real), new_name)
    if os.path.exists(target):
        raise HTTPException(status_code=409, detail="target exists")
    os.rename(real, target)
    return {"path": target, "renamed": True}


def _apply_rename(name: str, req: BulkRenameRequest, idx: int) -> str:
    stem, ext = os.path.splitext(name)
    if req.mode == "replace":
        if not req.find:
            return name
        return stem.replace(req.find, req.replace or "", 1) + ext
    if req.mode == "prefix":
        return (req.prefix or "") + name
    if req.mode == "suffix":
        return stem + (req.suffix or "") + ext
    if req.mode == "seq":
        num = req.seq_start + idx
        return str(num).zfill(req.seq_pad) + ext
    return name


@router.post("/bulk-rename")
def bulk_rename(req: BulkRenameRequest, store: RootStore = Depends(_get_store)):
    if req.mode not in ("replace", "prefix", "suffix", "seq"):
        raise HTTPException(status_code=400, detail="invalid mode")
    roots = store.roots()
    items = [_guard(p, roots) for p in req.items]
    plan = []
    for i, real in enumerate(items):
        base = os.path.basename(real)
        new_base = _apply_rename(base, req, i)
        if new_base != base:
            plan.append((real, os.path.join(os.path.dirname(real), new_base)))
    # Phase 1: validate
    targets = {}
    for real, target in plan:
        if os.path.exists(target):
            return {"ok": False, "phase": "validate", "errors": [{"path": real, "error": "target exists"}]}
        if target in targets:
            return {"ok": False, "phase": "validate", "errors": [{"path": real, "error": "collision"}]}
        targets[target] = real
    # Phase 2: best-effort execute
    results, errors = [], []
    for real, target in plan:
        try:
            os.rename(real, target)
            results.append({"from": real, "to": target})
        except OSError as e:
            errors.append({"path": real, "error": str(e)})
    return {"ok": not errors, "phase": "execute", "results": results, "errors": errors}


@router.delete("/trash")
def trash(req: TrashRequest, store: RootStore = Depends(_get_store)):
    roots = store.roots()
    results, errors = [], []
    for p in req.paths:
        try:
            real = _guard(p, roots)
        except HTTPException:
            errors.append({"path": p, "error": "outside whitelist"})
            continue
        try:
            send2trash(real)
            results.append({"path": real, "trashed": True})
        except Exception as e:
            errors.append({"path": real, "error": str(e)})
    return {"results": results, "errors": errors}


@router.post("/open")
def open_item(req: OpenRequest, store: RootStore = Depends(_get_store)):
    real = _guard(req.path, store.roots())
    if not os.path.exists(real):
        raise HTTPException(status_code=404, detail="not found")
    try:
        subprocess.run(["xdg-open", real], check=False, timeout=30, shell=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"opened": True, "path": real}


@router.post("/open-terminal")
def open_terminal(req: OpenRequest, store: RootStore = Depends(_get_store)):
    real = _guard(req.path, store.roots())
    target = real if os.path.isdir(real) else os.path.dirname(real)
    try:
        subprocess.run(
            ["x-terminal-emulator"], cwd=str(target), check=False, timeout=30, shell=False
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"opened": True, "cwd": target}


@router.get("/preview")
def preview(path: str, store: RootStore = Depends(_get_store)):
    real = _guard(path, store.roots())
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="not a file")
    mime, _ = mimetypes.guess_type(real)
    if mime is None:
        mime = "application/octet-stream"
    if not (mime.startswith("image/") or mime.startswith("audio/") or mime == "application/pdf"):
        raise HTTPException(status_code=415, detail="no inline preview for this type")
    return FileResponse(real, media_type=mime)


@router.get("/roots")
def list_roots(store: RootStore = Depends(_get_store)):
    return {"roots": store.roots()}


@router.post("/roots")
def add_root(req: AddRootRequest, store: RootStore = Depends(_get_store)):
    try:
        c = store.add_root(req.path)
    except GuardError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"roots": store.roots(), "added": c}


@router.delete("/roots/{path:path}")
def remove_root(path: str, store: RootStore = Depends(_get_store)):
    c = os.path.realpath(os.path.expanduser(path))
    if c not in store.roots():
        raise HTTPException(status_code=404, detail="root not found")
    store.remove_root(c)
    return {"roots": store.roots(), "removed": c}


def _target_of(source: str, target_dir: str) -> str:
    return os.path.join(target_dir, os.path.basename(source))


def _is_within(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(child), os.path.realpath(parent)]) == os.path.realpath(parent)
    except ValueError:
        return False


def _check_cycle(source: str, target_dir: str) -> None:
    if os.path.isdir(source) and _is_within(target_dir, source):
        raise HTTPException(status_code=400, detail="cannot copy/move a directory into itself")


def _collect_conflicts(sources: list[str], target_dir: str) -> list[dict]:
    conflicts = []
    for s in sources:
        target = _target_of(s, target_dir)
        if os.path.lexists(target):
            conflicts.append({"source": s, "target": target, "exists": True})
    return conflicts


def _unique_target(target: str) -> str:
    base, ext = os.path.splitext(target)
    n = 2
    candidate = f"{base} ({n}){ext}"
    while os.path.lexists(candidate) and n < 1000:
        n += 1
        candidate = f"{base} ({n}){ext}"
    if os.path.lexists(candidate):
        raise HTTPException(status_code=409, detail="too many collisions")
    return candidate


def _execute_copy_move(req: CopyMoveRequest, roots: list[str], move: bool):
    if req.on_conflict not in ("ask", "overwrite", "skip", "rename"):
        raise HTTPException(status_code=400, detail="invalid on_conflict")
    if not req.sources:
        raise HTTPException(status_code=400, detail="no sources")

    # guard + canonicalize + 404 for nonexistent source FIRST, then dedupe
    # (dedupe must run on canonical paths so symlink aliases collapse)
    guarded = []
    for s in req.sources:
        real = _guard(s, roots)
        if not os.path.lexists(real):
            raise HTTPException(status_code=404, detail="not found")
        guarded.append(real)
    sources = list(dict.fromkeys(guarded))

    # basename collision between distinct sources -> 400
    basenames = [os.path.basename(s) for s in sources]
    if len(set(basenames)) < len(basenames):
        raise HTTPException(status_code=400, detail="duplicate basename")

    target_dir = _guard(req.target_dir, roots)
    if os.path.isfile(target_dir):
        raise HTTPException(status_code=400, detail="target_dir is a file")
    os.makedirs(target_dir, exist_ok=True)
    for s in sources:
        _check_cycle(s, target_dir)
        try:
            guard_tree(s, roots)
        except GuardError as e:
            raise HTTPException(status_code=403, detail=str(e))

    conflicts = _collect_conflicts(sources, target_dir)
    non_conflicts = [s for s in sources if not os.path.lexists(_target_of(s, target_dir))]

    # Only pause for the "ask" phase when there ARE conflicts to resolve.
    # A conflict-free copy must execute immediately — otherwise the client
    # shows an empty "Resolve (0)" dialog.
    if req.on_conflict == "ask" and not req.decisions and conflicts:
        return {"phase": "ask", "conflicts": conflicts, "non_conflicts": non_conflicts}

    decisions = {}
    for d in (req.decisions or []):
        key = os.path.realpath(os.path.expanduser(d["source"]))
        decisions[key] = d.get("decision", "skip")

    results, errors = [], []
    for s in sources:
        target = _target_of(s, target_dir)
        try:
            mode = decisions.get(s, req.on_conflict)
            renamed = False
            if os.path.lexists(target):
                if mode == "skip":
                    results.append({"from": s, "to": target, "status": "skipped"})
                    continue
                if mode == "rename":
                    target = _unique_target(target)
                    renamed = True
                elif mode == "ask":
                    errors.append({"path": s, "error": "conflict unresolved (no decision)"})
                    continue
            if os.path.isdir(s):
                if move:
                    shutil.move(s, target)
                    status = "moved"
                else:
                    target_exists = os.path.isdir(target)
                    shutil.copytree(s, target, symlinks=True, dirs_exist_ok=True)
                    status = "merged" if target_exists else "copied"
            else:
                if move:
                    shutil.move(s, target)
                    status = "moved"
                else:
                    shutil.copy2(s, target)
                    status = "copied"
            if renamed:
                status = "renamed"
            results.append({"from": s, "to": target, "status": status})
        except OSError as e:
            errors.append({"path": s, "error": str(e)})
    return {"phase": "execute", "results": results, "errors": errors}


@router.post("/copy")
def copy_items(req: CopyMoveRequest, store: RootStore = Depends(_get_store)):
    return _execute_copy_move(req, store.roots(), move=False)


@router.post("/move")
def move_items(req: CopyMoveRequest, store: RootStore = Depends(_get_store)):
    return _execute_copy_move(req, store.roots(), move=True)


def _make_symlinks(req: SymlinkRequest, roots: list[str]) -> dict:
    if req.link_type not in ("relative", "absolute"):
        raise HTTPException(status_code=400, detail="invalid link_type")
    if not req.sources:
        raise HTTPException(status_code=400, detail="no sources")

    # Guard sources + 404 for nonexistent source first, then dedupe
    guarded = []
    for s in req.sources:
        real = _guard(s, roots)
        if not os.path.lexists(real):
            raise HTTPException(status_code=404, detail="not found")
        guarded.append(real)
    sources = list(dict.fromkeys(guarded))

    # target_dir must already exist (symlink does not create it, unlike copy/move)
    target_dir = _guard(req.target_dir, roots)
    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=404, detail="target_dir does not exist")

    results, errors = [], []
    for s in sources:
        link_path = os.path.join(target_dir, os.path.basename(s))
        try:
            if os.path.lexists(link_path):
                errors.append({"path": s, "error": "link target exists"})
                continue
            if req.link_type == "relative":
                link_target = os.path.relpath(s, target_dir)
            else:
                link_target = s
            os.symlink(link_target, link_path)
            results.append({"from": s, "to": link_path, "status": "linked"})
        except OSError as e:
            errors.append({"path": s, "error": str(e)})
    return {"results": results, "errors": errors}


@router.post("/symlink")
def symlink_items(req: SymlinkRequest, store: RootStore = Depends(_get_store)):
    return _make_symlinks(req, store.roots())
