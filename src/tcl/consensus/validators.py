"""Validator set rules (13a): personhood sortition, one seat per nullifier, flat bond, fixed term +
cooldown, equal weight, double-sign -> slash + permanent ban. Pure & deterministic (no floats/clock/IO)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .evidence import DuplicateVoteEvidence
from .personhood import PersonhoodProvider


class Reject(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(f"{code}: {msg}")
        self.code = code


@dataclass(frozen=True)
class ValidatorParams:
    N_VALIDATORS: int = 100
    EPOCH_BLOCKS: int = 100
    TERM_EPOCHS: int = 4
    COOLDOWN_EPOCHS: int = 4
    VALIDATOR_BOND: int = 1_000
    MIN_TIER: int = 2


@dataclass
class Candidate:
    nullifier: str
    key: str  # Ed25519 pubkey hex
    bond: int


def draw_without_replacement(seed: bytes, items: list[str], k: int) -> list[str]:
    """Deterministic unbiased sample (rejection sampling on SHA-256 stream, partial Fisher-Yates)."""
    pool = sorted(items)
    out: list[str] = []
    ctr = 0
    while pool and len(out) < k:
        m = len(pool)
        bound = (1 << 256) - ((1 << 256) % m)
        while True:
            x = int.from_bytes(hashlib.sha256(seed + ctr.to_bytes(8, "big")).digest(), "big")
            ctr += 1
            if x < bound:
                break
        out.append(pool.pop(x % m))
    return out


@dataclass
class ValidatorRegistry:
    personhood: PersonhoodProvider
    params: ValidatorParams = field(default_factory=ValidatorParams)
    candidates: dict[str, Candidate] = field(default_factory=dict)   # nullifier -> candidate
    seats: dict[str, int] = field(default_factory=dict)              # nullifier -> start epoch
    cooldown_until: dict[str, int] = field(default_factory=dict)     # nullifier -> epoch
    banned: set[str] = field(default_factory=set)
    slashed_pool: int = 0
    _evidence_seen: set = field(default_factory=set)
    epoch: int = -1

    # --- enrollment -------------------------------------------------------------------------
    def opt_in(self, nullifier: str, key: str, bond: int) -> None:
        p = self.params
        if nullifier in self.banned:
            raise Reject("UNAUTHORIZED", "nullifier permanently banned")
        if not self.personhood.is_enrolled(nullifier) or self.personhood.tier(nullifier) < p.MIN_TIER:
            raise Reject("TIER_LIMIT", "personhood tier below minimum")
        if bond != p.VALIDATOR_BOND:
            raise Reject("BAD_ARGS", "bond must equal the flat VALIDATOR_BOND")
        for n, c in self.candidates.items():
            if c.key == key and n != nullifier:
                raise Reject("BAD_ARGS", "key already bound to another person")
        # One entry per nullifier: re-opting with a new key REPLACES the key, never adds a seat.
        self.candidates[nullifier] = Candidate(nullifier, key, bond)

    # --- selection --------------------------------------------------------------------------
    def eligible(self, epoch: int) -> list[str]:
        return sorted(n for n, c in self.candidates.items()
                      if n not in self.banned and n not in self.seats
                      and self.cooldown_until.get(n, -1) <= epoch
                      and c.bond == self.params.VALIDATOR_BOND
                      and self.personhood.tier(n) >= self.params.MIN_TIER)

    def advance_epoch(self, epoch: int, rand: bytes) -> list[str]:
        """Expire terms, then refill to N_VALIDATORS by seeded draw. Returns sorted validator keys."""
        if epoch <= self.epoch:
            raise Reject("BAD_ARGS", "epoch must increase")
        p = self.params
        for n, start in sorted(self.seats.items()):
            if epoch - start >= p.TERM_EPOCHS:
                del self.seats[n]
                self.cooldown_until[n] = epoch + p.COOLDOWN_EPOCHS
        free = p.N_VALIDATORS - len(self.seats)
        seed = hashlib.sha256(b"tcl-sortition" + epoch.to_bytes(8, "big") + rand).digest()
        for n in draw_without_replacement(seed, self.eligible(epoch), free):
            self.seats[n] = epoch
        self.epoch = epoch
        return self.validator_keys()

    def validator_keys(self) -> list[str]:
        return sorted(self.candidates[n].key for n in self.seats)

    def weight(self, key: str) -> int:
        return 1 if key in self.validator_keys() else 0

    def nullifier_of(self, key: str) -> str | None:
        for n, c in self.candidates.items():
            if c.key == key:
                return n
        return None

    # --- slashing ---------------------------------------------------------------------------
    def submit_evidence(self, ev: DuplicateVoteEvidence, chain_id: str) -> str:
        if ev.key() in self._evidence_seen:
            raise Reject("NULLIFIER_SPENT", "evidence already processed")
        offender_key = ev.verify(chain_id)
        n = self.nullifier_of(offender_key)
        if n is None:
            raise Reject("BAD_ARGS", "unknown validator")
        self._evidence_seen.add(ev.key())
        c = self.candidates.pop(n)
        self.slashed_pool += c.bond
        self.seats.pop(n, None)
        self.banned.add(n)
        return n
