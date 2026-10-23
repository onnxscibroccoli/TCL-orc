"""Reference model of reusable bearer notes (docs/11 §4, §6, §7).

Mirrors contracts/physical/NoteController.sol (state machine, nullifiers,
burn-to-mint credit, epoch budget, gradual re-entry unlock) and adds the
ledger-side accelerated, velocity-weighted idle decay of §6.4, which is NOT
on-chain in the MVP. Integer arithmetic only; every rule fails closed by
raising :class:`NoteError`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .demurrage_fixed import LAMBDA_SCALED, SCALE, _taylor_multiplier

DENOMS = (5, 10, 20, 50, 100, 200, 500)
MAX_DENOM = 500
MAX_REWARD_BPS = 100
MAX_COST_BPS = 2000
DAY = 86_400


class NoteError(ValueError):
    pass


class State(Enum):
    NONE = 0
    LIVE = 1
    DORMANT = 2


@dataclass
class Note:
    denom: int
    era: int = 0
    state: State = State.LIVE


@dataclass
class Bucket:
    window_start: int = 0
    t0: int = 0
    window_amount: int = 0
    excess: int = 0
    released: int = 0
    outflow: int = 0  # qualifying outbound transfers (velocity numerator)
    decayed: int = 0  # accelerated-decay penalty sent to the vault


@dataclass
class NoteRegistry:
    unit: int = SCALE
    reward_bps: int = 100
    epoch_length: int = DAY
    epoch_budget: int = 1_000 * SCALE
    theta: int = 5_000_000 * SCALE
    agg_window: int = 30 * DAY
    release_window: int = 30 * DAY
    pool: int = 0
    physical: int = 0
    locked_total: int = 0
    vault: int = 0
    epoch: int = -1
    spent: int = 0
    cost_cap: dict[int, int] = field(default_factory=dict)
    notes: dict[int, Note] = field(default_factory=dict)
    nullifiers: set[int] = field(default_factory=set)
    buckets: dict[str, Bucket] = field(default_factory=dict)
    balances: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.reward_bps <= MAX_REWARD_BPS:
            raise NoteError("reward_bps out of bounds")
        for d in DENOMS:
            self.cost_cap.setdefault(d, d * self.unit * 5 // 1000)
        for d, cap in self.cost_cap.items():
            if d not in DENOMS or d > MAX_DENOM or cap * 10_000 > d * self.unit * MAX_COST_BPS:
                raise NoteError("cost cap out of bounds")

    # -- helpers
    def face(self, d: int) -> int:
        if d not in DENOMS:
            raise NoteError(f"denomination {d} not allowed")
        return d * self.unit

    def _debit(self, who: str, amt: int) -> None:
        if self.balances.get(who, 0) < amt:
            raise NoteError("insufficient balance")
        self.balances[who] -= amt

    def _credit(self, who: str, amt: int) -> None:
        self.balances[who] = self.balances.get(who, 0) + amt

    def total(self) -> int:
        return sum(self.balances.values()) + self.pool + self.physical + self.locked_total + self.vault

    def fund_pool(self, who: str, amt: int) -> None:
        self._debit(who, amt)
        self.pool += amt

    # -- §7 mint / re-arm
    def quote(self, d: int, attested: int, now: int) -> tuple[int, int]:
        v = self.face(d)
        cost = min(max(attested, 0), self.cost_cap[d])
        reward = v * self.reward_bps // 10_000
        spent = self.spent if now // self.epoch_length == self.epoch else 0
        if cost + reward > self.pool or spent + cost + reward > self.epoch_budget:
            return 0, 0
        return cost, reward

    def _burn_and_credit(self, who: str, d: int, attested: int, now: int) -> tuple[int, int]:
        if now // self.epoch_length != self.epoch:
            self.epoch, self.spent = now // self.epoch_length, 0
        cost, reward = self.quote(d, attested, now)
        self._debit(who, self.face(d))
        self.physical += self.face(d)
        self.pool -= cost + reward
        self.spent += cost + reward
        self._credit(who, cost + reward)
        return cost, reward

    def mint(self, who: str, nid: int, d: int, attested: int, now: int) -> tuple[int, int]:
        if nid <= 0 or nid in self.notes:
            raise NoteError("bad or existing note id")
        self.face(d)
        out = self._burn_and_credit(who, d, attested, now)
        self.notes[nid] = Note(d)
        return out

    def rearm(self, who: str, nid: int, attested: int, now: int) -> tuple[int, int]:
        n = self.notes.get(nid)
        if n is None or n.state is not State.DORMANT:
            raise NoteError("only DORMANT notes can be re-armed")
        out = self._burn_and_credit(who, n.denom, attested, now)
        n.era += 1
        n.state = State.LIVE
        return out

    # -- §4.4 deposit + §6.1/6.2 re-entry
    def deposit(self, nid: int, era: int, nf: int, expected_nf: int, to: str, now: int) -> tuple[int, int]:
        n = self.notes.get(nid)
        if n is None or n.state is not State.LIVE:
            raise NoteError("note not LIVE")
        if era != n.era or nf != expected_nf:
            raise NoteError("era/nullifier mismatch")
        if nf in self.nullifiers:
            raise NoteError("nullifier already used")
        self.nullifiers.add(nf)
        n.state = State.DORMANT
        v = self.face(n.denom)
        self.physical -= v
        b = self.buckets.setdefault(to, Bucket())
        if now >= b.window_start + self.agg_window:
            b.window_start, b.window_amount = now, 0
        prev = b.window_amount
        b.window_amount += v
        locked = max(0, b.window_amount - self.theta) - max(0, prev - self.theta)
        immediate = v - locked
        if locked:
            immediate += self._release(to, now)
            remaining = b.excess - b.released
            b.excess, b.released, b.t0 = remaining + locked, 0, now
            b.outflow, b.decayed = 0, 0
            self.locked_total += locked
        self._credit(to, immediate)
        return immediate, locked

    def unlocked(self, who: str, now: int) -> int:
        b = self.buckets.get(who)
        if not b or b.excess == 0:
            return 0
        dt = now - b.t0
        vested = b.excess if dt >= self.release_window else b.excess * dt // self.release_window
        return max(0, vested - b.released - b.decayed)

    def _release(self, who: str, now: int) -> int:
        amt = self.unlocked(who, now)
        if amt:
            self.buckets[who].released += amt
            self.locked_total -= amt
        return amt

    def release(self, who: str, now: int) -> int:
        amt = self._release(who, now)
        self._credit(who, amt)
        return amt

    def record_outflow(self, who: str, amt: int) -> None:
        """Qualifying outbound transfer (12h hold, fresh counterparty: checked by caller)."""
        if amt < 0:
            raise NoteError("negative outflow")
        b = self.buckets.get(who)
        if b:
            b.outflow += amt

    # -- §6.4 accelerated, velocity-weighted idle decay (ledger-side only)
    def velocity_scaled(self, who: str, now: int) -> int:
        b = self.buckets[who]
        unlocked_ex = min(b.excess, b.excess * max(0, now - b.t0) // self.release_window)
        denom = max(self.unit, unlocked_ex)
        return min(SCALE, b.outflow * SCALE // denom)

    def apply_bucket_decay(self, who: str, t_from: int, t_to: int, *, kappa: int = 9,
                           grace: int = 7 * DAY, horizon: int = 90 * DAY) -> int:
        """Decay the still-locked bucket at lambda_h; penalty goes to the vault."""
        if not 0 <= kappa <= 20:
            raise NoteError("kappa out of bounds [0, 20]")
        if t_to < t_from:
            raise NoteError("time goes backwards")
        b = self.buckets.get(who)
        if not b or b.excess == 0:
            return 0
        penalty, t = 0, t_from
        while t < t_to:
            step = min(DAY, t_to - t)
            tau = t - b.t0
            ramp = min(SCALE, max(0, tau - grace) * SCALE // horizon)
            v = self.velocity_scaled(who, t)
            lam_h = LAMBDA_SCALED * (SCALE + kappa * ramp * (SCALE - v) // SCALE) // SCALE
            locked = b.excess - b.released - b.decayed
            mult = _taylor_multiplier(lam_h * step)
            p = locked - locked * mult // SCALE
            b.decayed += p
            penalty += p
            t += step
        self.locked_total -= penalty
        self.vault += penalty
        return penalty
