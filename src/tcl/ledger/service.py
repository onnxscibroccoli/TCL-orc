"""Ledger service: applies demurrage/redistribution atomically over a LedgerStorage."""
from __future__ import annotations

from typing import Literal

from ..logs import get_logger
from .demurrage import apply_demurrage, redistribute
from .demurrage_fixed import apply_demurrage_fixed
from .storage import VAULT, LedgerStorage

log = get_logger("ledger")
Mode = Literal["linear", "fixed"]


class Ledger:
    """Lazy decay: balances are settled on touch using elapsed time since last_update.

    mode="linear" uses ledger.demurrage (per-day), mode="fixed" uses
    ledger.demurrage_fixed (per-second Taylor). Results are identical to
    calling the pure functions directly.
    """

    def __init__(self, storage: LedgerStorage, mode: Mode = "fixed") -> None:
        if mode not in ("linear", "fixed"):
            raise ValueError("mode must be 'linear' or 'fixed'")
        self.s = storage
        self.mode = mode

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
            self.s.put_account(account, bal + amount, now)
            self.s.append_event("deposit", account, amount, now)
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
            self.s.put_account(account, new_bal, last + dt)
            if fee:
                self.s.put_account(VAULT, self.vault() + fee, now)
                self.s.append_event("demurrage", account, fee, now)
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
            self.s.put_account(VAULT, new_vault, now)
            bal = self.settle(account, now)
            self.s.put_account(account, bal + credited, now)
            self.s.append_event("redistribute", account, credited, now)

    def spend_nullifier(self, n: int, now: int) -> None:
        with self.s.transaction():
            self.s.add_nullifier(n)
            self.s.append_event("nullifier", hex(n), 0, now)
