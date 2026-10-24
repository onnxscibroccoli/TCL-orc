"""Localhost TCP peer-to-peer transport for multi-process localnets. Stdlib only.

Wire format: every frame is a 4-byte big-endian length followed by canonical JSON (max 4 MiB).

Authentication (mutual, Ed25519 node keys):
  1. both sides send HELLO {chain_id, key, nonce(32 random bytes)}; a peer whose key is not in the
     allowed set (the genesis validator keys), or whose chain_id differs, is dropped.
  2. both sides send AUTH = Sign(sk, "tcl-p2p-auth" | chain_id | my_nonce | peer_nonce | my_key |
     peer_key) and verify the peer's. Fresh nonces on both sides prevent handshake replay.
  3. every later frame is {seq, body, sig} with sig = Sign(sk, session_id | seq | body); the
     receiver requires seq == expected (no replay/reorder/drop) and a valid signature.
     session_id = SHA256(sorted nonces).

Encryption: NONE. Frames are authenticated and integrity-protected but readable by anyone who can
observe loopback traffic. Acceptable for a localhost dev network; a real network needs an
authenticated key exchange (e.g. Noise XX over X25519) + AEAD, which is out of scope here.
CAVEAT: the in-house Ed25519 is not constant-time (see consensus/ed25519.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import struct
from typing import Any, Awaitable, Callable, Optional

from ..consensus import ed25519

MAX_FRAME = 4 << 20


class AuthError(Exception):
    pass


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


async def read_frame(r: asyncio.StreamReader) -> Any:
    hdr = await r.readexactly(4)
    (n,) = struct.unpack(">I", hdr)
    if n > MAX_FRAME:
        raise AuthError("frame too large")
    return json.loads(await r.readexactly(n))


def write_frame(w: asyncio.StreamWriter, obj: Any) -> None:
    b = _canon(obj)
    if len(b) > MAX_FRAME:
        raise ValueError("frame too large")
    w.write(struct.pack(">I", len(b)) + b)


def _auth_msg(chain_id: str, n1: str, n2: str, k1: str, k2: str) -> bytes:
    return _canon(["tcl-p2p-auth", chain_id, n1, n2, k1, k2])


class Peer:
    """An authenticated connection. send() signs; recv() verifies."""

    def __init__(self, r, w, sk: bytes, me: str, peer: str, sid: str) -> None:
        self.r, self.w, self.sk, self.me, self.key, self.sid = r, w, sk, me, peer, sid
        self.tx_seq = self.rx_seq = 0

    async def send(self, body: Any) -> None:
        sig = ed25519.sign(self.sk, _canon([self.sid, self.tx_seq, body])).hex()
        write_frame(self.w, {"seq": self.tx_seq, "body": body, "sig": sig})
        self.tx_seq += 1
        await self.w.drain()

    async def recv(self) -> Any:
        f = await read_frame(self.r)
        if not isinstance(f, dict) or f.get("seq") != self.rx_seq:
            raise AuthError("bad sequence")
        if not ed25519.verify(bytes.fromhex(self.key), _canon([self.sid, f["seq"], f.get("body")]),
                              bytes.fromhex(f.get("sig") or "")):
            raise AuthError("bad frame signature")
        self.rx_seq += 1
        return f["body"]

    def close(self) -> None:
        try:
            self.w.close()
        except Exception:  # noqa: BLE001
            pass


async def handshake(r, w, sk: bytes, chain_id: str, allowed: set[str],
                    timeout: float = 5.0) -> Peer:
    me = ed25519.public_key(sk).hex()
    nonce = os.urandom(32).hex()
    write_frame(w, {"t": "hello", "chain_id": chain_id, "key": me, "nonce": nonce})
    await w.drain()
    h = await asyncio.wait_for(read_frame(r), timeout)
    if not isinstance(h, dict) or h.get("t") != "hello" or h.get("chain_id") != chain_id:
        raise AuthError("bad hello / chain_id")
    pk, pn = h.get("key"), h.get("nonce")
    if pk not in allowed or pk == me or not isinstance(pn, str) or len(pn) != 64:
        raise AuthError("peer key not allowed")
    write_frame(w, {"t": "auth", "sig": ed25519.sign(sk, _auth_msg(chain_id, nonce, pn, me,
                                                                   pk)).hex()})
    await w.drain()
    a = await asyncio.wait_for(read_frame(r), timeout)
    try:
        ok = ed25519.verify(bytes.fromhex(pk), _auth_msg(chain_id, pn, nonce, pk, me),
                            bytes.fromhex(a.get("sig") or ""))
    except (AttributeError, ValueError):
        ok = False
    if not ok:
        raise AuthError("bad auth signature")
    sid = hashlib.sha256("".join(sorted([nonce, pn])).encode()).hexdigest()
    return Peer(r, w, sk, me, pk, sid)


class Transport:
    """Listens on 127.0.0.1:port, dials `dial` peers with automatic reconnect, and delivers
    verified message bodies to `on_message(peer_key, body)`."""

    def __init__(self, sk: bytes, chain_id: str, allowed: set[str], port: int,
                 dial: dict[str, int], on_message: Callable[[str, Any], Awaitable[None]],
                 on_connect: Optional[Callable[[str], Awaitable[None]]] = None) -> None:
        self.sk, self.chain_id, self.allowed, self.port = sk, chain_id, allowed, port
        self.dial, self.on_message, self.on_connect = dial, on_message, on_connect
        self.peers: dict[str, Peer] = {}
        self.rejected = 0
        self._server = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", self.port)
        for key, port in self.dial.items():
            asyncio.ensure_future(self._dial_loop(key, port))

    async def _accept(self, r, w) -> None:
        try:
            p = await handshake(r, w, self.sk, self.chain_id, self.allowed)
        except (AuthError, asyncio.IncompleteReadError, asyncio.TimeoutError, OSError,
                ValueError):
            self.rejected += 1
            w.close()
            return
        await self._run(p)

    async def _dial_loop(self, key: str, port: int) -> None:
        while True:
            if key not in self.peers:
                try:
                    r, w = await asyncio.open_connection("127.0.0.1", port)
                    p = await handshake(r, w, self.sk, self.chain_id, {key})
                    asyncio.ensure_future(self._run(p))
                except (AuthError, OSError, asyncio.IncompleteReadError, asyncio.TimeoutError,
                        ValueError):
                    pass
            await asyncio.sleep(0.2)

    async def _run(self, p: Peer) -> None:
        old = self.peers.get(p.key)
        if old is not None:
            old.close()
        self.peers[p.key] = p
        try:
            if self.on_connect:
                await self.on_connect(p.key)
            while True:
                body = await p.recv()
                await self.on_message(p.key, body)
        except (AuthError, asyncio.IncompleteReadError, OSError, ValueError):
            pass
        finally:
            if self.peers.get(p.key) is p:
                del self.peers[p.key]
            p.close()

    async def send(self, key: str, body: Any) -> None:
        p = self.peers.get(key)
        if p is None:
            return
        try:
            await p.send(body)
        except (OSError, ConnectionError):
            p.close()

    async def broadcast(self, body: Any) -> None:
        for k in list(self.peers):
            await self.send(k, body)

    def close(self) -> None:
        if self._server:
            self._server.close()
        for p in list(self.peers.values()):
            p.close()
