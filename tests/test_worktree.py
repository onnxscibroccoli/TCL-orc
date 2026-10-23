import pytest

from tcl.orchestrator import IllegalTransition, TaskState as S
from tcl.worktree import WorktreeConflict, WorktreeError, WorktreeManager


def test_create_list_remove(git_repo):
    m = WorktreeManager(git_repo)
    rec = m.create("feat/a", claims=["ledger/x.py"])
    assert rec["port"] == 8000
    assert (git_repo / ".worktrees/feat_a/README").exists()
    assert (git_repo / ".worktrees/feat_a/.env.local").read_text() == "PORT=8000\n"
    assert m.tasks()["feat/a"].state is S.ALLOCATED
    assert str(git_repo / ".worktrees/feat_a") in m.git_worktrees()
    assert m.create("feat/b")["port"] == 8001
    with pytest.raises(WorktreeConflict):
        m.remove("feat/a")  # active task
    for s in (S.EXECUTING, S.VERIFYING, S.MERGED):
        m.transition("feat/a", s)
    m.remove("feat/a")
    assert [r["branch"] for r in m.list()] == ["feat/b"]
    assert m.create("feat/c")["port"] == 8000  # port reused


def test_conflict_guard_branch(git_repo):
    m = WorktreeManager(git_repo)
    m.create("feat/a")
    with pytest.raises(WorktreeConflict):
        m.create("feat/a")


def test_conflict_guard_claims(git_repo):
    m = WorktreeManager(git_repo)
    m.create("a", claims=["f1", "f2"])
    with pytest.raises(WorktreeConflict):
        m.create("b", claims=["f2"])
    m.transition("a", S.FAILED, error="x")
    m.create("b", claims=["f2"])  # released once terminal


def test_illegal_transition_persisted_state_unchanged(git_repo):
    m = WorktreeManager(git_repo)
    m.create("a")
    with pytest.raises(IllegalTransition):
        m.transition("a", S.MERGED)
    assert m.tasks()["a"].state is S.ALLOCATED


def test_invalid_branch(git_repo):
    with pytest.raises(WorktreeError):
        WorktreeManager(git_repo).create("../evil")


def test_force_remove(git_repo):
    m = WorktreeManager(git_repo)
    m.create("a")
    m.remove("a", force=True)
    assert m.list() == []


def test_harness_shim(git_repo):
    import json
    import subprocess
    from pathlib import Path
    shim = Path(__file__).resolve().parent.parent / "bin/worktree-harness"
    run = lambda *a: subprocess.run([str(shim), *a], cwd=git_repo, capture_output=True, text=True)
    assert json.loads(run("create", "feat/s").stdout)["branch"] == "feat/s"
    assert [x["branch"] for x in json.loads(run("list").stdout)] == ["feat/s"]
    assert json.loads(run("health").stdout)[0]["healthy"]
    assert run("remove", "feat/s", "--force").returncode == 0
    assert run("bogus").returncode == 1
