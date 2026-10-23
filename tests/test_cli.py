import json

from typer.testing import CliRunner

from tcl.cli import app
from tcl.ledger.demurrage_fixed import SCALE, T, apply_demurrage_fixed

r = CliRunner()


def j(res):
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def test_ledger_flow(tmp_path):
    db = str(tmp_path / "c.db")
    assert j(r.invoke(app, ["ledger", "init", "--db", db]))["journal_mode"] == "wal"
    start = T + 10**6 * SCALE
    j(r.invoke(app, ["ledger", "deposit", "alice", str(start), "--db", db, "--now", "0"]))
    out = j(r.invoke(app, ["ledger", "apply-decay", "--db", db, "--now", "86400"]))
    exp_bal, exp_fee = apply_demurrage_fixed(start, 86400)
    assert int(out["extracted"]) == exp_fee
    b = j(r.invoke(app, ["ledger", "balance", "alice", "--db", db]))
    assert int(b["balance"]) == exp_bal and int(b["vault"]) == exp_fee


def test_worktree_cli(git_repo):
    rec = j(r.invoke(app, ["worktree", "create", "feat/x", "--repo", str(git_repo)]))
    assert rec["task"]["state"] == "ALLOCATED"
    lst = j(r.invoke(app, ["worktree", "list", "--repo", str(git_repo)]))
    assert [x["branch"] for x in lst] == ["feat/x"]
    dup = r.invoke(app, ["worktree", "create", "feat/x", "--repo", str(git_repo)])
    assert dup.exit_code == 1


def test_structured_logging(tmp_path, capsys):
    import io, logging
    from tcl import logs
    buf = io.StringIO()
    logs.configure("INFO", stream=buf)
    logs.get_logger("t").info("hello", extra={"k": 1})
    rec = json.loads(buf.getvalue())
    assert rec["event"] == "hello" and rec["k"] == 1 and rec["logger"] == "tcl.t"
    logs.configure("WARNING")
