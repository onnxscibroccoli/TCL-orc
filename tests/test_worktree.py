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
