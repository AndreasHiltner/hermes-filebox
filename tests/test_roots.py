import json
import pytest
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


@pytest.mark.parametrize("name", [".ssh", ".hermes", ".aws", ".gnupg", ".config"])
def test_add_root_rejects_sensitive_home_dotdirs(tmp_path, monkeypatch, name):
    home = tmp_path / "home"
    (home / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    st = RootStore(tmp_path / "state")
    with pytest.raises(GuardError, match="sensitive"):
        st.add_root(str(home / name))
    # a same-named dir NOT directly under home is still fine
    other = tmp_path / "proj" / name
    other.mkdir(parents=True)
    assert st.add_root(str(other)) == str(other.resolve())


def test_add_root_rejects_nonexistent(tmp_path):
    st = RootStore(state_dir=tmp_path / "state")
    try:
        st.add_root(str(tmp_path / "nope"))
        assert False
    except GuardError:
        pass
