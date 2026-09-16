import os
from pathlib import Path

from guard import guard_path


def _mk(tmp: Path):
    (tmp / "root" / "sub").mkdir(parents=True)
    (tmp / "root" / "file.txt").write_text("hi")
    (tmp / "outside").mkdir()
    return tmp


def test_happy_path_returns_canonical(tmp_path):
    t = _mk(tmp_path)
    roots = [str(t / "root")]
    p = guard_path(str(t / "root" / "sub"), roots)
    assert p == os.path.realpath(t / "root" / "sub")


def test_traversal_blocked(tmp_path):
    t = _mk(tmp_path)
    roots = [str(t / "root")]
    try:
        guard_path(str(t / "root" / ".." / "outside"), roots)
        assert False, "expected 403"
    except PermissionError:
        pass


def test_absolute_escape_blocked(tmp_path):
    t = _mk(tmp_path)
    roots = [str(t / "root")]
    try:
        guard_path(str(t / "outside"), roots)
        assert False, "expected 403"
    except PermissionError:
        pass


def test_symlink_escape_blocked(tmp_path):
    t = _mk(tmp_path)
    roots = [str(t / "root")]
    (t / "root" / "link").symlink_to(t / "outside")
    try:
        guard_path(str(t / "root" / "link"), roots)
        assert False, "expected 403"
    except PermissionError:
        pass


def test_symlink_inside_resolved(tmp_path):
    t = _mk(tmp_path)
    roots = [str(t / "root")]
    (t / "root" / "link").symlink_to(t / "root" / "sub")
    p = guard_path(str(t / "root" / "link"), roots)
    assert p == os.path.realpath(t / "root" / "sub")
