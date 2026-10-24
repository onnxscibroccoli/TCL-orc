"""Native governance (minimal port of ParameterRegistry + ConvictionVoting + GovernanceTimelock).

* Parameter registry: every key has hard [lo, hi] bounds fixed at genesis; writes outside bounds
  are rejected, and the ONLY write path is an executed, timelocked proposal.
* Conviction voting, one person one vote: voters are identified by personhood nullifier (MOCK
  provider: tier >= MIN_VOTE_TIER), weight is exactly 1 per person. Money never adds weight.
  Y(t+dt) = 2^(-dt/h) Y(t) + (1 - 2^(-dt/h)) S, h = VOTE_HALF_LIFE (default 7 d, bounds 3-30 d).
  A new vote contributes 0 in its own block (flash votes cannot pass anything).
  Pass: Y_for >= quorum * persons AND Y_for > m (Y_for + Y_against), m = 50% (66% constitutional).
* Timelock: a passed proposal is queued and executable only after the delay (constitutional
  proposals use the longer delay). Execution re-checks bounds (fail-closed).
Integer WAD fixed point throughout (same decay series as ConvictionVoting.sol).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

WAD = 10**18
LN2_WAD = 693147180559945309
DAY = 86_400
MIN_VOTE_TIER = 1

# key -> (default, lo, hi, constitutional)
DEFAULT_PARAMS: dict[str, tuple[int, int, int, bool]] = {
    "VOTE_HALF_LIFE": (7 * DAY, 3 * DAY, 30 * DAY, False),
    # demurrage: applied by the ledger from the NEXT validator epoch (docs/15 §5)
    "DEMURRAGE_RATE_PPM_DAY": (500, 100, 1_000, False),       # 0.05%/day, bounds 0.01-0.1%
    "DEMURRAGE_THRESHOLD_TCL": (5_000_000, 1_000_000, 50_000_000, False),
    # note mint policy (PR #20, docs/11 §7.2); hard bounds mirror NoteController
    "MINT_REWARD_BPS": (20, 0, 100, False),                   # rho 0.2%, <= 1%
    "COST_CAP_BPS": (4000, 0, 5000, False),                   # gamma_c 40%, <= 50%
    "COST_CAP_ABS_MILLI": (3000, 0, 10_000, False),           # c_abs 3 TCL, <= 10
    "SMALL_BONUS_MILLI": (250, 0, 1_000, False),              # b_small 0.25 TCL, <= 1
    "SMALL_RESERVE_BPS": (5000, 0, 8000, False),              # 50% of epoch budget, <= 80%
    "MIN_CIRCULATION": (7 * DAY, DAY, 90 * DAY, False),       # re-arm reward era age
    "POOL_SHARE_BPS": (500, 100, 2_500, False),
    "UBI_ENABLED": (0, 0, 1, True),
}


class GovError(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(f"{code}: {msg}" if msg else code)
        self.code = code


def decay_factor(dt: int, h: int) -> int:
    """2^(-dt/h) in WAD (port of ConvictionVoting.decayFactor)."""
    k = dt // h
    if k >= 128:
        return 0
    y = LN2_WAD * (dt % h) // h
    s, term, neg, i = WAD, WAD, True, 1
    while i < 30 and term:
        term = term * y // (i * WAD)
        s = s - term if neg else s + term
        neg, i = not neg, i + 1
    return s >> k


@dataclass
class GovState:
    params: dict = field(default_factory=lambda: {k: v[0] for k, v in DEFAULT_PARAMS.items()})
    bounds: dict = field(default_factory=lambda: {k: [v[1], v[2], v[3]]
                                                  for k, v in DEFAULT_PARAMS.items()})
    proposals: dict = field(default_factory=dict)
    next_id: int = 1
    quorum_wad: int = WAD // 10           # 10% of persons
    param_delay: int = 2 * DAY
    constitutional_delay: int = 14 * DAY

    def to_json(self) -> dict:
        return asdict(self)


class Governance:
    def __init__(self, state: GovState, personhood) -> None:
        self.s, self.ph = state, personhood

    def get(self, key: str) -> int:
        return self.s.params[key]

    # --- proposals -------------------------------------------------------------------------
    def propose(self, key: str, value: int, now: int) -> int:
        if key not in self.s.bounds:
            raise GovError("UNKNOWN_PARAM", key)
        lo, hi, const = self.s.bounds[key]
        if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
            raise GovError("OUT_OF_BOUNDS", f"{key} must be in [{lo}, {hi}]")
        pid = self.s.next_id
        self.s.next_id += 1
        self.s.proposals[str(pid)] = {"key": key, "value": value, "constitutional": const,
                                      "sup_for": 0, "sup_against": 0, "conv_for": 0,
                                      "conv_against": 0, "last": now, "voters": {},
                                      "eta": None, "executed": False}
        return pid

    def _p(self, pid: int) -> dict:
        p = self.s.proposals.get(str(pid))
        if p is None:
            raise GovError("UNKNOWN_PROPOSAL")
        return p

    def conviction(self, pid: int, now: int) -> tuple[int, int]:
        p = self._p(pid)
        dt = now - p["last"]
        if dt <= 0:
            return p["conv_for"], p["conv_against"]
        a = decay_factor(dt, self.get("VOTE_HALF_LIFE"))
        f = (a * p["conv_for"] + (WAD - a) * p["sup_for"] * WAD) // WAD
        g = (a * p["conv_against"] + (WAD - a) * p["sup_against"] * WAD) // WAD
        return f, g

    def _checkpoint(self, p: dict, pid: int, now: int) -> None:
        p["conv_for"], p["conv_against"] = self.conviction(pid, now)
        p["last"] = now

    def vote(self, pid: int, person: str, against: bool, now: int) -> None:
        p = self._p(pid)
        if p["eta"] is not None:
            raise GovError("ALREADY_QUEUED")
        if not self.ph.is_enrolled(person) or self.ph.tier(person) < MIN_VOTE_TIER:
            raise GovError("NOT_A_PERSON")
        if person in p["voters"]:
            raise GovError("NULLIFIER_SPENT", "one vote per person per proposal")
        self._checkpoint(p, pid, now)
        p["voters"][person] = bool(against)
        p["sup_against" if against else "sup_for"] += 1

    def withdraw(self, pid: int, person: str, now: int) -> None:
        p = self._p(pid)
        if p["eta"] is not None or person not in p["voters"]:
            raise GovError("BAD_STATE")
        self._checkpoint(p, pid, now)
        p["sup_against" if p["voters"].pop(person) else "sup_for"] -= 1

    def passes(self, pid: int, now: int, persons: int) -> bool:
        p = self._p(pid)
        f, g = self.conviction(pid, now)
        m = 6_600 if p["constitutional"] else 5_000
        return persons > 0 and f >= persons * self.s.quorum_wad and f * 10_000 > m * (f + g)

    def queue(self, pid: int, now: int, persons: int) -> int:
        p = self._p(pid)
        if p["eta"] is not None:
            raise GovError("ALREADY_QUEUED")
        if not self.passes(pid, now, persons):
            raise GovError("NOT_PASSED")
        p["eta"] = now + (self.s.constitutional_delay if p["constitutional"]
                          else self.s.param_delay)
        return p["eta"]

    def execute(self, pid: int, now: int) -> None:
        p = self._p(pid)
        if p["eta"] is None or p["executed"]:
            raise GovError("BAD_STATE")
        if now < p["eta"]:
            raise GovError("TIMELOCKED", f"executable at {p['eta']}")
        lo, hi, _ = self.s.bounds[p["key"]]
        if not lo <= p["value"] <= hi:
            raise GovError("OUT_OF_BOUNDS")
        self.s.params[p["key"]] = p["value"]
        p["executed"] = True
