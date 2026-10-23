"""Acceptance 4 (+ end-to-end timing): task -> sibling worktree -> code change ->
ledger update -> Groth16 proof -> deploy to local anvil -> settleEpoch -> StateSettled.

The measured window starts when the task is submitted to the daemon and ends
when the StateSettled event has been decoded from the receipt. It excludes the
one-time circuit compile + dev trusted setup (cached; reported by conftest).
"""
import json
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path

from tcl.chain import anvil, deploy, settle_epoch
from tcl.daemon import Daemon, request
from tcl.ledger.demurrage_fixed import SCALE, T
from tcl.ledger.service import Ledger
from tcl.storage.db import SQLiteStorage
from tcl.zk import prove_settlement, verify

DEPTH = 4

BUDGET_S = 30.0
DAY = 86_400


def _wait(sock, tid, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = request(sock, {"op": "status", "task_id": tid})["task"]
        if st["state"] in ("MERGED", "FAILED"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"task {tid} not terminal")


def test_full_pipeline_state_settled(zk_art, evm_tools, repo, tmp_path):
    sd = Path(tempfile.mkdtemp(prefix="tcl", dir="/tmp"))
    d = Daemon(sd / "d.sock", db=tmp_path / "tasks.db", repo=repo, workers=2,
               http=("127.0.0.1", 0)).start()
    timings = {}
    try:
        t_start = time.monotonic()
        # health over HTTP alongside the socket
        with urllib.request.urlopen(f"http://{d.http_addr[0]}:{d.http_addr[1]}/health") as r:
            assert r.status == 200 and json.loads(r.read())["status"] == "healthy"
        # 1) task scheduled -> runs in a sibling worktree and makes a code change
        now = int(time.time()) - 2
        t0 = now - 3 * DAY
        change = {"alice": str(T + 2_000_000 * SCALE), "bob": str(42 * SCALE), "t0": t0}
        script = f"printf '%s' '{json.dumps(change)}' > deposits.json"
        request(d.socket_path, {"op": "submit", "task": {
            "task_id": "e2e", "command": ["sh", "-c", script], "timeout": 10}})
        st = _wait(d.socket_path, "e2e")
        assert st["state"] == "MERGED", st
        wt = repo.parent / "worktrees" / "worktree-task_e2e"
        assert (wt / "deposits.json").exists() and not (repo / "deposits.json").exists()
        timings["task"] = time.monotonic() - t_start
        # 2) ledger update from the worktree's change (persistent SQLite WAL)
        dep_in = json.loads((wt / "deposits.json").read_text())
        led = Ledger(SQLiteStorage(tmp_path / "ledger.db"), "fixed", depth=DEPTH)
        for acct in ("bob", "alice"):
            led.deposit(acct, int(dep_in[acct]), dep_in["t0"])
        genesis_root = led.root()
        # 3) Groth16 proof of the demurrage transition (verified locally)
        t1 = time.monotonic()
        bundle = prove_settlement(led, "alice", now, zk_art, tmp_path / "zk")
        timings["prove"] = time.monotonic() - t1
        assert verify(zk_art.vkey, bundle["proof"], bundle["publicSignals"], tmp_path / "v")
        assert int(bundle["public"]["fee"]) > 0
        assert int(bundle["public"]["oldRoot"]) == genesis_root
        # 4) deploy to local anvil + settleEpoch
        t2 = time.monotonic()
        with anvil() as rpc:
            dep = deploy(rpc, genesis_root, dep_in["t0"], tmp_path / "chain", zkey=zk_art.zkey)
            timings["deploy"] = time.monotonic() - t2
            t3 = time.monotonic()
            ev = settle_epoch(dep, bundle["calldata"])
            timings["settle"] = time.monotonic() - t3
        timings["total"] = time.monotonic() - t_start
    finally:
        d.stop()
        shutil.rmtree(sd, ignore_errors=True)
    # 5) Acceptance 4: StateSettled with the proven values
    assert ev["event"] == "StateSettled" and ev["epoch"] == 1
    assert ev["oldRoot"] == bundle["public"]["oldRoot"]
    assert ev["newRoot"] == bundle["public"]["newRoot"]
    assert ev["fee"] == bundle["public"]["fee"]
    assert ev["newTime"] == now
    report = {k: round(v, 3) for k, v in timings.items()}
    print("\n[e2e timings s]", json.dumps(report))
    out = Path(__file__).resolve().parents[2] / ".tcl" / "e2e-timings.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({**report, "event": ev}, indent=2))
    assert timings["total"] < BUDGET_S, report
