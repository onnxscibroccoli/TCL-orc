"""Native demurrage module, consistent with the ledger's fixed-point rule
(tcl.ledger.demurrage_fixed): same SCALE, threshold T, bounded Taylor steps, integer only.

The governed knob is DEMURRAGE_RATE_PPM_DAY (default 500 ppm/day = 0.05%/day, i.e. exactly the
ledger's LAMBDA_SCALED). At the default rate `apply` is bit-identical to apply_demurrage_fixed
(tested). Other rates scale lambda proportionally within the registry bounds.
"""
from __future__ import annotations

from ..ledger import demurrage_fixed as F

DEFAULT_PPM = 500


def lambda_scaled(rate_ppm_day: int) -> int:
    if rate_ppm_day == DEFAULT_PPM:
        return F.LAMBDA_SCALED
    return F.LAMBDA_SCALED * rate_ppm_day // DEFAULT_PPM


DEFAULT_THRESHOLD = F.T
RATE_BOUNDS = (100, 1_000)                                # ppm/day, hard
THRESHOLD_BOUNDS = (1_000_000 * F.SCALE, 50_000_000 * F.SCALE)  # base units, hard


def apply(balance: int, dt: int, rate_ppm_day: int = DEFAULT_PPM,
          threshold: int = DEFAULT_THRESHOLD) -> tuple[int, int]:
    """Return (new_balance, extracted). Extracted value goes to the vault; nothing is created or
    destroyed (new_balance + extracted == balance). Out-of-bounds params raise (fail-closed)."""
    if not RATE_BOUNDS[0] <= rate_ppm_day <= RATE_BOUNDS[1] or \
            not THRESHOLD_BOUNDS[0] <= threshold <= THRESHOLD_BOUNDS[1]:
        raise ValueError("demurrage parameters out of hard bounds")
    if rate_ppm_day == DEFAULT_PPM and threshold == F.T:
        return F.apply_demurrage_fixed(balance, dt)
    lam = lambda_scaled(rate_ppm_day)
    T = threshold
    if balance <= T or dt <= 0:
        return balance, 0
    cur, out, rem = balance, 0, dt
    while rem > 0:  # same loop as apply_demurrage_fixed, with the governed lambda
        step = min(rem, F.MAX_STEP_SECONDS)
        mult = F._taylor_multiplier(lam * step)
        if step >= 86_400:
            mult = max(0, mult - 55)
        surplus = cur - T
        fee = surplus - surplus * mult // F.SCALE
        cur, out, rem = cur - fee, out + fee, rem - step
    return cur, out


def make_rule(rate_ppm_day: int, threshold: int):
    """Ledger decay_override for governed params; None when equal to the ledger default."""
    apply(0, 0, rate_ppm_day, threshold)  # bounds check, fail-closed
    if rate_ppm_day == DEFAULT_PPM and threshold == F.T:
        return None
    return lambda bal, dt: apply(bal, dt, rate_ppm_day, threshold)
