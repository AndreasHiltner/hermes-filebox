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
    root.mkdir()
    (root / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    (root / "note.txt").write_text("hello")
    state = tmp_path / "hermes" / "state" / "filebox"
    state.mkdir(parents=True)
    (state / "roots.json").write_text(json.dumps({"roots": [str(root)]}))
    plugin_api.reset_store()
    return TestClient(plugin_api.create_app())


def test_preview_image(client, tmp_path):
    root = tmp_path / "root"
    r = client.get("/preview", params={"path": str(root / "img.png")})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")


def test_preview_text_415(client, tmp_path):
    root = tmp_path / "root"
    r = client.get("/preview", params={"path": str(root / "note.txt")})
    assert r.status_code == 415


def test_roots_list(client, tmp_path):
    r = client.get("/roots")
    assert r.status_code == 200
    assert len(r.json()["roots"]) >= 1


def test_add_root(client, tmp_path):
    target = tmp_path / "newroot"
    target.mkdir()
    r = client.post("/roots", json={"path": str(target)})
    assert r.status_code == 200
    assert str(target) in r.json()["roots"]


def test_add_root_rejects_home(client, tmp_path):
    r = client.post("/roots", json={"path": str(Path.home())})
    assert r.status_code == 403


def test_remove_root(client, tmp_path):
    target = tmp_path / "newroot"
    target.mkdir()
    client.post("/roots", json={"path": str(target)})
    r = client.request("DELETE", f"/roots/{target}")
    assert r.status_code == 200
    assert str(target) not in r.json()["roots"]
