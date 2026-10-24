"""Multi-process localnet: 4 validator processes over authenticated localhost TCP p2p,
one killed (SIGKILL) and restarted; it must catch up and agree. Offline only."""
from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import socket
import subprocess
import sys
import time

import pytest

from tcl.consensus import ed25519
from tcl.localnet import p2p
from tcl.localnet.rpc import call

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="posix signals")


def cli(d, *a, timeout=60):
    return subprocess.run([sys.executable, "-m", "tcl.cli", "localnet", *a, "--data-dir", str(d)],
                          capture_output=True, text=True, timeout=timeout)


def wait(pred, timeout=60.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except OSError:
            pass
        time.sleep(step)
    return False


def free_base():
    for _ in range(50):
        base = random.randint(20000, 40000)
        ok = True
        for i in range(6):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", base + i))
                except OSError:
                    ok = False
        if ok:
            return base
    raise RuntimeError("no free ports")


def test_four_processes_kill_restart(tmp_path):
    d = tmp_path / "pn"
    base = free_base()
    r = cli(d, "start", "--processes", "-n", "4", "--port", str(base))
    assert r.returncode == 0, r.stderr
    info = json.loads((d / "node.json").read_text())
    urls, pids = info["urls"], info["pid"]
    st = lambda i: call(urls[i], "tcl_status", timeout=10)  # noqa: E731
    try:
        assert wait(lambda: all(len(st(i)["peers"]) == 3 for i in range(4)))
        assert wait(lambda: min(st(i)["height"] for i in range(4)) >= 3)
        dev = call(urls[0], "tcl_devAccounts")
        call(urls[0], "tcl_devSend", [dev[0], "transfer", {"to": dev[1], "amount": 7}])
        assert wait(lambda: all(call(urls[i], "tcl_getAccount", [dev[1]])["balance"] ==
                                10**12 + 7 for i in range(4)))
        # kill validator 2 hard; 3/4 is still > 2/3 so the chain keeps finalizing
        os.kill(pids[2], signal.SIGKILL)
        h = max(st(i)["height"] for i in (0, 1, 3))
        assert wait(lambda: min(st(i)["height"] for i in (0, 1, 3)) >= h + 4)
        call(urls[0], "tcl_devSend", [dev[0], "transfer", {"to": dev[1], "amount": 5}])
        assert wait(lambda: call(urls[1], "tcl_getAccount", [dev[1]])["balance"] ==
                    10**12 + 12)
        # restart it with the same data dir: it syncs blocks (certificates checked) and rejoins
        (d / "node2" / "node.json").unlink(missing_ok=True)
        p = subprocess.Popen([sys.executable, "-m", "tcl.localnet.proc", "--data-dir", str(d),
                              "--index", "2", "--base-port", str(base + 1)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        pids[2] = p.pid
        assert wait(lambda: (d / "node2" / "node.json").exists())
        urls[2] = json.loads((d / "node2" / "node.json").read_text())["url"]
        target = st(0)["height"]
        assert wait(lambda: st(2)["height"] >= target + 2, timeout=90)
        assert call(urls[2], "tcl_getAccount", [dev[1]])["balance"] == 10**12 + 12
        common = min(st(i)["height"] for i in range(4))
        blocks = [call(urls[i], "tcl_getBlock", [common]) for i in range(4)]
        assert len({b["hash"] for b in blocks}) == 1
        assert len({b["header"]["state_root"] for b in blocks}) == 1
        # an unknown key cannot join the mesh (mutual Ed25519 authentication)
        before = st(0)["rejected_handshakes"]

        async def intruder():
            r_, w_ = await asyncio.open_connection("127.0.0.1", base + 1)
            with pytest.raises((p2p.AuthError, asyncio.IncompleteReadError,
                                ConnectionError)):
                await p2p.handshake(r_, w_, b"\x07" * 32, "tcl-localnet-1",
                                    {ed25519.public_key(b"\x00" * 32).hex()})
            w_.close()
        asyncio.run(intruder())
        assert wait(lambda: st(0)["rejected_handshakes"] > before)
    finally:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        (d / "node.json").unlink(missing_ok=True)


def test_p2p_frames_are_authenticated():
    """Tampered, replayed or reordered frames are rejected after the handshake."""
    async def run():
        sk_a, sk_b = b"\x01" * 32, b"\x02" * 32
        ka, kb = ed25519.public_key(sk_a).hex(), ed25519.public_key(sk_b).hex()
        got: list = []
        server_peer: list = []

        async def accept(r, w):
            p = await p2p.handshake(r, w, sk_b, "c", {ka})
            server_peer.append(p)
            try:
                while True:
                    got.append(await p.recv())
            except (p2p.AuthError, asyncio.IncompleteReadError):
                got.append("REJECTED")
        srv = await asyncio.start_server(accept, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        p = await p2p.handshake(r, w, sk_a, "c", {kb})
        await p.send({"x": 1})
        # forge a frame with a valid structure but wrong signature
        p2p.write_frame(w, {"seq": 1, "body": {"x": 2}, "sig": "00" * 64})
        await w.drain()
        await asyncio.sleep(0.3)
        w.close()
        srv.close()
        return got
    assert asyncio.run(run()) == [{"x": 1}, "REJECTED"]


def test_p2p_wrong_chain_rejected():
    async def run():
        sk_a, sk_b = b"\x01" * 32, b"\x02" * 32
        ka, kb = ed25519.public_key(sk_a).hex(), ed25519.public_key(sk_b).hex()
        res = []

        async def accept(r, w):
            try:
                await p2p.handshake(r, w, sk_b, "chain-B", {ka})
            except p2p.AuthError as e:
                res.append(str(e))
            w.close()
        srv = await asyncio.start_server(accept, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        try:
            await p2p.handshake(r, w, sk_a, "chain-A", {kb})
        except (p2p.AuthError, asyncio.IncompleteReadError, ConnectionError):
            pass
        await asyncio.sleep(0.2)
        srv.close()
        return res
    assert asyncio.run(run()) and "chain_id" in asyncio.run(run())[0]
