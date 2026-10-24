"""One localnet validator as its own OS process, talking to peers over the authenticated
localhost TCP transport (p2p.py). Started by `tcl localnet start --processes`.

  python -m tcl.localnet.proc --data-dir D --index I [--base-port P] [--rpc-port R]

Layout: D/genesis.json is shared; each node keeps D/node<I>/{val.sqlite3,blocks.jsonl}.
Consensus: one TendermintEngine per height (13b), real timers (1 engine tick = --tick-ms).
Sync: on connect and every second peers exchange STATUS; a node that is behind receives the
committed blocks with their >2/3 certificates, checks each certificate against ITS OWN committed
validator set, and re-executes them (so a killed-and-restarted node catches up, fail-closed).
Evidence the engine collects is submitted as a consensus/evidence tx; the beacon leader adds its
ECVRF reveal to blocks it proposes. Stdlib only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional

from ..consensus.engine import Broadcast, Decide, ScheduleTimeout, TendermintEngine
from ..consensus.types import CommitCert, Proposal, Vote, sign_tx, tx_id, verify_commit
from ..node.chain import ChainState, evidence_args
from .genesis import Genesis
from .node import (Block, Localnet, LocalnetError, Reject, Tx, Validator, _HostApp,
                   init_validator_genesis, verify_tx_sig)


def enc(m: Any) -> dict:
    if isinstance(m, Vote):
        return {"t": "vote", **m.to_dict()}
    if isinstance(m, Proposal):
        return {"t": "proposal", "height": m.height, "round": m.round, "pol_round": m.pol_round,
                "block": m.block, "proposer": m.proposer, "sig": m.sig}
    if isinstance(m, CommitCert):
        return {"t": "cert", "height": m.height, "block": m.block, "commit": m.commit,
                "round": m.round}
    raise TypeError(type(m))


def dec(d: dict) -> Any:
    t = d.get("t")
    if t == "vote":
        return Vote(d["kind"], d["height"], d["round"], d["block_hash"], d["validator"], d["sig"])
    if t == "proposal":
        return Proposal(d["height"], d["round"], d["pol_round"], d["block"], d["proposer"],
                        d["sig"])
    if t == "cert":
        return CommitCert(d["height"], d["block"], d["commit"], d["round"])
    raise ValueError(t)


class _App(_HostApp):
    def on_decide(self, block: dict, commit: dict) -> None:
        super().on_decide(block, commit)
        self.net._committed(block, commit, self.decided[2])


class ProcNode:
    def __init__(self, data_dir: Path, index: int, base_port: int, tick_ms: int = 10,
                 block_interval: float = 0.2, dev: bool = True) -> None:
        self.root = Path(data_dir)
        self.genesis = Genesis.from_json(json.loads((self.root / "genesis.json").read_text()))
        g = self.genesis
        self.chain_id, self.index, self.base_port = g.chain_id, index, base_port
        self.tick, self.interval, self.dev = tick_ms / 1000, block_interval, dev
        self.dir = self.root / f"node{index}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.v = Validator(index, g.validator_secret(index), self.dir / "val.sqlite3")
        self.v.chain_id, self.v.dev_proofs = self.chain_id, g.dev_proofs
        self.tiers = {a.name: a.personhood_tier for a in g.dev_account_list()}
        self.dev_keys = g.dev_keys() if dev else {}
        self.blocks_path = self.dir / "blocks.jsonl"
        if self.blocks_path.exists():
            self.blocks = [Block.from_json(json.loads(x))
                           for x in self.blocks_path.read_text().splitlines()]
            self.v.height = self.v.meta("height", 0)
            if self.v.height != len(self.blocks) - 1:
                raise LocalnetError("node db and block log disagree")
        else:
            hdr = init_validator_genesis(self.v, g)
            self.blocks = []
            self.blocks_path.write_text("")
            self._append(Block(hdr, [], []))
        self.mempool: dict[str, dict] = {}
        self.pending: list = []          # engine messages for future heights
        self.engine: Optional[TendermintEngine] = None
        self.app: Optional[_App] = None
        self._round_txs: list = []
        self._round_time = 0
        self._submitted_ev: set = set()
        self.lock = asyncio.Lock()
        ids = g.validator_ids()
        self.peer_ports = {k: base_port + i for i, k in enumerate(ids)}
        self.transport = None

    # --- duck-typed surface used by _HostApp ------------------------------------------
    _header = Localnet._header

    def valset_at(self, height: int) -> list[str]:
        hist = self.v.meta("chain", None)["valset_hist"]
        keys = hist[0][1]
        for start, ks in hist:
            if start <= height:
                keys = ks
        return keys

    @property
    def vals_sorted(self) -> list[str]:
        return self.v.chain_keys()

    @property
    def height(self) -> int:
        return self.blocks[-1].header["height"]

    # --- chain ------------------------------------------------------------------------
    def _append(self, b: Block) -> None:
        self.blocks.append(b)
        with self.blocks_path.open("a") as f:
            f.write(json.dumps(b.to_json(), sort_keys=True) + "\n")

    def _committed(self, block: dict, commit: dict, rc: list) -> None:
        self._append(Block(block["header"], block["txs"], rc, block["last_commit"], commit))
        for t in block["txs"]:
            self.mempool.pop(tx_id(t), None)

    def apply_synced(self, b: Block) -> bool:
        """Re-execute a committed block from a peer after checking its certificate."""
        if b.header["height"] != self.height + 1 or b.header["prev_hash"] != self.blocks[-1].hash:
            return False
        if not b.commit or b.commit.get("block_hash") != b.hash or \
                not verify_commit(b.commit, self.chain_id, self.v.chain_keys()):
            return False
        try:
            with self.v.storage.transaction():
                root, rc = self.v.execute_block([Tx.from_json(t) for t in b.txs],
                                                b.header["time"], self.tiers, b.last_commit)
                if root != b.header["state_root"] or self.v.last_aux != b.header["aux_roots"]:
                    raise LocalnetError("diverged")
        except (LocalnetError, Reject):
            self.v.height = self.v.meta("height", 0)
            return False
        self._committed({"header": b.header, "txs": b.txs, "last_commit": b.last_commit},
                        b.commit, rc)
        return True

    def _round_inputs(self) -> None:
        txs = list(self.mempool.values())
        st = ChainState(self.v.meta("chain", None), self.chain_id, None)
        pend = st.beacon_pending()
        if pend and pend[0] == self.v.id:
            from ..consensus import vrf
            txs.append({"chain_id": self.chain_id, "module": "consensus", "method": "beacon",
                        "sender": None, "nonce": 0, "args": {"epoch": pend[1],
                                                             "pi": vrf.prove(self.v.secret,
                                                                             pend[2]).hex()},
                        "proof": None, "sig": None, "pow_stamp": 0})
        self._round_txs = txs
        self._round_time = self.blocks[-1].header["time"] + self.genesis.block_time

    # --- engine driving ---------------------------------------------------------------
    async def _route(self, actions: list) -> None:
        decided = False
        for a in actions:
            if decided and not (isinstance(a, Broadcast) and isinstance(a.msg, CommitCert)):
                continue  # engine already moved to h+1 with stale inputs; a fresh one starts
            if isinstance(a, ScheduleTimeout):
                asyncio.get_running_loop().call_later(
                    a.delay * self.tick, lambda a=a: asyncio.ensure_future(
                        self._timeout(a.height, a.round, a.step)))
            elif isinstance(a, Broadcast):
                await self.transport.broadcast(enc(a.msg))
            elif isinstance(a, Decide):
                decided = True
        if self.engine is not None:
            for ev in self.engine.evidence:
                if ev.key() not in self._submitted_ev:
                    self._submitted_ev.add(ev.key())
                    t = {"chain_id": self.chain_id, "module": "consensus",
                         "method": "evidence", "sender": None, "nonce": 0,
                         "args": evidence_args(ev), "proof": None, "sig": None, "pow_stamp": 0}
                    self.mempool[tx_id(t)] = t
                    await self.transport.broadcast({"t": "tx", "tx": t})

    async def _after(self, actions: list) -> None:
        await self._route(actions)
        if self.app is not None and self.app.decided:
            self.engine = self.app = None
            asyncio.get_running_loop().call_later(
                self.interval, lambda: asyncio.ensure_future(self._start_height()))

    async def _start_height(self) -> None:
        async with self.lock:
            if self.engine is not None:
                return
            if self.v.id not in self.vals_sorted:
                asyncio.get_running_loop().call_later(
                    self.interval, lambda: asyncio.ensure_future(self._start_height()))
                return
            self._round_inputs()
            self.app = _App(self, self.v)
            self.engine = TendermintEngine(self.app, self.v.secret)
            h = self.height + 1
            acts = self.engine.start(h)
            pend, self.pending = self.pending, []
            for m in pend:
                if m.height >= h and self.engine is not None and self.app and \
                        not self.app.decided:
                    acts += self.engine.on_message(m)
            await self._after(acts)

    async def _timeout(self, h: int, r: int, step: str) -> None:
        async with self.lock:
            if self.engine is None or self.engine.height != h:
                return
            self._round_inputs()
            await self._after(self.engine.on_timeout(h, r, step))

    async def on_message(self, peer: str, body: Any) -> None:
        if not isinstance(body, dict):
            return
        t = body.get("t")
        async with self.lock:
            if t == "status":
                ph = body.get("height", 0)
                for b in self.blocks[ph + 1: ph + 51]:
                    await self.transport.send(peer, {"t": "block", "block": b.to_json()})
                return
            if t == "block":
                try:
                    b = Block.from_json(body["block"])
                except (KeyError, TypeError):
                    return
                if b.header.get("height") == self.height + 1 and self.apply_synced(b):
                    self.engine = self.app = None
                    asyncio.get_running_loop().call_later(
                        0.01, lambda: asyncio.ensure_future(self._start_height()))
                return
            if t == "tx":
                tx = body.get("tx")
                if isinstance(tx, dict) and tx_id(tx) not in self.mempool:
                    self.mempool[tx_id(tx)] = tx
                return
            try:
                m = dec(body)
            except (KeyError, TypeError, ValueError):
                return
            cur = self.height + 1
            if m.height > cur or self.engine is None:
                if m.height >= cur:
                    self.pending.append(m)
                    self.pending = self.pending[-5000:]
                if m.height > cur:  # we are behind: ask for blocks
                    await self.transport.send(peer, {"t": "status", "height": self.height})
                return
            if m.height < cur:
                return
            self._round_inputs()
            await self._after(self.engine.on_message(m))

    async def on_connect(self, peer: str) -> None:
        await self.transport.send(peer, {"t": "status", "height": self.height})

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            await self.transport.broadcast({"t": "status", "height": self.height})

    # --- RPC (called from the HTTP thread via the loop) ------------------------------------
    async def rpc(self, method: str, params: list) -> Any:
        async with self.lock:
            if method == "tcl_status":
                return {"chain_id": self.chain_id, "index": self.index, "id": self.v.id,
                        "height": self.height, "head": self.blocks[-1].hash,
                        "state_root": self.blocks[-1].header["state_root"],
                        "peers": sorted(self.transport.peers), "mempool": len(self.mempool),
                        "rejected_handshakes": self.transport.rejected}
            if method == "tcl_getBlock":
                return self.blocks[int(params[0])].to_json()
            if method == "tcl_getAccount":
                a = self.v.storage.get_account(params[0])
                return {"address": params[0], "balance": a[0] if a else 0,
                        "nonce": self.v.meta("nonces", {}).get(params[0], 0)}
            if method == "tcl_sendTx":
                tx = Tx.from_json(params[0]).to_json()
                if tx["chain_id"] != self.chain_id:
                    raise Reject("BAD_ARGS", "wrong chain_id")
                if tx["sender"] is not None and not verify_tx_sig(Tx.from_json(tx)):
                    raise Reject("BAD_SIG")
                self.mempool[tx_id(tx)] = tx
                await self.transport.broadcast({"t": "tx", "tx": tx})
                return tx_id(tx)
            if method == "tcl_devSend" and self.dev:
                sender, m, args = params[0], params[1], params[2]
                nonce = self.v.meta("nonces", {}).get(sender, 0) + sum(
                    1 for t in self.mempool.values() if t["sender"] == sender)
                tx = sign_tx({"chain_id": self.chain_id, "module": "circulatory", "method": m,
                              "sender": sender, "nonce": nonce, "args": args, "proof": None,
                              "sig": None, "pow_stamp": 0}, self.dev_keys[sender])
                self.mempool[tx_id(tx)] = tx
                await self.transport.broadcast({"t": "tx", "tx": tx})
                return tx_id(tx)
            if method == "tcl_devAccounts" and self.dev:
                return list(self.dev_keys)
        raise LocalnetError(f"unknown method {method}")

    async def run(self, rpc_port: int) -> None:
        from . import p2p
        loop = asyncio.get_running_loop()
        dial = {k: p for k, p in self.peer_ports.items()
                if self.genesis.validator_ids().index(k) > self.index}
        self.transport = p2p.Transport(self.v.secret, self.chain_id,
                                       set(self.genesis.validator_ids()),
                                       self.peer_ports[self.v.id], dial, self.on_message,
                                       self.on_connect)
        await self.transport.start()
        srv = _rpc_server(self, loop, rpc_port)
        (self.dir / "node.json").write_text(json.dumps(
            {"pid": os.getpid(), "url": f"http://127.0.0.1:{srv.server_port}"}))
        stop = asyncio.Event()
        for s in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(s, stop.set)
        asyncio.ensure_future(self._status_loop())
        loop.call_later(0.5, lambda: asyncio.ensure_future(self._start_height()))
        await stop.wait()
        srv.shutdown()
        self.transport.close()
        self.v.close()
        (self.dir / "node.json").unlink(missing_ok=True)


def _rpc_server(node: ProcNode, loop, port: int) -> HTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            try:
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fut = asyncio.run_coroutine_threadsafe(
                    node.rpc(req["method"], req.get("params") or []), loop)
                out = {"jsonrpc": "2.0", "id": req.get("id"), "result": fut.result(30)}
            except Exception as e:  # noqa: BLE001
                out = {"jsonrpc": "2.0", "id": None,
                       "error": {"code": -32003, "message": str(e)}}
            b = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    srv = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def install_loopback_guard() -> None:
    """The localnet is offline by construction: refuse any non-loopback connection attempt."""
    import ipaddress
    import socket
    real = socket.socket.connect

    def guarded(self, addr):  # type: ignore[no-untyped-def]
        if self.family in (socket.AF_INET, socket.AF_INET6):
            if not ipaddress.ip_address(addr[0]).is_loopback:
                raise OSError(f"localnet is offline-only: refusing {addr}")
        return real(self, addr)
    socket.socket.connect = guarded  # type: ignore[method-assign]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="tcl-localnet-proc")
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--index", required=True, type=int)
    ap.add_argument("--base-port", type=int, default=26650)
    ap.add_argument("--rpc-port", type=int, default=0)
    ap.add_argument("--tick-ms", type=int, default=10)
    ap.add_argument("--block-interval", type=float, default=0.2)
    ap.add_argument("--no-dev", action="store_true")
    a = ap.parse_args(argv)
    install_loopback_guard()
    node = ProcNode(a.data_dir, a.index, a.base_port, a.tick_ms, a.block_interval,
                    dev=not a.no_dev)
    asyncio.run(node.run(a.rpc_port))


if __name__ == "__main__":
    main()
