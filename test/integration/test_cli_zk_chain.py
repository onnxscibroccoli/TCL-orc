"""`tcl zk` / `tcl chain` CLI end-to-end against a throwaway anvil, plus replay rejection."""
import json
import time

from typer.testing import CliRunner

from tcl.chain import anvil
from tcl.cli import app
from tcl.ledger.demurrage_fixed import SCALE, T

DEPTH = 4

r = CliRunner()


def run(*args, ok=True):
    out = r.invoke(app, list(args))
    assert (out.exit_code == 0) == ok, out.output
    return out


def test_cli_compile_prove_verify_deploy_settle(zk_art, evm_tools, tmp_path, monkeypatch):
    monkeypatch.setenv("TCL_ZK_BUILD", str(zk_art.out_dir))
    monkeypatch.setenv("TCL_ZK_DEPTH", str(DEPTH))
    monkeypatch.setenv("TCL_TREE_DEPTH", str(DEPTH))
    monkeypatch.setenv("TCL_DB", str(tmp_path / "l.db"))
    monkeypatch.setenv("TCL_DEPLOYMENT", str(tmp_path / "dep.json"))
    now = int(time.time()) - 2
    t0 = now - 2 * 86_400
    assert json.loads(run("zk", "compile").stdout)["depth"] == DEPTH  # cached build
    run("ledger", "deposit", "alice", str(T + 1_000_000 * SCALE), "--now", str(t0))
    proof = tmp_path / "proof.json"
    with anvil() as rpc:
        monkeypatch.setenv("TCL_RPC_URL", rpc)
        dep = json.loads(run("chain", "deploy").stdout)
        assert dep["chain_id"] == 31337
        pr = json.loads(run("zk", "prove", "alice", "--now", str(now), "--out", str(proof)).stdout)
        assert pr["verified"] and pr["public"]["oldRoot"] == dep["genesis_root"]
        assert json.loads(run("zk", "verify", str(proof)).stdout) == {"valid": True}
        ev = json.loads(run("zk", "settle", str(proof)).stdout)
        assert ev["event"] == "StateSettled" and ev["newRoot"] == pr["public"]["newRoot"]
        # replaying the same proof must fail closed (StaleRoot)
        bad = run("chain", "settle", str(proof), ok=False)
        assert "StaleRoot" in bad.output
    # tampered proof is rejected by `zk verify`
    b = json.loads(proof.read_text())
    b["publicSignals"][1] = str(int(b["publicSignals"][1]) + 1)
    proof.write_text(json.dumps(b))
    assert json.loads(run("zk", "verify", str(proof), ok=False).stdout) == {"valid": False}
