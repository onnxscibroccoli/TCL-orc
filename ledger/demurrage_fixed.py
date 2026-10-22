"""Compatibility API using the contract-exact integer reference.

The earlier Taylor/calibration experiment is not a settlement rule.
Use settle directly to retain the private numerator remainder across calls.
"""
from ledger.demurrage import SCALE, T, settle


def apply_demurrage_fixed(balance: int, delta_t_seconds: int) -> tuple[int, int]:
    """One settlement with zero initial remainder; no alternate decay curve."""
    return settle(balance, delta_t_seconds)[:2]
