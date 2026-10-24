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


def apply(balance: int, dt: int, rate_ppm_day: int = DEFAULT_PPM) -> tuple[int, int]:
    """Return (new_balance, extracted). Extracted value goes to the vault; nothing is created or
    destroyed (new_balance + extracted == balance)."""
    if rate_ppm_day == DEFAULT_PPM:
        return F.apply_demurrage_fixed(balance, dt)
    lam = lambda_scaled(rate_ppm_day)
    if balance <= F.T or dt <= 0:
        return balance, 0
    cur, out, rem = balance, 0, dt
    while rem > 0:  # same loop as apply_demurrage_fixed, with the governed lambda
        step = min(rem, F.MAX_STEP_SECONDS)
        mult = F._taylor_multiplier(lam * step)
        if step >= 86_400:
            mult = max(0, mult - 55)
        surplus = cur - F.T
        fee = surplus - surplus * mult // F.SCALE
        cur, out, rem = cur - fee, out + fee, rem - step
    return cur, out
