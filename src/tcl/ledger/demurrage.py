"""Empirical demurrage ledger simulation matching CirculatoryEngine invariants.
All values are integers (wei-scale). Tests must pass in rootless container.
"""
from __future__ import annotations

T = 5_000_000 * 10**18
DELTA_NUM = 5
DELTA_DEN = 10_000  # 0.0005 = 5/10000 per day, scaled


def apply_demurrage(balance: int, delta_t_days: int) -> tuple[int, int]:
    """Return (new_balance, extracted_to_vault)."""
    if balance <= T or delta_t_days <= 0:
        return balance, 0
    surplus = balance - T
    fee = (surplus * DELTA_NUM * delta_t_days) // DELTA_DEN
    if fee > surplus:
        fee = surplus
    return balance - fee, fee


def redistribute(vault: int, amount: int) -> tuple[int, int]:
    if amount > vault or amount < 0:
        raise ValueError("invalid redistribution amount")
    return vault - amount, amount
