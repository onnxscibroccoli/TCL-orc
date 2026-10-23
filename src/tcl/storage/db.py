"""ACID storage abstraction for TCL off-chain state (canonical location).

Holds accounts, nullifiers, the event log, Poseidon Merkle tree nodes and
the state journal (undo log keyed by Merkle root) used for rollback.

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
    def append_event(self, kind: str, account: str, amount: int, ts: int,
                     root: Optional[int] = None) -> None: ...

    @abstractmethod
    def events(self) -> list[tuple[str, str, int, int]]: ...

    # --- Merkle tree nodes / meta / state journal -------------------------
    @abstractmethod
    def get_meta(self, k: str) -> Optional[str]: ...

    @abstractmethod
    def put_meta(self, k: str, v: str) -> None: ...

    @abstractmethod
    def get_node(self, level: int, index: int) -> Optional[int]: ...

    @abstractmethod
    def put_node(self, level: int, index: int, value: Optional[int]) -> None:
        """Store node hash; ``None`` deletes it (node reverts to the zero hash)."""

    @abstractmethod
    def leaf_index(self, account: str) -> Optional[int]: ...

    @abstractmethod
    def assign_leaf(self, account: str) -> int:
        """Return existing or next free leaf index for an account."""

    @abstractmethod
    def delete_account(self, account: str) -> None: ...

    @abstractmethod
    def journal_append(self, root: int, account: str,
                       prev: Optional[tuple[int, int]]) -> int:
        """Record a state transition (with pre-image) and resulting root; returns seq."""

    @abstractmethod
    def journal(self) -> list[tuple[int, int, str, Optional[tuple[int, int]]]]:
        """[(seq, root, account, prev)] oldest first."""

    @abstractmethod
    def journal_truncate(self, after_seq: int) -> None: ...

    @abstractmethod
    def events_with_root(self) -> list[tuple[str, str, int, int, Optional[int]]]: ...

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
        self._ev: list[tuple[str, str, int, int, Optional[int]]] = []
        self._meta: dict[str, str] = {}
        self._nodes: dict[tuple[int, int], int] = {}
        self._leaves: dict[str, int] = {}
        self._jr: list[tuple[int, int, str, Optional[tuple[int, int]]]] = []
        self._lock = threading.RLock()
        self._depth = 0

    @contextmanager
    def transaction(self) -> Iterator["InMemoryStorage"]:
        with self._lock:
            snap = deepcopy(self._state())
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._restore(snap)
                raise
            finally:
                self._depth -= 1

    def _state(self):
        return (self._acc, self._null, self._ev, self._meta, self._nodes, self._leaves, self._jr)

    def _restore(self, snap):
        (self._acc, self._null, self._ev, self._meta, self._nodes,
         self._leaves, self._jr) = snap

    def get_account(self, account):
        return self._acc.get(account)

    def delete_account(self, account):
        self._acc.pop(account, None)

    def get_meta(self, k):
        return self._meta.get(k)

    def put_meta(self, k, v):
        self._meta[k] = str(v)

    def get_node(self, level, index):
        return self._nodes.get((level, index))

    def put_node(self, level, index, value):
        if value is None:
            self._nodes.pop((level, index), None)
        else:
            self._nodes[(level, index)] = int(value)

    def leaf_index(self, account):
        return self._leaves.get(account)

    def assign_leaf(self, account):
        if account not in self._leaves:
            self._leaves[account] = len(self._leaves)
        return self._leaves[account]

    def journal_append(self, root, account, prev):
        seq = (self._jr[-1][0] + 1) if self._jr else 1
        self._jr.append((seq, int(root), account, prev))
        return seq

    def journal(self):
        return list(self._jr)

    def journal_truncate(self, after_seq):
        self._jr = [e for e in self._jr if e[0] <= after_seq]

    def events_with_root(self):
        return list(self._ev)

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

    def append_event(self, kind, account, amount, ts, root=None):
        self._ev.append((kind, account, int(amount), int(ts), None if root is None else int(root)))

    def events(self):
        return [e[:4] for e in self._ev]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  account TEXT PRIMARY KEY, balance TEXT NOT NULL, last_update INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS nullifiers(n TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, account TEXT NOT NULL,
  amount TEXT NOT NULL, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tree_nodes(
  level INTEGER NOT NULL, idx INTEGER NOT NULL, h TEXT NOT NULL, PRIMARY KEY(level, idx));
CREATE TABLE IF NOT EXISTS leaves(account TEXT PRIMARY KEY, idx INTEGER NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS journal(
  seq INTEGER PRIMARY KEY, root TEXT NOT NULL, account TEXT NOT NULL,
  prev_balance TEXT, prev_last INTEGER);
CREATE INDEX IF NOT EXISTS journal_root ON journal(root);
"""
SCHEMA_VERSION = "2"


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
        cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
        if "root" not in cols:  # v1 -> v2 migration
            conn.execute("ALTER TABLE events ADD COLUMN root TEXT")
        conn.execute("INSERT INTO meta VALUES('schema_version', ?) ON CONFLICT(k) "
                     "DO UPDATE SET v=excluded.v", (SCHEMA_VERSION,))
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

    def append_event(self, kind, account, amount, ts, root=None):
        self._require_tx().execute(
            "INSERT INTO events(kind, account, amount, ts, root) VALUES(?,?,?,?,?)",
            (kind, account, str(int(amount)), int(ts), None if root is None else str(int(root))))

    def events(self):
        return [(k, a, int(m), int(t)) for k, a, m, t in self._conn().execute(
            "SELECT kind, account, amount, ts FROM events ORDER BY id")]

    def events_with_root(self):
        return [(k, a, int(m), int(t), None if r is None else int(r))
                for k, a, m, t, r in self._conn().execute(
                    "SELECT kind, account, amount, ts, root FROM events ORDER BY id")]

    def delete_account(self, account):
        self._require_tx().execute("DELETE FROM accounts WHERE account=?", (account,))

    def get_meta(self, k):
        r = self._conn().execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def put_meta(self, k, v):
        self._require_tx().execute(
            "INSERT INTO meta VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    def get_node(self, level, index):
        r = self._conn().execute(
            "SELECT h FROM tree_nodes WHERE level=? AND idx=?", (level, index)).fetchone()
        return int(r[0]) if r else None

    def put_node(self, level, index, value):
        c = self._require_tx()
        if value is None:
            c.execute("DELETE FROM tree_nodes WHERE level=? AND idx=?", (level, index))
        else:
            c.execute("INSERT INTO tree_nodes VALUES(?,?,?) ON CONFLICT(level, idx) "
                      "DO UPDATE SET h=excluded.h", (level, index, str(int(value))))

    def leaf_index(self, account):
        r = self._conn().execute("SELECT idx FROM leaves WHERE account=?", (account,)).fetchone()
        return int(r[0]) if r else None

    def assign_leaf(self, account):
        i = self.leaf_index(account)
        if i is not None:
            return i
        c = self._require_tx()
        n = c.execute("SELECT COALESCE(MAX(idx) + 1, 0) FROM leaves").fetchone()[0]
        c.execute("INSERT INTO leaves VALUES(?,?)", (account, n))
        return int(n)

    def journal_append(self, root, account, prev):
        c = self._require_tx()
        cur = c.execute(
            "INSERT INTO journal(root, account, prev_balance, prev_last) VALUES(?,?,?,?)",
            (str(int(root)), account, None if prev is None else str(prev[0]),
             None if prev is None else int(prev[1])))
        return int(cur.lastrowid)

    def journal(self):
        return [(int(s), int(r), a, None if b is None else (int(b), int(l)))
                for s, r, a, b, l in self._conn().execute(
                    "SELECT seq, root, account, prev_balance, prev_last FROM journal ORDER BY seq")]

    def journal_truncate(self, after_seq):
        self._require_tx().execute("DELETE FROM journal WHERE seq > ?", (after_seq,))

    def integrity_check(self) -> str:
        return self._conn().execute("PRAGMA integrity_check").fetchone()[0]

    def checkpoint(self) -> tuple[int, int, int]:
        return tuple(self._conn().execute("PRAGMA wal_checkpoint(FULL)").fetchone())

    def close(self):
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None


def open_storage(url: str | Path) -> LedgerStorage:
    """``memory://`` -> InMemoryStorage, anything else -> SQLite file path."""
    u = str(url)
    if u.startswith("memory://"):
        return InMemoryStorage()
    if u.startswith("sqlite:///"):
        u = u[len("sqlite:///"):]
    return SQLiteStorage(u)
