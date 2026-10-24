"""Localnet MVP tests (stdlib-only node; placeholder consensus)."""
from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tcl.consensus.types import sign_tx, verify_commit
from tcl.localnet import Genesis, Localnet, LocalnetError
from tcl.localnet.node import Reject
from tcl.localnet.rpc import RPCError, Server, call

G = Genesis(validators=4, dev_accounts=3)


def acct(net: Localnet, i: int) -> str:
    return list(net.dev_keys)[i]


def tx(net, sender, method, args, nonce=None):
    """Signed dev tx (anonymous when sender is None)."""
    if sender is None:
        return {"chain_id": net.chain_id, "module": "circulatory", "method": method,
                "sender": None, "nonce": 0, "args": args, "proof": {"dev": True}, "sig": None,
                "pow_stamp": 0}
    return net.sign_dev_tx(sender, method, args, nonce)


@pytest.fixture(params=["bft", "fast"])
def net(tmp_path, request):
    n = Localnet(tmp_path / "ln", G, consensus=request.param)
    yield n
    n.close()


def test_deterministic_genesis(tmp_path):
    a, b = Localnet(tmp_path / "a", G), Localnet(tmp_path / "b", G, consensus="fast")
    assert a.blocks[0].hash == b.blocks[0].hash
    assert a.status()["state_root"] == b.status()["state_root"]
    c = Localnet(tmp_path / "c", Genesis(validators=4, dev_accounts=3, seed="other"))
    assert c.status()["state_root"] != a.status()["state_root"]
    for n in (a, b, c):
        n.close()


def test_transfer_mine_and_all_validators_agree(net):
    a0, a1 = acct(net, 0), acct(net, 1)
    net.send_tx(tx(net, a0, "transfer", {"to": a1, "amount": 1000}))
    (r,) = net.mine()
    assert r["height"] == 1 and r["signers"] >= 3
    roots = {str(v.ledger.root()) for v in net.validators}
    assert len(roots) == 1 and net.verify()
    assert net.get_account(a0)["nonce"] == 1
    b = net.get_block(1)
    assert b["receipts"][0]["ok"] and b["header"]["prev_hash"] == net.blocks[0].hash


def test_reject_codes(net):
    a0, a1 = acct(net, 0), acct(net, 1)
    bad = tx(net, a0, "transfer", {"to": a1, "amount": 1})
    with pytest.raises(Reject):  # tampered signature refused at admission
        net.send_tx({**bad, "args": {"to": a1, "amount": 2}})
    net.send_tx(tx(net, a0, "transfer", {"to": a1, "amount": 1}, nonce=7))
    net.send_tx(sign_tx({**tx(net, a0, "x", {}, nonce=0), "module": "nope"},
                        net.dev_keys[a0]))
    net.send_tx(tx(net, a0, "nope", {}, nonce=0))
    net.send_tx(tx(net, None, "spend_nullifier", {"nullifier": 42}))
    net.send_tx(tx(net, None, "spend_nullifier", {"nullifier": 42}))
    net.send_tx(tx(net, a0, "transfer", {"to": a1, "amount": 10**15}, nonce=0))  # tier 1
    net.mine()
    codes = [r.get("code") for r in net.get_block(1)["receipts"]]
    assert codes == ["BAD_NONCE", "UNKNOWN_MODULE", "UNKNOWN_METHOD", None, "NULLIFIER_SPENT",
                     "TIER_LIMIT"]
    assert net.verify()


def test_time_warp_applies_demurrage(tmp_path):
    from tcl.ledger.demurrage_fixed import T
    from tcl.localnet import DevAccount
    net = Localnet(tmp_path / "w", Genesis(validators=4, accounts=(
        DevAccount("whale", 2 * T, 3), DevAccount("small", 10, 1)), dev_accounts=1))
    a0 = "whale"
    before = net.get_account(a0)["balance"]
    t0 = net.time
    net.warp(30 * 86_400)
    net.send_tx(tx(net, acct(net, 0), "settle", {"account": a0}))
    net.mine()
    assert net.blocks[-1].header["time"] == t0 + 30 * 86_400 + G.block_time
    assert net.get_account(a0)["balance"] < before
    with pytest.raises(LocalnetError):
        net.set_time(t0)  # never backwards
    assert net.get_account("small")["balance"] == 10  # below threshold: no decay
    assert net.verify()
    net.close()


