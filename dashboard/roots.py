"""Root whitelist persistence.

Runtime-added roots persist in state/filebox/roots.json, merged over the
built-in DEFAULT_ROOT_CANDIDATES. Every root is canonicalized on add.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from guard import GuardError, _canon

DEFAULT_ROOT_CANDIDATES = [
    "~/Downloads",
    "~/Documents",
    "~/OneDrive/Documents/ObsidianMD",
    "~/Video",
    "~/Pictures",
]


class RootStore:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._json = self.state_dir / "roots.json"
        self._roots = self._load()

    def _load(self) -> list[str]:
        roots: list[str] = []
        if self._json.exists():
            data = json.loads(self._json.read_text())
            roots.extend(data.get("roots", []))
        for cand in DEFAULT_ROOT_CANDIDATES:
            p = Path(os.path.expanduser(cand))
            if p.exists() and str(p) not in roots:
                roots.append(str(p))
        seen, out = set(), []
        for r in roots:
            c = _canon(r)
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def roots(self) -> list[str]:
        return list(self._roots)

    def add_root(self, path: str) -> str:
        c = _canon(path)
        home = _canon("~")
        # Reject "/", home itself, and any ancestor of home: an ancestor root
        # would subsume the entire home directory and defeat the whitelist.
        try:
            if c == "/" or os.path.commonpath([c, home]) == c:
                raise GuardError("refusing to add '/' or a parent of home as root")
        except ValueError:
            pass
        if not os.path.isdir(c):
            raise GuardError("root must be an existing directory")
        if c not in self._roots:
            self._roots.append(c)
            self._persist()
        return c

    def remove_root(self, path: str) -> None:
        c = _canon(path)
        if c in self._roots:
            self._roots.remove(c)
            self._persist()

    def _persist(self) -> None:
        self._json.write_text(json.dumps({"roots": self._roots}, indent=2))
