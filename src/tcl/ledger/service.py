"""Ledger service: applies demurrage/redistribution atomically over a LedgerStorage."""
from __future__ import annotations

from typing import Literal

from ..utils.logger import get_logger
from .demurrage import apply_demurrage, redistribute
from .demurrage_fixed import apply_demurrage_fixed
from ..storage.db import VAULT, LedgerStorage, StorageError
from .poseidon_tree import MerkleProof, PoseidonMerkleTree, account_leaf

log = get_logger("ledger")
Mode = Literal["linear", "fixed"]


class Ledger:
    """Lazy decay: balances are settled on touch using elapsed time since last_update.

    mode="linear" uses ledger.demurrage (per-day), mode="fixed" uses
    ledger.demurrage_fixed (per-second Taylor). Results are identical to
    calling the pure functions directly.
    """

    def __init__(self, storage: LedgerStorage, mode: Mode = "fixed", depth: int = 20) -> None:
        if mode not in ("linear", "fixed"):
            raise ValueError("mode must be 'linear' or 'fixed'")
        self.s = storage
        self.mode = mode
        self.tree = PoseidonMerkleTree(storage, depth)

    # --- state tree ---------------------------------------------------------
    def _put(self, account: str, balance: int, last_update: int) -> int:
        """Write account, update its Poseidon leaf, journal pre-image + new root."""
        with self.s.transaction():
            prev = self.s.get_account(account)
            self.s.put_account(account, balance, last_update)
            idx = self.s.assign_leaf(account)
            root = self.tree.update(idx, account_leaf(account, balance, last_update))
            self.s.journal_append(root, account, prev)
            return root

    def _event(self, kind: str, account: str, amount: int, now: int) -> None:
        self.s.append_event(kind, account, amount, now, root=self.tree.root())

    def root(self) -> int:
        return self.tree.root()

    def roots(self) -> list[int]:
        """Historical roots, oldest first (empty-tree root first)."""
        return [self.tree.zeros[self.tree.depth]] + [r for _, r, _, _ in self.s.journal()]

    def proof(self, account: str) -> MerkleProof:
        idx = self.s.leaf_index(account)
        if idx is None:
            raise KeyError(f"unknown account {account!r}")
        return self.tree.proof(idx)

    def verify_state(self) -> bool:
        """Recompute the root from account rows; True iff it matches stored root."""
        leaves = {}
        for acct in self.s.accounts() + [VAULT]:
            a = self.s.get_account(acct)
            idx = self.s.leaf_index(acct)
            if a is None:
                continue
            if idx is None:
                return False
            leaves[idx] = account_leaf(acct, a[0], a[1])
        return self.tree.rebuild_root(leaves) == self.tree.root()

    def rollback(self, root: int, now: int) -> int:
        """Restore state to the most recent point whose root == ``root``.

        Undoes journal entries newer than that point (accounts + tree leaves),
        truncates the journal and appends an audit ``rollback`` event.
        Nullifiers are NOT un-spent (fail-closed against double spends).
        """
        with self.s.transaction():
            j = self.s.journal()
            target = None
            if root == self.tree.zeros[self.tree.depth]:
                target = 0
            for seq, r, _, _ in j:
                if r == root:
                    target = seq
            if target is None:
                raise StorageError("unknown root: not in verified history")
            undone = 0
            for seq, _, acct, prev in reversed(j):
                if seq <= target:
                    break
                idx = self.s.leaf_index(acct)
                if prev is None:
                    self.s.delete_account(acct)
                    self.tree.update(idx, 0)
                else:
                    self.s.put_account(acct, prev[0], prev[1])
                    self.tree.update(idx, account_leaf(acct, prev[0], prev[1]))
                undone += 1
            if self.tree.root() != root:
                raise StorageError("rollback produced unexpected root")
            self.s.journal_truncate(target)
            self._event("rollback", "", undone, now)
        log.info("rollback", extra={"root": str(root), "undone": undone})
        return undone

    def _decay(self, balance: int, dt_seconds: int) -> tuple[int, int]:
        if self.mode == "fixed":
            return apply_demurrage_fixed(balance, dt_seconds)
        return apply_demurrage(balance, dt_seconds // 86_400)

    def balance(self, account: str) -> int:
        a = self.s.get_account(account)
        return a[0] if a else 0

    def vault(self) -> int:
        return self.s.get_vault()

    def deposit(self, account: str, amount: int, now: int) -> int:
        if amount <= 0:
            raise ValueError("amount must be positive")
        with self.s.transaction():
            bal = self.settle(account, now)
            self._put(account, bal + amount, now)
            self._event("deposit", account, amount, now)
        log.info("deposit", extra={"account": account, "amount": str(amount)})
        return bal + amount

    def settle(self, account: str, now: int) -> int:
        """Apply lazily accrued demurrage to one account; fee goes to vault."""
        with self.s.transaction():
            a = self.s.get_account(account)
            if a is None:
                return 0
            bal, last = a
            if now < last:
                raise ValueError("time went backwards")
            dt = now - last
            if self.mode == "linear":
                # only consume whole days so fractional time is not lost
                dt = (dt // 86_400) * 86_400
            new_bal, fee = self._decay(bal, dt)
            self._put(account, new_bal, last + dt)
            if fee:
                self._put(VAULT, self.vault() + fee, now)
                self._event("demurrage", account, fee, now)
                log.info("demurrage", extra={"account": account, "fee": str(fee)})
            return new_bal

    def apply_decay_all(self, now: int) -> int:
        """Settle every account atomically. Returns total extracted."""
        with self.s.transaction():
            before = self.vault()
            for acct in self.s.accounts():
                self.settle(acct, now)
            return self.vault() - before

    def redistribute(self, account: str, amount: int, now: int) -> None:
        with self.s.transaction():
            new_vault, credited = redistribute(self.vault(), amount)
            self._put(VAULT, new_vault, now)
            bal = self.settle(account, now)
            self._put(account, bal + credited, now)
            self._event("redistribute", account, credited, now)

    def spend_nullifier(self, n: int, now: int) -> None:
        with self.s.transaction():
            self.s.add_nullifier(n)
            self._event("nullifier", hex(n), 0, now)
