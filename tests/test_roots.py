import json
from pathlib import Path

from guard import GuardError
from roots import RootStore


def test_default_roots_filtered_to_existing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "Downloads").mkdir()
    monkeypatch.setenv("HOME", str(home))
    st = RootStore(state_dir=tmp_path / "state")
    assert str(home / "Downloads") in st.roots()


def test_add_root_persists(tmp_path):
    st = RootStore(state_dir=tmp_path / "state")
    target = tmp_path / "added"
    target.mkdir()
    st.add_root(str(target))
    assert str(target) in st.roots()
    raw = json.loads((tmp_path / "state" / "roots.json").read_text())
    assert str(target) in raw["roots"]


def test_add_root_rejects_slash(tmp_path):
    st = RootStore(state_dir=tmp_path / "state")
    try:
        st.add_root("/")
        assert False
    except GuardError:
        pass


def test_add_root_rejects_home(tmp_path):
    st = RootStore(state_dir=tmp_path / "state")
    try:
        st.add_root(str(tmp_path / "home"))
        assert False
    except GuardError:
        pass


def test_add_root_rejects_nonexistent(tmp_path):
    st = RootStore(state_dir=tmp_path / "state")
    try:
        st.add_root(str(tmp_path / "nope"))
        assert False
    except GuardError:
        pass
