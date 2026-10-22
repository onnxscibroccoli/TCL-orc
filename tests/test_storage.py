import sqlite3
import threading

import pytest

from tcl.ledger import InMemoryStorage, Ledger, SQLiteStorage, StorageError
from tcl.ledger.demurrage import T, apply_demurrage
from tcl.ledger.demurrage_fixed import SCALE, apply_demurrage_fixed
from tcl.ledger.nullifiers import NullifierSet

DAY = 86_400


@pytest.fixture(params=["sqlite", "memory"])
def storage(request, tmp_path):
    if request.param == "sqlite":
        s = SQLiteStorage(tmp_path / "l.db")
        yield s
        s.close()
    else:
        yield InMemoryStorage()


def test_wal_enabled(tmp_path):
    assert SQLiteStorage(tmp_path / "a.db").journal_mode.lower() == "wal"


def test_bigint_roundtrip(storage):
    big = 2**200 + 7
    with storage.transaction():
        storage.put_account("a", big, 1)
    assert storage.get_account("a") == (big, 1)


def test_persistence_across_reopen(tmp_path):
    p = tmp_path / "p.db"
    s = SQLiteStorage(p)
    lg = Ledger(s)
    lg.deposit("alice", T + 1_000_000 * SCALE, now=0)
    lg.settle("alice", now=DAY)
    NullifierSet(s).mark_spent(0xBEEF)
    bal, vault = lg.balance("alice"), lg.vault()
    s.close()
    s2 = SQLiteStorage(p)
    lg2 = Ledger(s2)
    assert lg2.balance("alice") == bal
    assert lg2.vault() == vault > 0
    assert NullifierSet(s2).is_spent(0xBEEF)
    with pytest.raises(ValueError):
        NullifierSet(s2).mark_spent(0xBEEF)
    assert [e[0] for e in s2.events()] == ["deposit", "demurrage"]


def test_rollback_on_failure(storage):
    lg = Ledger(storage)
    lg.deposit("a", 100, now=0)
    with pytest.raises(RuntimeError):
        with storage.transaction():
            storage.put_account("a", 999, 1)
            storage.append_event("x", "a", 1, 1)
            raise RuntimeError("boom")
    assert lg.balance("a") == 100
    assert len(storage.events()) == 1


def test_nested_savepoint_rollback(storage):
    with storage.transaction():
        storage.put_account("a", 1, 0)
        with pytest.raises(KeyError):
            with storage.transaction():
                storage.put_account("a", 2, 0)
                raise KeyError
        assert storage.get_account("a") == (1, 0)
    assert storage.get_account("a") == (1, 0)


def test_redistribute_overdraw_rolls_back(storage):
    lg = Ledger(storage)
    lg.deposit("a", T + 10**6 * SCALE, now=0)
    lg.settle("a", now=DAY)
    v, b = lg.vault(), lg.balance("a")
    with pytest.raises(ValueError):
        lg.redistribute("a", v + 1, now=DAY)
    assert (lg.vault(), lg.balance("a")) == (v, b)
    lg.redistribute("a", v, now=DAY)
    assert lg.vault() == 0 and lg.balance("a") == b + v


def test_write_outside_tx_rejected(tmp_path):
    s = SQLiteStorage(tmp_path / "w.db")
    with pytest.raises(StorageError):
        s.put_account("a", 1, 0)


def test_negative_balance_rejected(storage):
    with pytest.raises(StorageError):
        with storage.transaction():
            storage.put_account("a", -1, 0)


def test_ledger_matches_pure_fixed(storage):
    start = T + 10_000_000 * SCALE
    lg = Ledger(storage, "fixed")
    lg.deposit("a", start, now=0)
    lg.settle("a", now=30 * DAY)
    exp_bal, exp_fee = apply_demurrage_fixed(start, 30 * DAY)
    assert lg.balance("a") == exp_bal
    assert lg.vault() == exp_fee
    assert lg.balance("a") + lg.vault() == start  # conservation


def test_ledger_matches_pure_linear(storage):
    start = T + 10**6 * SCALE
    lg = Ledger(storage, "linear")
    lg.deposit("a", start, now=0)
    lg.settle("a", now=3 * DAY + 100)  # partial day not consumed
    assert lg.balance("a") == apply_demurrage(start, 3)[0]
    assert storage.get_account("a")[1] == 3 * DAY


def test_time_backwards_rejected(storage):
    lg = Ledger(storage)
    lg.deposit("a", 5, now=10)
    with pytest.raises(ValueError):
        lg.settle("a", now=5)


def test_apply_decay_all_atomic(storage):
    lg = Ledger(storage)
    lg.deposit("a", T + 10**6 * SCALE, now=0)
    lg.deposit("b", T - 1, now=0)
    total = lg.apply_decay_all(now=DAY)
    assert total == lg.vault() > 0
    assert lg.balance("b") == T - 1


def test_concurrent_wal_writers(tmp_path):
    p = tmp_path / "c.db"
    SQLiteStorage(p)
    errors = []

    def worker(i):
        try:
            s = SQLiteStorage(p)
            lg = Ledger(s)
            for _ in range(25):
                lg.deposit("shared", 1, now=0)
            s.close()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert Ledger(SQLiteStorage(p)).balance("shared") == 200


def test_reader_sees_committed_during_write(tmp_path):
    p = tmp_path / "r.db"
    w = SQLiteStorage(p)
    lg = Ledger(w)
    lg.deposit("a", 1, now=0)
    r = sqlite3.connect(p)
    with w.transaction():
        w.put_account("a", 50, 0)
        # WAL: reader not blocked, sees last committed value
        assert r.execute("SELECT balance FROM accounts WHERE account='a'").fetchone()[0] == "1"
    assert r.execute("SELECT balance FROM accounts WHERE account='a'").fetchone()[0] == "50"


def test_legacy_shims_still_import():
    from ledger.demurrage import apply_demurrage as a1
    from ledger.demurrage_fixed import _taylor_multiplier
    from ledger.nullifiers import NullifierSet as N
    assert a1 is apply_demurrage and callable(_taylor_multiplier) and N is NullifierSet
