"""Acceptance Test 2: parallel worktree isolation + Conflict Guard, via tcl-cli + daemon."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tcl.cli import app
from tcl.daemon import Daemon, request
from test_daemon import wait_terminal

r = CliRunner()


@pytest.fixture
def daemon(git_repo, tmp_path):
    (git_repo / "package.json").write_text('{"name": "tcl"}\n')
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "pkg"], cwd=git_repo, check=True)
    sd = Path(tempfile.mkdtemp(prefix="tcl", dir="/tmp"))
    d = Daemon(sd / "d.sock", db=tmp_path / "t.db", repo=git_repo, workers=4,
               telemetry=tmp_path / "tel.jsonl").start()
    yield d
    d.stop()
    shutil.rmtree(sd, ignore_errors=True)


def create(sock, tid, code):
    out = r.invoke(app, ["task", "create", tid, "--socket", str(sock), "--", "sh", "-c", code])
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["submitted"] == [tid]


def test_acceptance_2(daemon, git_repo, tmp_path):
    sock, eng = daemon.socket_path, daemon.engine
    wt = git_repo.parent / "worktrees"
    # 1) independent files -> concurrent, isolated, merged
    create(sock, "ind-a", "echo a > a.txt; sleep 1")
    create(sock, "ind-b", "echo b > b.txt; sleep 1")
    st = wait_terminal(sock, ["ind-a", "ind-b"])
    assert st == {"ind-a": "MERGED", "ind-b": "MERGED"}
    (a0, a1), (b0, b1) = eng.timeline["ind-a"], eng.timeline["ind-b"]
    assert a0 < b1 and b0 < a1, "tasks did not overlap in time"
    assert (wt / "worktree-task_ind-a/a.txt").exists()
    assert not (wt / "worktree-task_ind-a/b.txt").exists()  # isolation
    assert not (wt / "worktree-task_ind-b/a.txt").exists()
    assert not (git_repo / "a.txt").exists()
    assert eng.alerts == []
    # 2) both edit package.json -> Conflict Guard alert before merge
    create(sock, "pkg-1", "echo '{\"v\":1}' > package.json; sleep 0.5")
    create(sock, "pkg-2", "echo '{\"v\":2}' > package.json; sleep 1.5")
    st = wait_terminal(sock, ["pkg-1", "pkg-2"])
    assert len(eng.alerts) >= 1
    alert = eng.alerts[0]
    assert alert.files == ("package.json",)
    assert {alert.worktree, *alert.others} == {"pkg-1", "pkg-2"}
    flagged = eng.tasks[alert.worktree]
    assert flagged.state.value == "FAILED" and "conflict: package.json" in flagged.error
    assert "MERGED" not in [h[0] for h in flagged.history]  # never merged
    assert sorted(st.values()) == ["FAILED", "MERGED"]
    health = request(sock, {"op": "health"})
    assert health["alerts"][0]["files"] == ["package.json"]
    assert all(w["healthy"] for w in health["worktrees"])
    tel = [json.loads(l) for l in (tmp_path / "tel.jsonl").read_text().splitlines()]
    assert any(t["event"] == "conflict.alert" for t in tel)
    # the state machine record in the worktree registry agrees
    out = json.loads(r.invoke(app, ["worktree", "list", "--repo", str(git_repo)]).stdout)
    assert {x["task"]["task_id"]: x["task"]["state"] for x in out}[alert.worktree] == "FAILED"