def test_snapshot_revert(net):
    a0, a1 = acct(net, 0), acct(net, 1)
    sid = net.snapshot()
    root = net.status()["state_root"]
    net.send_tx(tx(net, a0, "transfer", {"to": a1, "amount": 5}))
    net.warp(1000)
    net.mine(3)
    assert net.height == 3
    st = net.revert(sid)
    assert st["height"] == 0 and st["state_root"] == root and net.verify()
    net.mine()  # chain continues deterministically after revert
    assert net.height == 1


def test_fault_injection_quorum_fast(tmp_path):
    net = Localnet(tmp_path / "f", G, consensus="fast")
    net.inject_fault({"validator": 1, "kind": "offline"})
    assert net.mine()[0]["signers"] == 3  # 3/4 > 2/3
    net.inject_fault({"validator": 2, "kind": "byzantine"})
    with pytest.raises(LocalnetError, match="no quorum"):
        net.mine()
    assert net.height == 1 and net.verify()  # failed round fully rolled back
    net.inject_fault({"validator": 1, "kind": "online"})  # catches up by replay
    assert net.mine()[0]["signers"] == 3
    assert net.validators[1].height == net.height
    net.close()


def test_bft_commits_with_signed_certificate(tmp_path):
    net = Localnet(tmp_path / "bft", G)  # default consensus is the real TendermintEngine
    assert net.consensus == "bft"
    a0, a1 = acct(net, 0), acct(net, 1)
    net.send_tx(tx(net, a0, "transfer", {"to": a1, "amount": 9}))
    r = net.mine(2)
    assert [x["height"] for x in r] == [1, 2] and r[0]["signers"] >= 3
    for b in net.blocks[1:]:
        assert b.commit["block_hash"] == b.hash
        assert verify_commit(b.commit, net.chain_id, net.vals_sorted)
    assert net.blocks[2].last_commit == net.blocks[1].commit
    assert len({str(v.ledger.root()) for v in net.validators}) == 1 and net.verify()
    net.close()


def test_bft_byzantine_validator_does_not_fork(tmp_path):
    net = Localnet(tmp_path / "byz", G)
    net.inject_fault({"validator": 0, "kind": "byzantine"})
    a0, a1 = acct(net, 0), acct(net, 1)
    for h in range(4):  # rotates through proposers, incl. the equivocating one
        net.send_tx(tx(net, a0, "transfer", {"to": a1, "amount": 1 + h}))
        net.mine()
    honest = [v for v in net.validators if not v.byzantine]
    assert net.height == 4 and all(v.height == 4 for v in honest)
    assert len({str(v.ledger.root()) for v in honest}) == 1 and net.verify()
    assert net.get_account(a1)["balance"] == G.dev_balance + 10
    # a second fault (offline) leaves 2/4 honest -> no quorum, no fork, no state change
    net.inject_fault({"validator": 1, "kind": "offline"})
    with pytest.raises(LocalnetError, match="no quorum"):
        net.mine()
    assert net.height == 4 and net.verify()
    net.inject_fault({"validator": 1, "kind": "online"})
    net.inject_fault({"validator": 0, "kind": "honest"})
    net.mine()
    assert all(v.height == 5 for v in net.validators) and net.verify()
    net.close()


def test_bft_offline_validator_catches_up(tmp_path):
    net = Localnet(tmp_path / "off", G)
    net.inject_fault({"validator": 2, "kind": "offline"})
    net.mine(2)
    assert net.validators[2].height == 0
    net.inject_fault({"validator": 2, "kind": "online"})
    net.mine()
    assert net.validators[2].height == 3 and net.verify()
    net.close()


def test_non_dev_refuses_dev_methods(tmp_path):
    n = Localnet(tmp_path / "p", G, dev=False)
    for f in (lambda: n.warp(1), n.snapshot, lambda: n.inject_fault({})):
        with pytest.raises(LocalnetError, match="UNAUTHORIZED"):
            f()
    n.close()


def test_persistence_restart(tmp_path):
    n = Localnet(tmp_path / "r", G)
    n.mine(2)
    head = n.status()
    n.close()
    m = Localnet(tmp_path / "r")
    assert m.status()["head"] == head["head"] and m.verify()
    m.mine()
    m.close()


