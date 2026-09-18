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
    state = tmp_path / "hermes" / "plugin-data" / "filebox"
    state.mkdir(parents=True)
    (state / "roots.json").write_text(json.dumps({"roots": [str(root)]}))
    plugin_api.reset_store()
    return TestClient(plugin_api.create_app())


def test_symlink_relative_happy_path(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/symlink", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "sub"), "link_type": "relative"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["status"] == "linked"
    link = root / "sub" / "a.txt"
    assert link.is_symlink()
    assert os.readlink(link) == os.path.relpath(root / "a.txt", root / "sub")


def test_symlink_absolute_happy_path(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/symlink", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "sub"), "link_type": "absolute"})
    assert r.status_code == 200
    link = root / "sub" / "a.txt"
    assert link.is_symlink()
    assert os.readlink(link) == str(root / "a.txt")


def test_symlink_directory_source(client, tmp_path):
    root = tmp_path / "root"
    (root / "dirsrc").mkdir()
    r = client.post("/symlink", json={"sources": [str(root / "dirsrc")], "target_dir": str(root / "sub"), "link_type": "relative"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["status"] == "linked"
    assert (root / "sub" / "dirsrc").is_symlink()


def test_symlink_name_conflict_error(client, tmp_path):
    root = tmp_path / "root"
    # pre-existing a.txt at target -> conflict -> error, no link created
    (root / "sub" / "a.txt").write_text("preexisting")
    r = client.post("/symlink", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "sub"), "link_type": "relative"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"] == []
    assert any("link target exists" in e["error"] for e in body["errors"])
    assert (root / "sub" / "a.txt").read_text() == "preexisting"


def test_symlink_source_outside_whitelist_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("x")
    r = client.post("/symlink", json={"sources": [str(outside / "x.txt")], "target_dir": str(root / "sub"), "link_type": "relative"})
    assert r.status_code == 403


def test_symlink_target_outside_whitelist_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    r = client.post("/symlink", json={"sources": [str(root / "a.txt")], "target_dir": str(outside), "link_type": "relative"})
    assert r.status_code == 403


def test_symlink_invalid_link_type_400(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/symlink", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "sub"), "link_type": "bogus"})
    assert r.status_code == 400


def test_symlink_nonexistent_source_404(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/symlink", json={"sources": [str(root / "nope.txt")], "target_dir": str(root / "sub"), "link_type": "relative"})
    assert r.status_code == 404


def test_symlink_nonexistent_target_dir_404(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/symlink", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "missing"), "link_type": "relative"})
    assert r.status_code == 404


def test_symlink_source_is_symlink_outside_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("secret")
    os.symlink(str(outside), str(root / "link.txt"))
    # guard_path realpaths -> outside whitelist -> 403
    r = client.post("/symlink", json={"sources": [str(root / "link.txt")], "target_dir": str(root / "sub"), "link_type": "relative"})
    assert r.status_code == 403


def test_symlink_target_dir_symlink_outside_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), str(root / "sub_link"))
    # target_dir is a symlink -> outside whitelist -> guard realpaths -> 403
    r = client.post("/symlink", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "sub_link"), "link_type": "relative"})
    assert r.status_code == 403


def test_symlink_target_dir_symlink_inside_lands_at_canonical(client, tmp_path):
    root = tmp_path / "root"
    (root / "real_sub").mkdir()
    os.symlink(str(root / "real_sub"), str(root / "sub_alias"))
    # target_dir is a symlink pointing INSIDE whitelist -> link must land at canonical dir
    r = client.post("/symlink", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "sub_alias"), "link_type": "absolute"})
    assert r.status_code == 200
    # link created at the real target directory (canonical), not through the symlink alias
    assert (root / "real_sub" / "a.txt").is_symlink()


def test_symlink_link_on_link_chain_outside_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("secret")
    # chain: root/chain.txt -> root/link.txt -> outside
    os.symlink(str(outside), str(root / "link.txt"))
    os.symlink(str(root / "link.txt"), str(root / "chain.txt"))
    r = client.post("/symlink", json={"sources": [str(root / "chain.txt")], "target_dir": str(root / "sub"), "link_type": "relative"})
    assert r.status_code == 403


def test_symlink_duplicate_sources_deduped(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/symlink", json={"sources": [str(root / "a.txt"), str(root / "a.txt")], "target_dir": str(root / "sub"), "link_type": "relative"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["results"]) == 1
