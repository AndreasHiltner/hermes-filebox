import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))
import pytest
from fastapi.testclient import TestClient
import plugin_api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("hello world")
    (root / "sub" / "b.md").write_text("# hi")
    state = tmp_path / "hermes" / "state" / "filebox"
    state.mkdir(parents=True)
    (state / "roots.json").write_text(json.dumps({"roots": [str(root)]}))
    plugin_api.reset_store()
    return TestClient(plugin_api.create_app())


def test_copy_happy_path(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/copy", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "sub"), "on_conflict": "overwrite"})
    assert r.status_code == 200
    assert (root / "sub" / "a.txt").exists()


def test_move_happy_path(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/move", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "sub"), "on_conflict": "overwrite"})
    assert r.status_code == 200
    assert not (root / "a.txt").exists()
    assert (root / "sub" / "a.txt").exists()


def test_copy_nonexistent_target_dir_created(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/copy", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "newdir"), "on_conflict": "overwrite"})
    assert r.status_code == 200
    assert (root / "newdir" / "a.txt").exists()


def test_move_into_own_subtree_400(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/move", json={"sources": [str(root / "sub")], "target_dir": str(root / "sub" / "deeper"), "on_conflict": "overwrite"})
    assert r.status_code == 400


def test_copy_outside_whitelist_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("x")
    # source outside
    r = client.post("/copy", json={"sources": [str(outside / "x.txt")], "target_dir": str(root / "sub"), "on_conflict": "overwrite"})
    assert r.status_code == 403
    # target outside
    r = client.post("/copy", json={"sources": [str(root / "a.txt")], "target_dir": str(outside), "on_conflict": "overwrite"})
    assert r.status_code == 403


def test_copy_ask_phase1(client, tmp_path):
    root = tmp_path / "root"
    # conflict: a.txt copied into root itself already exists at root/a.txt
    r = client.post("/copy", json={"sources": [str(root / "a.txt")], "target_dir": str(root), "on_conflict": "ask"})
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "ask"
    assert "conflicts" in body
    assert "non_conflicts" in body


def test_copy_directory_recursive(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/copy", json={"sources": [str(root / "sub")], "target_dir": str(root / "sub2"), "on_conflict": "overwrite"})
    assert r.status_code == 200
    assert (root / "sub2" / "sub" / "b.md").exists()
