"""Deterministic in-process network simulator (tests / localnet fault injection). No sockets, no clock.

* Virtual time in integer ticks; every random choice comes from one seeded `random.Random`.
* Partitions: messages between different groups are HELD (not lost) and released on heal(), modelling
  partial synchrony (GST = heal time).
* Byzantine nodes: equivocate - every vote they cast is re-signed for a conflicting value and the two
  versions go to different halves of the network; as proposer they send two different blocks.
"""
from __future__ import annotations

import hashlib
import heapq
import random
from dataclasses import dataclass, field
from typing import Optional

from . import ed25519
from .encoding import merkle_root
from .engine import Broadcast, Decide, ScheduleTimeout, TendermintEngine, Timeouts
from .types import (GENESIS_PREV, Proposal, Vote, block_hash, make_block, make_header,
                    sign_proposal, sign_vote, validators_hash, verify_commit)

CHAIN_ID = "tcl-sim"


def dev_secret(i: int, tag: str = "dev") -> bytes:
    return hashlib.sha256(f"tcl-{tag}-key-{i}".encode()).digest()


class SimApp:
    """Minimal deterministic application: empty-ish blocks, chained hashes, commit verification."""

    def __init__(self, chain_id: str, vals: list[str], txs_for: Optional[dict] = None):
        self.chain_id = chain_id
        self.vals = sorted(vals)
        self.chain: list[dict] = []
        self.commits: list[dict] = []
        self.salt = 0  # byzantine proposers vary this to make conflicting blocks
        self.txs_for = txs_for or {}

    def validators(self, height: int) -> list[str]:
        return self.vals

    def _parent(self):
        return self.chain[-1] if self.chain else None

    def build_block(self, height: int, rnd: int, proposer: str) -> dict:
        parent = self._parent()
        txs = self.txs_for.get(height, [])
        hdr = make_header(chain_id=self.chain_id, height=height,
                          time=(parent["header"]["time"] if parent else 1_700_000_000) + 1 + self.salt,
                          prev_hash=block_hash(parent["header"]) if parent else GENESIS_PREV,
                          tx_root=merkle_root([]) if not txs else merkle_root([t for t in txs]),
                          validators_hash=validators_hash(self.vals),
                          next_validators_hash=validators_hash(self.vals), proposer=proposer)
        if txs:
            return {"header": hdr, "txs": [], "last_commit": self.commits[-1] if self.commits else None}
        return make_block(hdr, [], self.commits[-1] if self.commits else None)

    def validate_block(self, block: dict, height: int) -> bool:
        hd = block["header"]
        parent = self._parent()
        if hd["chain_id"] != self.chain_id or hd["height"] != height:
            return False
        if hd["prev_hash"] != (block_hash(parent["header"]) if parent else GENESIS_PREV):
            return False
        if parent and hd["time"] <= parent["header"]["time"]:
            return False
        if hd["validators_hash"] != validators_hash(self.vals):
            return False
        if parent:
            lc = block["last_commit"]
            if lc is None or lc["block_hash"] != block_hash(parent["header"]) or \
                    not verify_commit(lc, self.chain_id, self.vals):
                return False
        return True

    def on_decide(self, block: dict, commit: dict) -> None:
        self.chain.append(block)
        self.commits.append(commit)


@dataclass(order=True)
class _Ev:
    t: int
    seq: int
    kind: str = field(compare=False)
    node: int = field(compare=False)
    payload: object = field(compare=False)


