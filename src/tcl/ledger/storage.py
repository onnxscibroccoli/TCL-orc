"""Storage abstraction for the off-chain ledger.

All balances are integers (wei-scale) stored as decimal TEXT to avoid
SQLite's 64-bit INTEGER overflow. Every mutation happens inside
``transaction()``; an exception rolls back all writes (fail-closed).
"""
from __future__ import annotations

import sqlite3
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Iterator, Optional

VAULT = "__vault__"


class StorageError(RuntimeError):
    pass


class LedgerStorage(ABC):
    """Abstract persistent store. Implementations must be transactional."""

    @abstractmethod
    def transaction(self): ...

    @abstractmethod
    def get_account(self, account: str) -> Optional[tuple[int, int]]:
        """Return (balance, last_update_ts) or None."""

    @abstractmethod
    def put_account(self, account: str, balance: int, last_update: int) -> None: ...

    @abstractmethod
    def accounts(self) -> list[str]: ...

    @abstractmethod
    def is_nullifier_spent(self, n: int) -> bool: ...

    @abstractmethod
    def add_nullifier(self, n: int) -> None: ...

    @abstractmethod
    def append_event(self, kind: str, account: str, amount: int, ts: int) -> None: ...

    @abstractmethod
    def events(self) -> list[tuple[str, str, int, int]]: ...

    def close(self) -> None:  # pragma: no cover - default no-op
        pass

    def get_vault(self) -> int:
        a = self.get_account(VAULT)
        return a[0] if a else 0


class InMemoryStorage(LedgerStorage):
    """Transactional in-memory store (snapshot/restore). For tests and sims."""

    def __init__(self) -> None:
        self._acc: dict[str, tuple[int, int]] = {}
        self._null: set[int] = set()
        self._ev: list[tuple[str, str, int, int]] = []
        self._lock = threading.RLock()
        self._depth = 0

    @contextmanager
    def transaction(self) -> Iterator["InMemoryStorage"]:
        with self._lock:
            snap = deepcopy((self._acc, self._null, self._ev))
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._acc, self._null, self._ev = snap
                raise
            finally:
                self._depth -= 1

    def get_account(self, account):
        return self._acc.get(account)

    def put_account(self, account, balance, last_update):
        if balance < 0:
            raise StorageError("negative balance")
        self._acc[account] = (int(balance), int(last_update))

    def accounts(self):
        return sorted(k for k in self._acc if k != VAULT)

    def is_nullifier_spent(self, n):
        return n in self._null

    def add_nullifier(self, n):
        if n in self._null:
            raise ValueError("nullifier already spent")
        self._null.add(n)

    def append_event(self, kind, account, amount, ts):
        self._ev.append((kind, account, int(amount), int(ts)))

    def events(self):
        return list(self._ev)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  account TEXT PRIMARY KEY, balance TEXT NOT NULL, last_update INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS nullifiers(n TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, account TEXT NOT NULL,
  amount TEXT NOT NULL, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""
SCHEMA_VERSION = "1"


class SQLiteStorage(LedgerStorage):
    """SQLite backend in WAL mode. One connection per thread."""

    def __init__(self, path: str | Path, busy_timeout_ms: int = 10_000) -> None:
        self.path = str(path)
        self._busy = busy_timeout_ms
        self._local = threading.local()
        conn = self._conn()
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if self.path != ":memory:" and mode.lower() != "wal":
            raise StorageError(f"WAL not enabled (got {mode})")
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta VALUES('schema_version', ?)", (SCHEMA_VERSION,))
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=self._busy / 1000, isolation_level=None,
                                check_same_thread=False)
            c.execute(f"PRAGMA busy_timeout={self._busy}")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
            self._local.depth = 0
        return c

    @property
    def journal_mode(self) -> str:
        return self._conn().execute("PRAGMA journal_mode").fetchone()[0]

    @contextmanager
    def transaction(self) -> Iterator["SQLiteStorage"]:
        c = self._conn()
        depth = self._local.depth
        sp = f"sp{depth}"
        if depth == 0:
            c.execute("BEGIN IMMEDIATE")
        else:
            c.execute(f"SAVEPOINT {sp}")
        self._local.depth += 1
        try:
            yield self
        except BaseException:
            self._local.depth -= 1
            if depth == 0:
                c.execute("ROLLBACK")
            else:
                c.execute(f"ROLLBACK TO {sp}")
                c.execute(f"RELEASE {sp}")
            raise
        else:
            self._local.depth -= 1
            c.execute("COMMIT" if depth == 0 else f"RELEASE {sp}")

    def _require_tx(self) -> sqlite3.Connection:
        c = self._conn()
        if self._local.depth == 0:
            raise StorageError("writes must occur inside transaction()")
        return c

    def get_account(self, account):
        r = self._conn().execute(
            "SELECT balance, last_update FROM accounts WHERE account=?", (account,)).fetchone()
        return (int(r[0]), int(r[1])) if r else None

    def put_account(self, account, balance, last_update):
        if balance < 0:
            raise StorageError("negative balance")
        self._require_tx().execute(
            "INSERT INTO accounts VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET "
            "balance=excluded.balance, last_update=excluded.last_update",
            (account, str(int(balance)), int(last_update)))

    def accounts(self):
        return [r[0] for r in self._conn().execute(
            "SELECT account FROM accounts WHERE account != ? ORDER BY account", (VAULT,))]

    def is_nullifier_spent(self, n):
        return self._conn().execute("SELECT 1 FROM nullifiers WHERE n=?", (str(n),)).fetchone() is not None

    def add_nullifier(self, n):
        try:
            self._require_tx().execute("INSERT INTO nullifiers VALUES(?)", (str(n),))
        except sqlite3.IntegrityError as e:
            raise ValueError("nullifier already spent") from e

    def append_event(self, kind, account, amount, ts):
        self._require_tx().execute(
            "INSERT INTO events(kind, account, amount, ts) VALUES(?,?,?,?)",
            (kind, account, str(int(amount)), int(ts)))

    def events(self):
        return [(k, a, int(m), int(t)) for k, a, m, t in self._conn().execute(
            "SELECT kind, account, amount, ts FROM events ORDER BY id")]

    def close(self):
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None
