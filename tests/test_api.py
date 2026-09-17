import json
import os
import sys
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


def test_list(client, tmp_path):
    root = tmp_path / "root"
    r = client.get("/list", params={"path": str(root)})
    assert r.status_code == 200
    names = [e["name"] for e in r.json()["entries"]]
    assert "a.txt" in names and "sub" in names


def test_list_dirs_first(client, tmp_path):
    root = tmp_path / "root"
    (root / "z-dir").mkdir()
    (root / "a-dir").mkdir()
    (root / "m.txt").write_text("x")
    (root / "b.txt").write_text("x")
    r = client.get("/list", params={"path": str(root), "sort": "name", "order": "asc"})
    entries = r.json()["entries"]
    names = [e["name"] for e in entries]
    # Directories first (alphabetical), then files (alphabetical).
    assert names == ["a-dir", "sub", "z-dir", "a.txt", "b.txt", "m.txt"]


def test_list_outside_whitelist_403(client, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    r = client.get("/list", params={"path": str(outside)})
    assert r.status_code == 403


def test_list_traversal_403(client, tmp_path):
    root = tmp_path / "root"
    r = client.get("/list", params={"path": str(root / "..")})
    assert r.status_code == 403


def test_read_text(client, tmp_path):
    root = tmp_path / "root"
    r = client.get("/read", params={"path": str(root / "a.txt")})
    assert r.status_code == 200
    assert r.json()["content"] == "hello world"


def test_read_binary_415(client, tmp_path):
    root = tmp_path / "root"
    (root / "bin.dat").write_bytes(b"\x00\x01\x02")
    r = client.get("/read", params={"path": str(root / "bin.dat")})
    assert r.status_code == 415


def test_rename_happy(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/rename", json={"path": str(root / "a.txt"), "new_name": "c.txt"})
    assert r.status_code == 200
    assert (root / "c.txt").exists()


def test_rename_traversal_name_400(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/rename", json={"path": str(root / "a.txt"), "new_name": "../evil"})
    assert r.status_code == 400


def test_bulk_rename_two_phase_collision(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/bulk-rename", json={
        "items": [str(root / "a.txt"), str(root / "sub" / "b.md")],
        "mode": "seq", "seq_start": 1, "seq_pad": 0,
    })
    # a.txt -> 1.txt, b.md -> 2.md; no collision
    assert r.json()["phase"] == "execute"
    assert r.json()["ok"] is True


def test_bulk_rename_collision_detected(client, tmp_path):
    root = tmp_path / "root"
    (root / "c.txt").write_text("x")
    r = client.post("/bulk-rename", json={
        "items": [str(root / "a.txt"), str(root / "c.txt")],
        "mode": "replace", "find": "a", "replace": "c",
    })
    assert r.json()["phase"] == "validate"
    assert r.json()["ok"] is False


def test_trash(client, tmp_path):
    root = tmp_path / "root"
    r = client.request("DELETE", "/trash", json={"paths": [str(root / "a.txt")]})
    assert r.status_code == 200
    assert r.json()["results"][0]["trashed"] is True