class Network:
    def __init__(self, n: int, seed: int = 0, byzantine: frozenset = frozenset(),
                 max_delay: int = 5, timeouts: Optional[Timeouts] = None, chain_id: str = CHAIN_ID):
        self.rng = random.Random(seed)
        self.secrets = [dev_secret(i) for i in range(n)]
        self.keys = [ed25519.public_key(s).hex() for s in self.secrets]
        self.byz = set(byzantine)
        self.apps = [SimApp(chain_id, self.keys) for _ in range(n)]
        self.nodes = [TendermintEngine(self.apps[i], self.secrets[i], timeouts) for i in range(n)]
        self.n, self.max_delay, self.chain_id = n, max_delay, chain_id
        self.now, self._seq = 0, 0
        self.q: list[_Ev] = []
        self.groups: Optional[list[set[int]]] = None
        self.held: list[tuple[int, object]] = []
        self.decisions: dict[int, dict[int, str]] = {i: {} for i in range(n)}
        self.delivered = 0

    # ----------------------------------------------------------------------- control
    def partition(self, *groups: set[int]) -> None:
        self.groups = [set(g) for g in groups]

    def heal(self) -> None:
        self.groups = None
        held, self.held = self.held, []
        for dst, msg in held:
            self._push(self.now + self.rng.randint(1, self.max_delay), "msg", dst, msg)

    def start(self) -> None:
        for i, node in enumerate(self.nodes):
            self._handle(i, node.start(1))

    def run_until(self, t: int) -> None:
        while self.q and self.q[0].t <= t:
            ev = heapq.heappop(self.q)
            self.now = ev.t
            node = self.nodes[ev.node]
            if ev.kind == "msg":
                self.delivered += 1
                self._handle(ev.node, node.on_message(ev.payload))
            else:
                h, r, s = ev.payload  # type: ignore[misc]
                self._handle(ev.node, node.on_timeout(h, r, s))
        self.now = max(self.now, t)

    def honest(self) -> list[int]:
        return [i for i in range(self.n) if i not in self.byz]

    def min_honest_height(self) -> int:
        return min(len(self.decisions[i]) for i in self.honest())

    # ----------------------------------------------------------------------- internals
    def _push(self, t: int, kind: str, node: int, payload: object) -> None:
        self._seq += 1
        heapq.heappush(self.q, _Ev(t, self._seq, kind, node, payload))

    def _connected(self, a: int, b: int) -> bool:
        if self.groups is None:
            return True
        return any(a in g and b in g for g in self.groups)

    def _send(self, src: int, dst: int, msg: object) -> None:
        if dst == src:
            return  # engines self-deliver internally
        if not self._connected(src, dst):
            self.held.append((dst, msg))
            return
        self._push(self.now + self.rng.randint(1, self.max_delay), "msg", dst, msg)

    def _handle(self, src: int, actions: list) -> None:
        for a in actions:
            if isinstance(a, ScheduleTimeout):
                self._push(self.now + a.delay, "timeout", src, (a.height, a.round, a.step))
            elif isinstance(a, Decide):
                prev = self.decisions[src].get(a.height)
                assert prev is None, "decided twice at one height"
                self.decisions[src][a.height] = a.commit["block_hash"]
            elif isinstance(a, Broadcast):
                if src in self.byz and not isinstance(a.msg, (Vote, Proposal)):
                    continue  # byzantine nodes don't help others catch up
                if src in self.byz:
                    self._byz_broadcast(src, a.msg)
                else:
                    for d in range(self.n):
                        self._send(src, d, a.msg)

    def _byz_broadcast(self, src: int, msg: object) -> None:
        sk = self.secrets[src]
        others = [d for d in range(self.n) if d != src]
        half = set(self.rng.sample(others, len(others) // 2))
        if isinstance(msg, Vote):
            fake = "%064x" % self.rng.getrandbits(256) if msg.block_hash is not None else None
            if msg.block_hash is None:
                fake = "ab" * 32
            alt = sign_vote(msg.kind, self.chain_id, msg.height, msg.round, fake, sk)
        else:
            assert isinstance(msg, Proposal)
            app = self.apps[src]
            app.salt += 1
            blk = app.build_block(msg.height, msg.round, msg.proposer)
            app.salt -= 1
            alt = sign_proposal(self.chain_id, msg.height, msg.round, msg.pol_round, blk, sk)
        for d in others:
            self._send(src, d, alt if d in half else msg)