@pytest.fixture
def no_network(monkeypatch):
    """Fail any outbound connection that is not loopback/Unix (proves offline operation)."""
    real = socket.socket.connect
    attempts = []

    def guarded(self, addr):
        if self.family == socket.AF_UNIX:
            return real(self, addr)
        host = addr[0]
        if not ipaddress.ip_address(socket.gethostbyname(host)).is_loopback:
            attempts.append(addr)
            raise OSError(f"network disabled in test: {addr}")
        return real(self, addr)
    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(
        OSError("create_connection disabled")) if not ipaddress.ip_address(
        socket.gethostbyname(a[0][0])).is_loopback else _real_cc(*a, **k))
    yield attempts


_real_cc = socket.create_connection


def test_rpc_end_to_end_offline(tmp_path, no_network):
    with pytest.raises(OSError):
        socket.create_connection(("1.1.1.1", 80), timeout=1)
    net = Localnet(tmp_path / "rpc", G)
    srv = Server(net, port=0, sock=tmp_path / "rpc.sock").start()
    url = f"http://127.0.0.1:{srv.port}"
    try:
        assert call(url, "tcl_chainId") == G.chain_id
        a0 = acct(net, 0)
        call(url, "tcl_faucet", ["newbie", 777])
        sid = call(url, "tcl_snapshot")
        call(url, "tcl_warp", [3600])
        assert call(url, "tcl_mine", [2])[1]["height"] == 2
        assert call(url, "tcl_getAccount", ["newbie"])["balance"] > 0
        assert call("unix:" + str(tmp_path / "rpc.sock"), "tcl_status")["height"] == 2
        assert call(url, "tcl_revert", [sid])["height"] == 0
        with pytest.raises(RPCError) as e:
            call(url, "tcl_sendTx", [{"chain_id": "wrong", "module": "circulatory",
                                      "method": "transfer", "sender": a0, "nonce": 0,
                                      "args": {}}])
        assert e.value.data == {"reject": "BAD_ARGS"}
        with pytest.raises(RPCError):
            call(url, "eth_blockNumber")
        assert call(url, "tcl_getProof", [a0])
        tid = call(url, "tcl_devSend", [a0, "transfer", {"to": acct(net, 1), "amount": 3}])
        call(url, "tcl_mine", [1])
        assert call(url, "tcl_getTx", [tid])["receipt"]["ok"]
        assert call(url, "tcl_verifyState")
    finally:
        srv.shutdown()
    assert no_network == []


def test_rpc_dev_methods_refused_without_dev(tmp_path):
    srv = Server(Localnet(tmp_path / "nd", G, dev=False), port=0).start()
    # (bft default)
    try:
        with pytest.raises(RPCError) as e:
            call(f"http://127.0.0.1:{srv.port}", "tcl_mine")
        assert e.value.data == {"reject": "UNAUTHORIZED"}
    finally:
        srv.shutdown()


def test_localnet_stdlib_only():
    """New localnet code imports only stdlib + tcl itself (offline-bundle guarantee)."""
    import ast
    allowed = set(sys.stdlib_module_names) | {"tcl"}
    for f in Path(__file__).resolve().parents[1].joinpath("src/tcl/localnet").glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [node.module or ""]
            else:
                continue
            for m in mods:
                assert m.split(".")[0] in allowed, f"{f.name} imports {m}"


def test_cli_start_mine_stop(tmp_path):
    d = tmp_path / "cli"
    env_cmd = [sys.executable, "-m", "tcl.cli"]
    def run(*a):
        return subprocess.run(env_cmd + ["localnet", *a, "--data-dir", str(d)],
                              capture_output=True, text=True, timeout=60)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    r = run("start", "--port", str(port), "-n", "4")
    assert r.returncode == 0, r.stderr
    try:
        st = json.loads(run("status").stdout)
        assert st["consensus"] == "bft"
        dev = json.loads(run("accounts").stdout)
        assert run("transfer", dev[0], dev[1], "5").returncode == 0
        assert json.loads(run("mine", "2").stdout)[-1]["height"] == 2
        assert run("time", "advance", "60").returncode == 0
        sid = json.loads(run("snapshot").stdout)
        run("mine")
        assert json.loads(run("revert", sid).stdout)["height"] == 2
        assert json.loads(run("status").stdout)["height"] == 2
    finally:
        run("stop")
    for _ in range(50):
        if not (d / "node.json").exists():
            break
        time.sleep(0.1)
    assert not (d / "node.json").exists()
