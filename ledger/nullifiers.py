"""Minimal nullifier set for replay protection.
Uniqueness is enforced by field element, not by proof bytes.
"""
from __future__ import annotations

class NullifierSet:
    def __init__(self) -> None:
        self._spent: set[int] = set()

    def is_spent(self, nullifier: int) -> bool:
        return nullifier in self._spent

    def mark_spent(self, nullifier: int) -> None:
        if nullifier in self._spent:
            raise ValueError("nullifier already spent")
        self._spent.add(nullifier)
