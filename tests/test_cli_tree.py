import json

from typer.testing import CliRunner

from tcl.cli import app

r = CliRunner()


def j(res):
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def test_root_proof_rollback(tmp_path):
    db = str(tmp_path / "c.db")
    a = ["--db", db, "--depth", "10"]
    g = j(r.invoke(app, ["ledger", "root", *a, "--history"]))
    assert g["integrity"] == "ok" and g["state_verified"] and g["transitions"] == 0
    # db is now pinned to depth 10; default depth 20 must fail closed
    res = r.invoke(app, ["ledger", "deposit", "alice", "1000", "--db", db, "--now", "0"])
    assert res.exit_code != 0 and "depth mismatch" in str(res.exception)
    assert j(r.invoke(app, ["ledger", "root", *a]))["transitions"] == 0


def test_root_proof_rollback_depth20(tmp_path):
    db = str(tmp_path / "c.db")
    a = ["--db", db]
    j(r.invoke(app, ["ledger", "deposit", "alice", "1000", *a, "--now", "0"]))
    r1 = j(r.invoke(app, ["ledger", "root", *a]))
    j(r.invoke(app, ["ledger", "deposit", "bob", "5", *a, "--now", "1"]))
    r2 = j(r.invoke(app, ["ledger", "root", *a, "--history"]))
    assert r2["root"] != r1["root"] and r1["root"] in r2["history"]
    pf = j(r.invoke(app, ["ledger", "proof", "bob", *a]))
    assert pf["root"] == r2["root"]
    pfile = tmp_path / "p.json"
    pfile.write_text(json.dumps(pf))
    assert j(r.invoke(app, ["ledger", "verify-proof", str(pfile)]))["valid"]
    pf["leaf"] = str(int(pf["leaf"]) + 1)
    pfile.write_text(json.dumps(pf))
    assert r.invoke(app, ["ledger", "verify-proof", str(pfile)]).exit_code == 1
    rb = j(r.invoke(app, ["ledger", "rollback", "--root", r1["root_hex"], *a, "--now", "2"]))
    assert rb["root"] == r1["root"] and rb["state_verified"] and rb["undone"] == 1
    assert r.invoke(app, ["ledger", "rollback", "--root", "123", *a]).exit_code == 1
    assert r.invoke(app, ["ledger", "proof", "bob", *a]).exit_code == 0  # leaf slot kept
    b = j(r.invoke(app, ["ledger", "balance", "bob", *a]))
    assert b["balance"] == "0"
