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

from tcl.localnet import Genesis, Localnet, LocalnetError
from tcl.localnet.rpc import RPCError, Server, call

G = Genesis(validators=4, dev_accounts=3)


def acct(net: Localnet, i: int) -> str:
    return net.genesis.dev_account_list()[i].name


def tx(net, sender, method, args, nonce=None):
    n = net.get_account(sender)["nonce"] if (nonce is None and sender) else (nonce or 0)
    return {"chain_id": net.chain_id, "module": "circulatory", "method": method,
            "sender": sender, "nonce": n, "args": args}


@pytest.fixture
def net(tmp_path):
    n = Localnet(tmp_path / "ln", G)
    yield n
    n.close()


def test_deterministic_genesis(tmp_path):
    a, b = Localnet(tmp_path / "a", G), Localnet(tmp_path / "b", G)
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
    assert r["height"] == 1 and r["signers"] == 4
    roots = {str(v.ledger.root()) for v in net.validators}
    assert len(roots) == 1 and net.verify()
    assert net.get_account(a0)["nonce"] == 1
    b = net.get_block(1)
    assert b["receipts"][0]["ok"] and b["header"]["prev_hash"] == net.blocks[0].hash


def test_reject_codes(net):
    a0, a1 = acct(net, 0), acct(net, 1)
    net.send_tx(tx(net, a0, "transfer", {"to": a1, "amount": 1}, nonce=7))
    net.send_tx({**tx(net, a0, "x", {}), "module": "nope"})
    net.send_tx(tx(net, a0, "nope", {}))
    net.send_tx(tx(net, None, "spend_nullifier", {"nullifier": 42}))
    net.send_tx(tx(net, None, "spend_nullifier", {"nullifier": 42}))
    net.send_tx(tx(net, a0, "transfer", {"to": a1, "amount": 10**15}))  # tier 1 limit
    net.mine()
    codes = [r.get("code") for r in net.get_block(1)["receipts"]]
    assert codes == ["BAD_NONCE", "UNKNOWN_MODULE", "UNKNOWN_METHOD", None, "NULLIFIER_SPENT",
                     "TIER_LIMIT"]
    assert net.verify()


def test_time_warp_applies_demurrage(tmp_path):
    from tcl.ledger.demurrage_fixed import T
    from tcl.localnet import DevAccount
    net = Localnet(tmp_path / "w", Genesis(validators=4, accounts=(
        DevAccount("whale", 2 * T, 3), DevAccount("small", 10, 1))))
    a0 = "whale"
    before = net.get_account(a0)["balance"]
    t0 = net.time
    net.warp(30 * 86_400)
    net.send_tx(tx(net, None, "settle", {"account": a0}))
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


def test_fault_injection_quorum(net):
    net.inject_fault({"validator": 1, "kind": "offline"})
    assert net.mine()[0]["signers"] == 3  # 3/4 > 2/3
    net.inject_fault({"validator": 2, "kind": "byzantine"})
    with pytest.raises(LocalnetError, match="no quorum"):
        net.mine()
    assert net.height == 1 and net.verify()  # failed round fully rolled back
    net.inject_fault({"validator": 1, "kind": "online"})  # catches up by replay
    assert net.mine()[0]["signers"] == 3
    assert net.validators[1].height == net.height


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
    finally:
        srv.shutdown()
    assert no_network == []


def test_rpc_dev_methods_refused_without_dev(tmp_path):
    srv = Server(Localnet(tmp_path / "nd", G, dev=False), port=0).start()
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
    r = run("start", "--port", str(port), "-n", "3")
    assert r.returncode == 0, r.stderr
    try:
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
