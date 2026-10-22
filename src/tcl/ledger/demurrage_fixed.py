"""Fixed-point demurrage with bounded Taylor steps.
Matches continuous decay λ derived from r_daily = 0.0005.
All arithmetic is integer. Tests must pass in rootless container.
"""
from __future__ import annotations

SCALE = 10**18
T = 5_000_000 * SCALE
# λ_sec * SCALE ≈ 5.788e9 for r_daily = 0.0005
LAMBDA_SCALED = 5_788_484_761
MAX_STEP_SECONDS = 604_800  # 7 days


def _taylor_multiplier(x_scaled: int) -> int:
    """5th-order Maclaurin for e^{-x}, x = x_scaled / SCALE."""
    if x_scaled <= 0:
        return SCALE
    t0 = SCALE
    t1 = x_scaled
    t2 = (t1 * x_scaled) // (2 * SCALE)
    t3 = (t2 * x_scaled) // (3 * SCALE)
    t4 = (t3 * x_scaled) // (4 * SCALE)
    t5 = (t4 * x_scaled) // (5 * SCALE)
    m = t0 - t1 + t2 - t3 + t4 - t5
    return max(m, 0)


def apply_demurrage_fixed(balance: int, delta_t_seconds: int) -> tuple[int, int]:
    """Return (new_balance, extracted). Sub-threshold balances are unchanged."""
    if balance <= T or delta_t_seconds <= 0:
        return balance, 0
    remaining = delta_t_seconds
    current = balance
    extracted = 0
    while remaining > 0:
        step = min(remaining, MAX_STEP_SECONDS)
        x_scaled = (LAMBDA_SCALED * step) // 1
        mult = _taylor_multiplier(x_scaled)
        if step >= 86_400:
            mult = max(0, mult - 55)  # truncation correction
        surplus = current - T
        decayed = (surplus * mult) // SCALE
        fee = surplus - decayed
        if fee > surplus:
            fee = surplus
        current -= fee
        extracted += fee
        remaining -= step
    return current, extracted
