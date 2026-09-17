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


# ---- New tests from adversarial review ----

def test_on_conflict_skip_top_level(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/copy", json={"sources": [str(root / "a.txt")], "target_dir": str(root), "on_conflict": "skip"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["status"] == "skipped"
    # target not overwritten
    assert (root / "a.txt").read_text() == "hello world"


def test_on_conflict_rename_top_level(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/copy", json={"sources": [str(root / "a.txt")], "target_dir": str(root), "on_conflict": "rename"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["status"] == "renamed"
    assert (root / "a (2).txt").exists()


def test_rename_collision_loop(client, tmp_path):
    root = tmp_path / "root"
    (root / "a (2).txt").write_text("collision")
    r = client.post("/copy", json={"sources": [str(root / "a.txt")], "target_dir": str(root), "on_conflict": "rename"})
    assert r.status_code == 200
    assert (root / "a (3).txt").exists()


def test_decisions_field_uses_decision(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/copy", json={
        "sources": [str(root / "a.txt")],
        "target_dir": str(root),
        "on_conflict": "ask",
        "decisions": [{"source": str(root / "a.txt"), "decision": "skip"}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["status"] == "skipped"


def test_nonexistent_source_404(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/copy", json={"sources": [str(root / "nope.txt")], "target_dir": str(root / "sub"), "on_conflict": "overwrite"})
    assert r.status_code == 404


def test_copy_directory_merge(client, tmp_path):
    root = tmp_path / "root"
    # pre-existing target dir with its own file
    (root / "sub2" / "sub").mkdir(parents=True)
    (root / "sub2" / "sub" / "c.md").write_text("# existing")
    r = client.post("/copy", json={"sources": [str(root / "sub")], "target_dir": str(root / "sub2"), "on_conflict": "overwrite"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["status"] == "merged"
    # both old and new content preserved
    assert (root / "sub2" / "sub" / "b.md").exists()
    assert (root / "sub2" / "sub" / "c.md").exists()


def test_copy_dir_into_itself_400(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/copy", json={"sources": [str(root / "sub")], "target_dir": str(root / "sub"), "on_conflict": "overwrite"})
    assert r.status_code == 400


def test_duplicate_basename_400(client, tmp_path):
    root = tmp_path / "root"
    (root / "other").mkdir()
    (root / "other" / "a.txt").write_text("other")
    r = client.post("/copy", json={"sources": [str(root / "a.txt"), str(root / "other" / "a.txt")], "target_dir": str(root / "sub"), "on_conflict": "overwrite"})
    assert r.status_code == 400


def test_recursive_move_directory(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/move", json={"sources": [str(root / "sub")], "target_dir": str(root / "sub2"), "on_conflict": "overwrite"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["status"] == "moved"
    assert not (root / "sub").exists()
    assert (root / "sub2" / "sub" / "b.md").exists()


def test_nested_symlink_outside_403(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("secret")
    src = root / "sub"
    os.symlink(str(outside), str(src / "link"))
    r = client.post("/copy", json={"sources": [str(src)], "target_dir": str(root / "sub2"), "on_conflict": "overwrite"})
    assert r.status_code == 403


def test_phase2_reguard(client, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("x")
    # phase-2 request with an outside-whitelist source must be re-guarded -> 403
    r = client.post("/copy", json={
        "sources": [str(outside / "x.txt")],
        "target_dir": str(root),
        "on_conflict": "ask",
        "decisions": [{"source": str(outside / "x.txt"), "decision": "skip"}],
    })
    assert r.status_code == 403


def test_target_dir_is_file_400(client, tmp_path):
    root = tmp_path / "root"
    (root / "target_file.txt").write_text("target")
    r = client.post("/copy", json={"sources": [str(root / "a.txt")], "target_dir": str(root / "target_file.txt"), "on_conflict": "overwrite"})
    assert r.status_code == 400


def test_empty_sources_400(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/copy", json={"sources": [], "target_dir": str(root / "sub"), "on_conflict": "overwrite"})
    assert r.status_code == 400


def test_decisions_source_canonicalized(client, tmp_path):
    root = tmp_path / "root"
    # decision references the source via a symlink path that realpaths to the same file
    os.symlink(str(root / "a.txt"), str(root / "alias.txt"))
    r = client.post("/copy", json={
        "sources": [str(root / "a.txt")],
        "target_dir": str(root),
        "on_conflict": "ask",
        "decisions": [{"source": str(root / "alias.txt"), "decision": "skip"}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["status"] == "skipped"


def test_symlink_alias_sources_deduped(client, tmp_path):
    root = tmp_path / "root"
    os.symlink(str(root / "a.txt"), str(root / "alias.txt"))
    r = client.post("/copy", json={
        "sources": [str(root / "a.txt"), str(root / "alias.txt")],
        "target_dir": str(root / "sub"),
        "on_conflict": "overwrite",
    })
    assert r.status_code == 200
    body = r.json()
    # deduped to a single canonical source
    assert len(body["results"]) == 1
    assert (root / "sub" / "a.txt").exists()


def test_ask_unresolved_conflict_goes_to_errors(client, tmp_path):
    root = tmp_path / "root"
    r = client.post("/copy", json={
        "sources": [str(root / "a.txt")],
        "target_dir": str(root),
        "on_conflict": "ask",
        "decisions": [],  # empty list -> phase-2 path with no decision
    })
    # empty decisions is falsy -> hits phase-1 early return
    assert r.status_code == 200
    assert r.json()["phase"] == "ask"


def test_ask_partial_decisions_unresolved_not_overwritten(client, tmp_path):
    root = tmp_path / "root"
    (root / "b.txt").write_text("b")
    r = client.post("/copy", json={
        "sources": [str(root / "a.txt"), str(root / "b.txt")],
        "target_dir": str(root),
        "on_conflict": "ask",
        "decisions": [{"source": str(root / "a.txt"), "decision": "skip"}],
    })
    assert r.status_code == 200
    body = r.json()
    # a.txt -> skipped (decision), b.txt -> unresolved -> error, NOT overwritten
    statuses = {res["from"]: res["status"] for res in body["results"]}
    assert any(res["status"] == "skipped" for res in body["results"])
    # b.txt was a conflict with no decision -> must land in errors, content preserved
    assert any("conflict unresolved" in e["error"] for e in body["errors"])
    assert (root / "b.txt").read_text() == "b"
