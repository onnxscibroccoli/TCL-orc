import subprocess

from tcl.orchestrator.conflict_guard import ConflictGuard, modified_files
from tcl.worktree import WorktreeManager


def test_modified_files_and_overlap(git_repo):
    m = WorktreeManager(git_repo, layout="sibling")
    a = m.create("a")["path"]
    b = m.create("b")["path"]
    c = m.create("c")["path"]
    assert a.endswith("/worktrees/worktree-a")
    # unstaged edit, untracked file, committed change
    open(f"{a}/README", "w").write("changed")
    open(f"{b}/new.txt", "w").write("n")
    subprocess.run(["git", "add", "README"], cwd=c, check=True)
    open(f"{c}/README", "w").write("c")
    subprocess.run(["git", "commit", "-qam", "c"], cwd=c, check=True)
    assert modified_files(a, "main") == {"README"}
    assert modified_files(b, "main") == {"new.txt"}
    assert modified_files(c, "main") == {"README"}

    g = ConflictGuard()
    seen = []
    g.on_alert(seen.append)
    for n, p in (("a", a), ("b", b), ("c", c)):
        g.register(n, p, "main")
    assert g.check("b") is None
    alert = g.check("a")
    assert alert.others == ("c",) and alert.files == ("README",) and seen == [alert]
    assert len(g.scan()) == 2  # a and c both flagged
    g.unregister("c")
    assert g.check("a") is None


def test_sibling_layout_shares_object_store(git_repo):
    m = WorktreeManager(git_repo, layout="sibling")
    p = m.create("feat/x")["path"]
    assert p == str(git_repo.parent / "worktrees" / "worktree-feat_x")
    assert m.common_dir(p) == (git_repo / ".git").resolve()
    h = m.health()
    assert h[0]["healthy"] and h[0]["registered"]
