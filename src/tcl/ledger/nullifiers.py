"""Minimal nullifier set for replay protection.
Uniqueness is enforced by field element, not by proof bytes.
Optionally backed by a LedgerStorage for persistence.
"""
from __future__ import annotations


class NullifierSet:
    def __init__(self, storage=None) -> None:
        self._spent: set[int] = set()
        self._storage = storage

    def is_spent(self, nullifier: int) -> bool:
        if self._storage is not None:
            return self._storage.is_nullifier_spent(nullifier)
        return nullifier in self._spent

    def mark_spent(self, nullifier: int) -> None:
        if self._storage is not None:
            with self._storage.transaction():
                self._storage.add_nullifier(nullifier)
            return
        if nullifier in self._spent:
            raise ValueError("nullifier already spent")
        self._spent.add(nullifier)
