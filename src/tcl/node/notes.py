"""Native notes module: port of contracts/physical/NoteController.sol semantics (no EVM).

State machine per note id: NONE -> LIVE (mint) -> DORMANT (deposit w/ proof) -> LIVE (rearm, era+1).
Each deposit spends an era-bound nullifier proven by a Groth16 note_deposit proof whose public
signals are [id, denom, era, context, recipient, nullifier]; old-era proofs fail because `era` is
bound. Mint/rearm burn face value into module escrow and credit min(attestedCost, costCap[d])
+ face*rewardBps/1e4 from the incentive pool, falling back to a pure 1:1 swap (credit 0) if the
pool or the per-epoch budget cannot cover it. Damaged notes are retired OFF-chain only (no
on-chain retirement path, by owner decision).

Conservation invariant (tested): escrow == physical_outstanding + pool + locked_total, and
bank_total + escrow is constant across every operation.
Fail-closed: every check raises NoteError before any state is mutated.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

from .bn254 import R as FIELD_P

MAX_DENOM = 500
MAX_REWARD_BPS = 100
MAX_COST_BPS = 5000          # gamma_c <= 50% of face (hard)
MAX_CREDIT_BPS = 6000        # cost + reward <= 60% of face per mint (hard)
MAX_SMALL_RESERVE_BPS = 8000
SMALL_DENOM_MAX = 20         # 5, 10, 20 are "small everyday notes"
MAX_COST_ABS_FACE = 10
MAX_SMALL_BONUS_FACE = 1
MIN_CIRC_LOW, MIN_CIRC_HIGH = 86_400, 90 * 86_400
DAY = 86_400
MIN_WINDOW, MAX_WINDOW = DAY, 365 * DAY
DEFAULT_DENOMS = (5, 10, 20, 50, 100, 200, 500)
LIVE, DORMANT = "LIVE", "DORMANT"


class NoteError(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(f"{code}: {msg}" if msg else code)
        self.code = code


class Bank(Protocol):
    def debit(self, acct: str, amount: int) -> None: ...   # raise if insufficient
    def credit(self, acct: str, amount: int) -> None: ...


def default_recipient_field(acct) -> int:
    if isinstance(acct, int) and not isinstance(acct, bool):
        return acct
    return int.from_bytes(hashlib.sha256(b"tcl-recipient" + str(acct).encode()).digest(),
                          "big") % FIELD_P


@dataclass
class NotesState:
    unit: int
    context: int
    denoms: dict = field(default_factory=dict)     # str(d) -> [allowed, cost_cap]
    notes: dict = field(default_factory=dict)      # str(id) -> {denom, era, mint_time, state}
    nullifiers: list = field(default_factory=list)
    buckets: dict = field(default_factory=dict)    # acct -> {ws, t0, wa, excess, released}
    physical_outstanding: int = 0
    pool: int = 0
    locked_total: int = 0
    escrow: int = 0
    reward_bps: int = 100
    epoch_length: int = DAY
    epoch_budget: int = 0
    current_epoch: int = 0
    spent_in_epoch: int = 0
    spent_large_in_epoch: int = 0
    # cost-credit policy (PR #20 / docs/11 §7.2); in-chain it is overwritten from governance
    policy: dict = field(default_factory=dict)
    theta: int = 0
    agg_window: int = 30 * DAY
    release_window: int = 30 * DAY

    def to_json(self) -> dict:
        d = asdict(self)
        d["nullifiers"] = sorted(self.nullifiers)
        return d


class NotesModule:
    def __init__(self, state: NotesState, bank: Bank,
                 verify_proof: Callable[[dict, list], bool],
                 recipient_field: Callable = default_recipient_field) -> None:
        self.s, self.bank, self._verify, self._rf = state, bank, verify_proof, recipient_field
        self._nset = set(state.nullifiers)

    @classmethod
    def genesis(cls, unit: int, context: int, bank: Bank, verify_proof, **kw) -> "NotesModule":
        s = NotesState(unit=unit, context=context)
        m = cls(s, bank, verify_proof, **kw)
        for d in DEFAULT_DENOMS:
            m.set_denomination(d, True, d * unit * MAX_COST_BPS // 10_000)  # as contract (#20)
        m.set_params(100, DAY, 1_000 * unit, 5_000_000 * unit, 30 * DAY, 30 * DAY)
        m.set_cost_policy(4000, 3 * unit, unit // 4, 5000, 7 * DAY, reward_bps=20)
        return m

    def set_cost_policy(self, cost_bps: int, cost_abs: int, bonus: int, reserve_bps: int,
                        min_circ: int, reward_bps: int | None = None) -> None:
        u = self.s.unit
        if not (0 <= cost_bps <= MAX_COST_BPS and 0 <= cost_abs <= MAX_COST_ABS_FACE * u and
                0 <= bonus <= MAX_SMALL_BONUS_FACE * u and 0 <= reserve_bps <=
                MAX_SMALL_RESERVE_BPS and MIN_CIRC_LOW <= min_circ <= MIN_CIRC_HIGH):
            raise NoteError("OUT_OF_BOUNDS", "cost policy")
        rb = self.s.reward_bps if reward_bps is None else reward_bps
        if not 0 <= rb <= MAX_REWARD_BPS:
            raise NoteError("OUT_OF_BOUNDS", "reward")
        self.s.policy = {"reward_bps": rb, "cost_bps": cost_bps, "cost_abs": cost_abs,
                         "bonus": bonus, "reserve_bps": reserve_bps, "min_circ": min_circ}

    def policy(self) -> dict:
        p = self.s.policy
        if not p:  # fail closed: no policy -> zero credit, zero reward (pure 1:1)
            return {"reward_bps": 0, "cost_bps": 0, "cost_abs": 0, "bonus": 0,
                    "reserve_bps": 0, "min_circ": MIN_CIRC_HIGH}
        return p

    # --- params (governance-only callers) --------------------------------------------------
    def set_denomination(self, d: int, allowed: bool, cap: int) -> None:
        if not isinstance(d, int) or d <= 0 or d > MAX_DENOM:
            raise NoteError("BAD_DENOM")
        if cap < 0 or cap * 10_000 > d * self.s.unit * MAX_COST_BPS:
            raise NoteError("OUT_OF_BOUNDS", "cost cap above hard bound")
        self.s.denoms[str(d)] = [bool(allowed), cap]

    def set_params(self, r, el, eb, th, wa, wr) -> None:
        if not 0 <= r <= MAX_REWARD_BPS or not 3600 <= el <= MAX_WINDOW or eb < 0:
            raise NoteError("OUT_OF_BOUNDS")
        if not (MIN_WINDOW <= wa <= MAX_WINDOW and MIN_WINDOW <= wr <= MAX_WINDOW):
            raise NoteError("OUT_OF_BOUNDS")
        if th <= 0 or th >= 1 << 128:
            raise NoteError("OUT_OF_BOUNDS")
        s = self.s
        s.reward_bps, s.epoch_length, s.epoch_budget = r, el, eb
        s.theta, s.agg_window, s.release_window = th, wa, wr

    def fund_pool(self, acct: str, amount: int) -> None:
        _pos(amount)
        self.bank.debit(acct, amount)
        self.s.pool += amount
        self.s.escrow += amount

    # --- mint / rearm ------------------------------------------------------------------------
    def face(self, d: int) -> int:
        return d * self.s.unit

    def quote(self, d: int, attested: int, now: int) -> tuple[int, int]:
        return self._quote(d, attested, False, True, now)

    def quote_rearm(self, nid: int, now: int) -> tuple[int, int]:
        n = self.s.notes[str(nid)]
        eligible = now >= n["mint_time"] + self.policy()["min_circ"]
        return self._quote(n["denom"], 0, True, eligible, now)

    def _quote(self, d: int, attested: int, is_rearm: bool, eligible: bool,
               now: int) -> tuple[int, int]:
        """Port of NoteController._quote (PR #20): credit = min(claimed, cap_d, gamma*V, c_abs);
        reward = rho*V (+ b_small on first mint of 5/10/20); total <= 60% V; re-arm: no cost
        credit, reward only if the era circulated >= min_circ; 1:1 fallback if the pool, the
        epoch budget, or (for large notes) the non-reserved budget share can't cover it."""
        s, p = self.s, self.policy()
        v = self.face(d)
        small = d <= SMALL_DENOM_MAX
        cost = reward = 0
        if not is_rearm:
            cost = min(attested, s.denoms[str(d)][1], v * p["cost_bps"] // 10_000,
                       p["cost_abs"])
        if eligible:
            reward = v * p["reward_bps"] // 10_000
            if small and not is_rearm:
                reward += p["bonus"]
        mx = v * MAX_CREDIT_BPS // 10_000
        if cost + reward > mx:
            reward = 0 if cost >= mx else mx - cost
            cost = min(cost, mx)
        total = cost + reward
        if total == 0:
            return 0, 0
        same = now // s.epoch_length == s.current_epoch
        spent = s.spent_in_epoch if same else 0
        if total > s.pool or spent + total > s.epoch_budget:
            return 0, 0
        if not small:
            spent_l = s.spent_large_in_epoch if same else 0
            if spent_l + total > s.epoch_budget * (10_000 - p["reserve_bps"]) // 10_000:
                return 0, 0
        return cost, reward

    def mint(self, minter: str, nid: int, d: int, attested: int, now: int) -> tuple[int, int]:
        if not isinstance(nid, int) or nid <= 0 or nid >= FIELD_P:
            raise NoteError("BAD_ID")
        if str(nid) in self.s.notes:
            raise NoteError("BAD_STATE")
        if not self.s.denoms.get(str(d), [False])[0]:
            raise NoteError("BAD_DENOM")
        if not isinstance(attested, int) or attested < 0:
            raise NoteError("BAD_ARGS")
        cost, reward = self.quote(d, attested, now)
        self._burn_and_credit(minter, d, cost, reward, now)
        self.s.notes[str(nid)] = {"denom": d, "era": 0, "mint_time": now, "state": LIVE}
        return cost, reward

    def rearm(self, minter: str, nid: int, attested: int, now: int) -> tuple[int, int]:
        n = self.s.notes.get(str(nid))
        if n is None or n["state"] != DORMANT:
            raise NoteError("BAD_STATE")
        if not self.s.denoms.get(str(n["denom"]), [False])[0]:
            raise NoteError("BAD_DENOM")
        cost, reward = self.quote_rearm(nid, now)  # previous era's mint_time; claim ignored
        self._burn_and_credit(minter, n["denom"], cost, reward, now)
        n.update(era=n["era"] + 1, state=LIVE, mint_time=now)
        return cost, reward

    def _burn_and_credit(self, minter: str, d: int, cost: int, reward: int, now: int) -> None:
        s = self.s
        v = self.face(d)
        self.bank.debit(minter, v)  # raises before any module mutation if insufficient
        e = now // s.epoch_length
        if e != s.current_epoch:
            s.current_epoch, s.spent_in_epoch, s.spent_large_in_epoch = e, 0, 0
        credit = cost + reward
        s.physical_outstanding += v
        s.escrow += v
        if credit:
            s.pool -= credit
            s.spent_in_epoch += credit
            if d > SMALL_DENOM_MAX:
                s.spent_large_in_epoch += credit
            s.escrow -= credit
            self.bank.credit(minter, credit)

    # --- deposit -----------------------------------------------------------------------------
    def deposit(self, nid: int, nf: int, recipient, proof: dict, now: int) -> tuple[int, int]:
        s = self.s
        n = s.notes.get(str(nid))
        if n is None or n["state"] != LIVE:
            raise NoteError("BAD_STATE")
        if not isinstance(nf, int) or nf <= 0 or nf >= FIELD_P:
            raise NoteError("BAD_PUBLIC_INPUTS")
        if nf in self._nset:
            raise NoteError("NULLIFIER_SPENT")
        pub = [nid, n["denom"], n["era"], s.context, self._rf(recipient), nf]
        if not self._verify(proof, pub):
            raise NoteError("BAD_PROOF")
        s.nullifiers.append(nf)
        self._nset.add(nf)
        n["state"] = DORMANT
        v = self.face(n["denom"])
        s.physical_outstanding -= v
        immediate, locked = self._reentry(str(recipient), v, now)
        s.escrow -= immediate
        if immediate:
            self.bank.credit(str(recipient), immediate)
        return immediate, locked

    def unlocked(self, acct: str, now: int) -> int:
        b = self.s.buckets.get(acct)
        if not b or b["excess"] == 0:
            return 0
        dt = now - b["t0"]
        vested = b["excess"] if dt >= self.s.release_window else \
            b["excess"] * dt // self.s.release_window
        return vested - b["released"]

    def _release(self, acct: str, now: int) -> int:
        amt = self.unlocked(acct, now)
        if amt:
            self.s.buckets[acct]["released"] += amt
            self.s.locked_total -= amt
        return amt

    def release(self, acct: str, now: int) -> int:
        amt = self._release(acct, now)
        if amt:
            self.s.escrow -= amt
            self.bank.credit(acct, amt)
        return amt

    def _reentry(self, acct: str, v: int, now: int) -> tuple[int, int]:
        s = self.s
        b = s.buckets.setdefault(acct, {"ws": 0, "t0": 0, "wa": 0, "excess": 0, "released": 0})
        if now >= b["ws"] + s.agg_window:
            b["ws"], b["wa"] = now, 0
        prev = b["wa"]
        new = prev + v
        b["wa"] = new
        locked = max(new - s.theta, 0) - max(prev - s.theta, 0)
        immediate = v - locked
        if locked:
            immediate += self._release(acct, now)
            remaining = b["excess"] - b["released"]
            b.update(excess=remaining + locked, released=0, t0=now)
            s.locked_total += locked
        return immediate, locked

    def check_invariants(self) -> None:
        s = self.s
        live = sum(self.face(n["denom"]) for n in s.notes.values() if n["state"] == LIVE)
        assert live == s.physical_outstanding, "P != sum of LIVE face values"
        assert s.escrow == s.physical_outstanding + s.pool + s.locked_total, "escrow mismatch"
        assert min(s.pool, s.locked_total, s.physical_outstanding) >= 0


def _pos(x) -> None:
    if not isinstance(x, int) or isinstance(x, bool) or x <= 0:
        raise NoteError("BAD_ARGS", "amount must be a positive int")


@dataclass
class DictBank:
    """Simple in-memory bank used by tests and the localnet adapter's dry runs."""
    bal: dict = field(default_factory=dict)

    def debit(self, acct: str, amount: int) -> None:
        if self.bal.get(acct, 0) < amount:
            raise NoteError("INSUFFICIENT_BALANCE")
        self.bal[acct] -= amount

    def credit(self, acct: str, amount: int) -> None:
        self.bal[acct] = self.bal.get(acct, 0) + amount

    def total(self) -> int:
        return sum(self.bal.values())


def make_groth16_verifier(vk) -> Callable[[dict, list], bool]:
    from . import groth16

    def _v(proof: dict, pub: list) -> bool:
        return groth16.verify(vk, proof, pub)
    return _v


