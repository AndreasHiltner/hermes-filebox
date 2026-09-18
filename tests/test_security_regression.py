"""Adversarial security regression tests against the finished filebox plugin.

These tests probe REAL behavior (not just re-asserting happy paths):
traversal, symlink escape/inside, root-add bypass, subprocess guard via
monkeypatch, and "nothing happened" side-effect checks.

A test that fails here documents a real security gap; it is NOT patched here
(per QA policy: report, don't fix).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))

import pytest
from fastapi.testclient import TestClient

import guard
import plugin_api
from guard import guard_path, GuardError


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("hello world")
    (root / "sub" / "b.md").write_text("# hi")
    (root / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP SECRET")
    state = tmp_path / "hermes" / "state" / "filebox"
    state.mkdir(parents=True)
    (state / "roots.json").write_text(json.dumps({"roots": [str(root)]}))
    plugin_api.reset_store()
    return TestClient(plugin_api.create_app())


# ---------------------------------------------------------------------------
# 1. Path traversal
# ---------------------------------------------------------------------------

def test_traversal_dotdot(client, tmp_path):
    root = tmp_path / "root"
    r = client.get("/list", params={"path": str(root / ".." / ".." / "etc")})
    assert r.status_code == 403


def test_traversal_percent_encoded(client, tmp_path):
    # %2f decodes to '/', %2e to '.'; must still be blocked post-decode.
    # Send the RAW percent-encoded URL (not via params=, which re-encodes '%').
    from urllib.parse import quote
    root = tmp_path / "root"
    encoded = quote(str(root) + "/../../etc", safe="")
    r = client.get(f"/list?path={encoded}")
    assert r.status_code == 403


def test_traversal_absolute_outside(client):
    r = client.get("/list", params={"path": "/etc/passwd"})
    assert r.status_code == 403


def test_traversal_nothing_leaked(client, tmp_path):
    # Traversal must not expose any entry content, not even a partial listing.
    root = tmp_path / "root"
    r = client.get("/read", params={"path": str(root / ".." / "outside" / "secret.txt")})
    assert r.status_code == 403
    assert "TOP SECRET" not in r.text


# ---------------------------------------------------------------------------
# 2/3. Symlink escape / inside
# ---------------------------------------------------------------------------

def test_symlink_escape_list_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "link").symlink_to(outside)
    r = client.get("/list", params={"path": str(root / "link")})
    assert r.status_code == 403


def test_symlink_escape_read_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "linkfile").symlink_to(outside / "secret.txt")
    r = client.get("/read", params={"path": str(root / "linkfile")})
    assert r.status_code == 403
    assert "TOP SECRET" not in r.text


def test_symlink_inside_list_200(client, tmp_path):
    root = tmp_path / "root"
    (root / "link").symlink_to(root / "sub")
    r = client.get("/list", params={"path": str(root / "link")})
    assert r.status_code == 200
    names = [e["name"] for e in r.json()["entries"]]
    assert "b.md" in names


# ---------------------------------------------------------------------------
# 4. Root-add bypass
# ---------------------------------------------------------------------------

def test_add_root_slash_403(client):
    r = client.post("/roots", json={"path": "/"})
    assert r.status_code == 403


def test_add_root_tilde_home_403(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    r = client.post("/roots", json={"path": "~"})
    assert r.status_code == 403


def test_add_root_nonexistent_403(client, tmp_path):
    r = client.post("/roots", json={"path": str(tmp_path / "does-not-exist")})
    assert r.status_code == 403


def test_add_root_file_403(client, tmp_path):
    f = tmp_path / "afile.txt"
    f.write_text("x")
    r = client.post("/roots", json={"path": str(f)})
    assert r.status_code == 403


def test_add_root_symlink_to_home_403(client, tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    link = tmp_path / "linkhome"
    link.symlink_to(home)
    r = client.post("/roots", json={"path": str(link)})
    assert r.status_code == 403


def test_add_root_parent_of_home_403(client, tmp_path, monkeypatch):
    # Adding an ancestor of home (e.g. the parent dir containing home) subsumes
    # home and defeats the "never expose home" invariant. Must be rejected.
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    parent = home.parent  # tmp_path itself
    r = client.post("/roots", json={"path": str(parent)})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 5. /read content-type & size limits
# ---------------------------------------------------------------------------

def test_read_binary_415(client, tmp_path):
    root = tmp_path / "root"
    (root / "bin.dat").write_bytes(b"\x00\x01\x02")
    r = client.get("/read", params={"path": str(root / "bin.dat")})
    assert r.status_code == 415


def test_read_binary_null_deep_415(client, tmp_path):
    # Null byte past byte 0 but within the first 8192 must still be caught.
    root = tmp_path / "root"
    (root / "bin.dat").write_bytes(b"A" * 4000 + b"\x00" + b"B" * 100)
    r = client.get("/read", params={"path": str(root / "bin.dat")})
    assert r.status_code == 415


def test_read_large_without_full_413(client, tmp_path):
    root = tmp_path / "root"
    (root / "big.txt").write_text("x" * (70 * 1024))
    r = client.get("/read", params={"path": str(root / "big.txt")})
    assert r.status_code == 413


def test_read_directory_404(client, tmp_path):
    root = tmp_path / "root"
    r = client.get("/read", params={"path": str(root / "sub")})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 6. /preview whitelist & type
# ---------------------------------------------------------------------------

def test_preview_text_415(client, tmp_path):
    root = tmp_path / "root"
    (root / "note.txt").write_text("hello")
    r = client.get("/preview", params={"path": str(root / "note.txt")})
    assert r.status_code == 415


def test_preview_outside_whitelist_403(client, tmp_path):
    outside = tmp_path / "outside"
    (outside / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    r = client.get("/preview", params={"path": str(outside / "x.png")})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 7. /rename name escape
# ---------------------------------------------------------------------------

def test_rename_traversal_name_400(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/rename", json={"path": str(root / "a.txt"), "new_name": "../evil"})
    assert r.status_code == 400


def test_rename_deep_traversal_name_400(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/rename", json={"path": str(root / "a.txt"), "new_name": "evil/../../x"})
    assert r.status_code == 400


def test_rename_traversal_nothing_happened(client, tmp_path):
    # On 400, the source file must remain intact and no file created outside.
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    r = client.post("/rename", json={"path": str(root / "a.txt"), "new_name": "../evil"})
    assert r.status_code == 400
    assert (root / "a.txt").exists()
    assert not (outside / "evil").exists()


# ---------------------------------------------------------------------------
# 8. /open and /open-terminal subprocess guard (monkeypatch)
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_run(monkeypatch):
    calls = []
    def _fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return None
    monkeypatch.setattr(plugin_api.subprocess, "run", _fake_run)
    return calls


def test_open_outside_whitelist_no_subprocess(client, tmp_path, fake_run):
    outside = tmp_path / "outside"
    r = client.post("/open", json={"path": str(outside / "secret.txt")})
    assert r.status_code == 403
    assert fake_run == []  # subprocess.run MUST NOT be called


def test_open_terminal_outside_whitelist_no_subprocess(client, tmp_path, fake_run):
    outside = tmp_path / "outside"
    r = client.post("/open-terminal", json={"path": str(outside)})
    assert r.status_code == 403
    assert fake_run == []  # x-terminal-emulator MUST NOT be launched


def test_open_inside_uses_shell_false_and_list(client, tmp_path, fake_run):
    root = tmp_path / "root"
    r = client.post("/open", json={"path": str(root / "a.txt")})
    assert r.status_code == 200
    assert len(fake_run) == 1
    args, kwargs = fake_run[0]
    assert kwargs["shell"] is False
    assert isinstance(args[0], list)          # arg list, not a shell string
    assert args[0][0] == "xdg-open"


def test_open_terminal_inside_uses_shell_false_and_cwd(client, tmp_path, fake_run):
    root = tmp_path / "root"
    r = client.post("/open-terminal", json={"path": str(root / "sub")})
    assert r.status_code == 200
    assert len(fake_run) == 1
    args, kwargs = fake_run[0]
    assert kwargs["shell"] is False
    assert args[0][0] == "x-terminal-emulator"
    assert kwargs["cwd"] == str(root / "sub")


# ---------------------------------------------------------------------------
# 8b. cross-platform opener dispatch (_open_command / _terminal_command)
# ---------------------------------------------------------------------------

def _set_platform(monkeypatch, value):
    monkeypatch.setattr(plugin_api._sys, "platform", value)


def test_open_command_darwin(monkeypatch):
    _set_platform(monkeypatch, "darwin")
    assert plugin_api._open_command("/tmp/x") == ["open", "/tmp/x"]


def test_open_command_windows(monkeypatch):
    _set_platform(monkeypatch, "win32")
    assert plugin_api._open_command("C:\\x.txt") == ["cmd", "/c", "start", "", "C:\\x.txt"]


def test_open_command_linux(monkeypatch):
    _set_platform(monkeypatch, "linux")
    assert plugin_api._open_command("/tmp/x") == ["xdg-open", "/tmp/x"]


def test_terminal_command_darwin(monkeypatch):
    _set_platform(monkeypatch, "darwin")
    assert plugin_api._terminal_command("/tmp/x") == ["open", "-a", "Terminal", "/tmp/x"]


def test_terminal_command_windows_with_wt(monkeypatch):
    _set_platform(monkeypatch, "win32")
    monkeypatch.setattr(plugin_api.shutil, "which", lambda _: "C:\\wt.exe")
    assert plugin_api._terminal_command("C:\\x") == ["C:\\wt.exe", "-d", "C:\\x"]


def test_terminal_command_windows_without_wt(monkeypatch):
    _set_platform(monkeypatch, "win32")
    monkeypatch.setattr(plugin_api.shutil, "which", lambda _: None)
    assert plugin_api._terminal_command("C:\\x") == [
        "cmd", "/c", "start", "/D", "C:\\x", "cmd"
    ]


def test_terminal_command_windows_without_wt_no_interpolation(monkeypatch):
    """Regression: a hostile directory name must never be spliced into a
    command string that cmd re-parses (the old `/k cd /d "<dir>"` form)."""
    _set_platform(monkeypatch, "win32")
    monkeypatch.setattr(plugin_api.shutil, "which", lambda _: None)
    hostile = 'C:\\x" & calc & "'
    argv = plugin_api._terminal_command(hostile)
    # the directory appears verbatim as exactly one argv element ...
    assert argv.count(hostile) == 1
    # ... and no other element contains it or any `cd` / `/k` re-parse hook
    others = [a for a in argv if a != hostile]
    assert all(hostile not in a and "calc" not in a for a in others)
    assert "/k" not in [a.lower() for a in argv]
    assert not any(a.lower().startswith("cd ") for a in argv)


def test_terminal_command_linux(monkeypatch):
    _set_platform(monkeypatch, "linux")
    assert plugin_api._terminal_command("/tmp/x") == ["x-terminal-emulator"]


# ---------------------------------------------------------------------------
# 9. /trash outside whitelist -> errors[], no crash, nothing trashed
# ---------------------------------------------------------------------------

def test_trash_outside_whitelist_errors_no_crash(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    r = client.request("DELETE", "/trash", json={
        "paths": [str(root / "a.txt"), str(outside / "secret.txt")]
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["errors"]) == 1
    assert body["errors"][0]["error"] == "outside whitelist"
    assert (outside / "secret.txt").exists()  # NOT trashed


# ---------------------------------------------------------------------------
# 10. commonpath ValueError / degenerate -> GuardError, never 500
# ---------------------------------------------------------------------------

def test_guard_degenerate_roots_raises_guard_error():
    with pytest.raises(GuardError):
        guard_path("/etc/passwd", [])


def test_guard_never_valueerror():
    # Any adversarial input must surface as GuardError (PermissionError), never
    # a raw ValueError/OSError that would bubble up as a 500.
    inputs = [
        ("/etc/passwd", ["/home/x"]),
        ("relative/path", ["/abs/root"]),
        ("/", ["/home/x"]),
    ]
    for path, roots in inputs:
        with pytest.raises(GuardError):
            guard_path(path, roots)


def test_guard_is_permission_error_subclass():
    assert issubclass(GuardError, PermissionError)


# ---------------------------------------------------------------------------
# 11. Slice 3 — nested-symlink exfiltration + phase-2 re-guard (copy/move)
# ---------------------------------------------------------------------------

def test_nested_symlink_outside_copy_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (outside / "secret.txt").write_text("TOP SECRET")
    (root / "srcdir").mkdir()
    (root / "srcdir" / "good.txt").write_text("good")
    (root / "srcdir" / "link").symlink_to(outside / "secret.txt")
    r = client.post("/copy", json={"sources": [str(root / "srcdir")], "target_dir": str(root / "sub"), "on_conflict": "overwrite"})
    assert r.status_code == 403
    assert not (root / "sub" / "srcdir").exists()


def test_nested_symlink_outside_move_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "srcdir").mkdir()
    (root / "srcdir" / "link").symlink_to(outside / "secret.txt")
    r = client.post("/move", json={"sources": [str(root / "srcdir")], "target_dir": str(root / "sub"), "on_conflict": "overwrite"})
    assert r.status_code == 403
    # source dir untouched (move must not partially consume it)
    assert (root / "srcdir").exists()


def test_nested_symlink_inside_whitelist_preserved(client, tmp_path):
    # A symlink inside the tree pointing to ANOTHER whitelist path must be
    # preserved as a link (copytree symlinks=True), not dereferenced or rejected.
    root = tmp_path / "root"
    (root / "target.txt").write_text("target")
    (root / "srcdir").mkdir()
    (root / "srcdir" / "link").symlink_to(root / "target.txt")
    r = client.post("/copy", json={"sources": [str(root / "srcdir")], "target_dir": str(root / "sub"), "on_conflict": "overwrite"})
    assert r.status_code == 200
    copied_link = root / "sub" / "srcdir" / "link"
    assert copied_link.is_symlink()


def test_phase2_reguard_swap_outside_403(client, tmp_path):
    # Phase-2 (post-dialog) request with a source that now resolves outside the
    # whitelist must be re-guarded and rejected — no server-side trust carried
    # across the dialog pause (stateless protocol).
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (outside / "x.txt").write_text("x")
    r = client.post("/copy", json={
        "sources": [str(outside / "x.txt")],
        "target_dir": str(root),
        "on_conflict": "ask",
        "decisions": [{"source": str(outside / "x.txt"), "decision": "skip"}],
    })
    assert r.status_code == 403
    assert not (root / "x.txt").exists()
