"""Tendermint-style BFT engine (Buchman, Kwon, Milosevic 2018, Alg. 1), equal-weight validators.

Pure, deterministic, event-driven: the host feeds messages/timeouts and executes returned actions.
No I/O, no clock, no threads.  See docs/13b_consensus_engine.md for the swap-in contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Union

from . import ed25519
from .evidence import DuplicateVoteEvidence
from .types import CommitCert, Proposal, Vote, verify_commit, block_hash, make_commit, quorum, sign_proposal, sign_vote

PROPOSE, PREVOTE, PRECOMMIT = "propose", "prevote", "precommit"


# ---------------------------------------------------------------------------------- actions
@dataclass(frozen=True)
class Broadcast:
    msg: Union[Proposal, Vote]


@dataclass(frozen=True)
class ScheduleTimeout:
    height: int
    round: int
    step: str
    delay: int  # abstract ticks (host decides the unit)


@dataclass(frozen=True)
class Decide:
    height: int
    block: Any
    commit: dict

    def __hash__(self) -> int:
        return hash((self.height, self.commit["block_hash"]))


Action = Union[Broadcast, ScheduleTimeout, Decide]


# ---------------------------------------------------------------------------------- host API
class Application(Protocol):
    chain_id: str

    def validators(self, height: int) -> list[str]:
        """Sorted Ed25519 pubkeys (hex) for `height`. Every entry has weight 1."""
        ...

    def build_block(self, height: int, round: int, proposer: str) -> dict: ...

    def validate_block(self, block: dict, height: int) -> bool: ...

    def on_decide(self, block: dict, commit: dict) -> None: ...


class ConsensusEngine(Protocol):
    """The swap-in interface (localnet's round-robin engine and TendermintEngine both satisfy it)."""

    height: int

    def start(self, height: int) -> list[Action]: ...

    def on_message(self, msg: Union[Proposal, Vote]) -> list[Action]: ...

    def on_timeout(self, height: int, round: int, step: str) -> list[Action]: ...


def proposer_for(validators: list[str], height: int, rnd: int) -> str:
    return validators[(height + rnd) % len(validators)]


@dataclass
class Timeouts:
    propose: int = 30
    prevote: int = 20
    precommit: int = 20
    delta: int = 10  # added per round -> eventually exceeds any post-GST delay

    def of(self, step: str, rnd: int) -> int:
        return getattr(self, step) + rnd * self.delta


@dataclass
class _HeightState:
    proposals: dict[int, Proposal] = field(default_factory=dict)
    votes: dict[tuple[str, int], dict[str, Vote]] = field(default_factory=dict)
    fired: set = field(default_factory=set)
    blocks: dict[str, dict] = field(default_factory=dict)  # every validly-signed proposed block


class TendermintEngine:
    def __init__(self, app: Application, secret: bytes, timeouts: Optional[Timeouts] = None):
        self.app = app
        self.secret = secret
        self.me = ed25519.public_key(secret).hex()
        self.t = timeouts or Timeouts()
        self.height = -1
        self.evidence: list[DuplicateVoteEvidence] = []
        self._future: list[Union[Proposal, Vote]] = []
        self.certs: dict[int, CommitCert] = {}
        self._resent: set = set()
        self._out: list[Action] = []

    # ------------------------------------------------------------------ public
    def start(self, height: int) -> list[Action]:
        self._out = []
        self._new_height(height)
        return self._flush()

    def on_message(self, msg: Union[Proposal, Vote]) -> list[Action]:
        self._out = []
        self._ingest(msg)
        return self._flush()

    def on_timeout(self, height: int, rnd: int, step: str) -> list[Action]:
        self._out = []
        if height == self.height and rnd == self.round:
            if step == PROPOSE and self.step == PROPOSE:
                self._vote(PREVOTE, None)
                self.step = PREVOTE
            elif step == PREVOTE and self.step == PREVOTE:
                self._vote(PRECOMMIT, None)
                self.step = PRECOMMIT
            elif step == PRECOMMIT:
                self._start_round(rnd + 1)
            self._check()
        return self._flush()

    # ------------------------------------------------------------------ internals
    def _flush(self) -> list[Action]:
        out, self._out = self._out, []
        return out

    def _new_height(self, h: int) -> None:
        self.height = h
        self.vals = list(self.app.validators(h))
        self.n = len(self.vals)
        self.q = quorum(self.n)
        self.f1 = self.n - self.q + 1  # f+1: at least one honest
        self.hs = _HeightState()
        self.locked_round, self.locked_value = -1, None
        self.valid_round, self.valid_value = -1, None
        self.decided = False
        self.round = -1
        self._start_round(0)
        pending, self._future = self._future, []
        for m in pending:
            self._ingest(m)
        self._check()

    def _start_round(self, r: int) -> None:
        self.round, self.step = r, PROPOSE
        if self.me in self.vals and proposer_for(self.vals, self.height, r) == self.me:
            v = self.valid_value if self.valid_value is not None else \
                self.app.build_block(self.height, r, self.me)
            p = sign_proposal(self.app.chain_id, self.height, r, self.valid_round, v, self.secret)
            self._out.append(Broadcast(p))
            self._record(p)
        else:
            self._out.append(ScheduleTimeout(self.height, r, PROPOSE, self.t.of(PROPOSE, r)))

    def _vote(self, kind: str, bh: Optional[str]) -> None:
        if self.me not in self.vals:
            return
        v = sign_vote(kind, self.app.chain_id, self.height, self.round, bh, self.secret)
        self._out.append(Broadcast(v))
        self._record(v)

    def _ingest(self, m: Union[Proposal, Vote, CommitCert]) -> None:
        if isinstance(m, CommitCert):
            if m.height == self.height and not self.decided:
                self._accept_cert(m)
            elif m.height > self.height:
                self._future.append(m)  # type: ignore[arg-type]
            return
        if m.height > self.height:
            self._future.append(m)
            return
        if m.height < self.height:
            # Peer is behind: help it catch up with our self-certifying commit (once per h/round).
            c = self.certs.get(m.height)
            if c is not None and self._once_global((m.height, m.round)):
                self._out.append(Broadcast(c))  # type: ignore[arg-type]
            return
        if self.decided:
            return
        if self._record(m):
            self._check()

    def _record(self, m: Union[Proposal, Vote]) -> bool:
        cid = self.app.chain_id
        if isinstance(m, Proposal):
            if m.proposer != proposer_for(self.vals, m.height, m.round):
                return False
            if not m.verify(cid):
                return False
            bh = block_hash(m.block["header"])
            new_block = bh not in self.hs.blocks
            self.hs.blocks.setdefault(bh, m.block)
            if m.round in self.hs.proposals:
                return new_block  # equivocating proposer: keep block for decision, vote on first
            self.hs.proposals[m.round] = m
            return True
        if m.validator not in self.vals or m.kind not in (PREVOTE, PRECOMMIT) or not m.verify(cid):
            return False
        box = self.hs.votes.setdefault((m.kind, m.round), {})
        prev = box.get(m.validator)
        if prev is not None:
            if prev.block_hash != m.block_hash:
                ev = DuplicateVoteEvidence(prev, m)
                if all(e.key() != ev.key() for e in self.evidence):
                    self.evidence.append(ev)
            return False  # first vote wins; equivocator gets no extra weight
        box[m.validator] = m
        return True

    def _count(self, kind: str, r: int, bh: Any = "*") -> int:
        box = self.hs.votes.get((kind, r), {})
        if bh == "*":
            return len(box)
        return sum(1 for v in box.values() if v.block_hash == bh)

    def _valid(self, p: Proposal) -> bool:
        return self._valid_block(block_hash(p.block["header"]), p.block)

    def _valid_block(self, bh: str, blk: dict) -> bool:
        cache = self.hs.__dict__.setdefault("valid_cache", {})
        if bh not in cache:
            cache[bh] = bool(self.app.validate_block(blk, self.height))
        return cache[bh]

    def _once_global(self, key: tuple) -> bool:
        if key in self._resent:
            return False
        self._resent.add(key)
        return True

    def _accept_cert(self, c: CommitCert) -> None:
        bh = block_hash(c.block["header"])
        if c.commit["height"] != self.height or c.commit["block_hash"] != bh:
            return
        if not verify_commit(c.commit, self.app.chain_id, self.vals):
            return
        if not self._valid_block(bh, c.block):
            return
        self._finalize(c.block, c.commit)

    def _finalize(self, blk: dict, commit: dict) -> None:
        h = self.height
        self.decided = True
        self.app.on_decide(blk, commit)
        self.certs[h] = CommitCert(h, blk, commit, commit["round"])
        self._out.append(Decide(h, blk, commit))
        self._out.append(Broadcast(self.certs[h]))  # type: ignore[arg-type]
        self._new_height(h + 1)

    def _once(self, key: tuple) -> bool:
        if key in self.hs.fired:
            return False
        self.hs.fired.add(key)
        return True

    def _check(self) -> None:  # noqa: C901  (mirrors the paper's upon-rules)
        changed = True
        while changed and not self.decided:
            changed = False
            h, r = self.height, self.round
            # L49: decide in any round
            for (kind, pr), box in sorted(self.hs.votes.items()):
                if kind != PRECOMMIT:
                    continue
                tally: dict[str, int] = {}
                for v in box.values():
                    if v.block_hash is not None:
                        tally[v.block_hash] = tally.get(v.block_hash, 0) + 1
                bh = next((b for b, c in sorted(tally.items()) if c >= self.q), None)
                if bh is None or bh not in self.hs.blocks:
                    continue
                blk = self.hs.blocks[bh]
                if not self._valid_block(bh, blk):
                    continue
                pcs = [v for v in box.values() if v.block_hash == bh]
                self._finalize(blk, make_commit(h, pr, bh, pcs))
                return
            p = self.hs.proposals.get(r)
            if p is not None and self.step == PROPOSE:
                bh = block_hash(p.block["header"])
                if p.pol_round == -1:  # L22
                    ok = self._valid(p) and (self.locked_round == -1 or self.locked_value_hash() == bh)
                    self._vote(PREVOTE, bh if ok else None)
                    self.step = PREVOTE
                    changed = True
                elif 0 <= p.pol_round < r and self._count(PREVOTE, p.pol_round, bh) >= self.q:  # L28
                    ok = self._valid(p) and (self.locked_round <= p.pol_round or
                                             self.locked_value_hash() == bh)
                    self._vote(PREVOTE, bh if ok else None)
                    self.step = PREVOTE
                    changed = True
            if self.step == PREVOTE and self._count(PREVOTE, r) >= self.q and \
                    self._once(("tprevote", r)):  # L34
                self._out.append(ScheduleTimeout(h, r, PREVOTE, self.t.of(PREVOTE, r)))
            if p is not None and self.step in (PREVOTE, PRECOMMIT):  # L36
                bh = block_hash(p.block["header"])
                if self._count(PREVOTE, r, bh) >= self.q and self._valid(p) and self._once(("l36", r)):
                    if self.step == PREVOTE:
                        self.locked_value, self.locked_round = p.block, r
                        self._vote(PRECOMMIT, bh)
                        self.step = PRECOMMIT
                    self.valid_value, self.valid_round = p.block, r
                    changed = True
            if self.step == PREVOTE and self._count(PREVOTE, r, None) >= self.q:  # L44
                self._vote(PRECOMMIT, None)
                self.step = PRECOMMIT
                changed = True
            if self._count(PRECOMMIT, r) >= self.q and self._once(("tprecommit", r)):  # L47
                self._out.append(ScheduleTimeout(h, r, PRECOMMIT, self.t.of(PRECOMMIT, r)))
            # L55: f+1 distinct senders in a higher round -> skip ahead
            higher: dict[int, set[str]] = {}
            for (kind, vr), box in self.hs.votes.items():
                if vr > r:
                    higher.setdefault(vr, set()).update(box)
            for pr2, pp in self.hs.proposals.items():
                if pr2 > r:
                    higher.setdefault(pr2, set()).add(pp.proposer)
            for vr in sorted(higher):
                if len(higher[vr]) >= self.f1:
                    self._start_round(vr)
                    changed = True
                    break

    def locked_value_hash(self) -> Optional[str]:
        return None if self.locked_value is None else block_hash(self.locked_value["header"])
