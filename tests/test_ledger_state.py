import pytest

from tcl.ledger import Ledger
from tcl.ledger.demurrage_fixed import SCALE, T
from tcl.ledger.poseidon_tree import account_leaf, verify_proof
from tcl.storage import VAULT, InMemoryStorage, SQLiteStorage, StorageError

START = T + 10**6 * SCALE


def mk(tmp_path, backend):
    return Ledger(InMemoryStorage() if backend == "mem" else SQLiteStorage(tmp_path / "l.db"),
                  depth=12)


@pytest.mark.parametrize("backend", ["mem", "sqlite"])
def test_each_transition_updates_root_and_event_log(tmp_path, backend):
    lg = mk(tmp_path, backend)
    r0 = lg.root()
    lg.deposit("alice", START, 0)
    r1 = lg.root()
    assert r1 != r0
    lg.apply_decay_all(86_400)
    assert lg.root() != r1 and lg.vault() > 0
    evs = lg.s.events_with_root()
    assert [e[0] for e in evs] == ["deposit", "demurrage"]
    assert evs[0][4] == r1 and evs[-1][4] == lg.root()
    assert lg.roots()[0] == r0 and lg.roots()[-1] == lg.root()
    assert lg.verify_state()
    pf = lg.proof("alice")
    bal, last = lg.s.get_account("alice")
    assert pf.leaf == account_leaf("alice", bal, last) and verify_proof(lg.root(), pf)
    assert verify_proof(lg.root(), lg.proof(VAULT))


def test_backends_agree_on_root(tmp_path):
    a, b = mk(tmp_path, "mem"), mk(tmp_path, "sqlite")
    for lg in (a, b):
        lg.deposit("alice", START, 0)
        lg.deposit("bob", START // 3, 10)
        lg.apply_decay_all(3 * 86_400)
        lg.redistribute("bob", lg.vault() // 2, 3 * 86_400)
    assert a.root() == b.root()


@pytest.mark.parametrize("backend", ["mem", "sqlite"])
def test_rollback_to_prior_root(tmp_path, backend):
    lg = mk(tmp_path, backend)
    lg.deposit("alice", START, 0)
    lg.deposit("bob", START, 0)
    snap_root = lg.root()
    snap = {a: lg.s.get_account(a) for a in ("alice", "bob")}
    lg.apply_decay_all(5 * 86_400)
    lg.deposit("carol", 10**18, 5 * 86_400)
    assert lg.root() != snap_root
    undone = lg.rollback(snap_root, 6 * 86_400)
    assert undone > 0 and lg.root() == snap_root
    assert {a: lg.s.get_account(a) for a in ("alice", "bob")} == snap
    assert lg.s.get_account("carol") is None and lg.vault() == 0
    assert lg.verify_state()
    assert lg.s.events_with_root()[-1][0] == "rollback"
    # history after the target is gone; continuing works
    lg.deposit("dave", 5, 7 * 86_400)
    assert lg.verify_state()


@pytest.mark.parametrize("backend", ["mem", "sqlite"])
def test_rollback_to_genesis_and_unknown(tmp_path, backend):
    lg = mk(tmp_path, backend)
    genesis = lg.root()
    lg.deposit("alice", START, 0)
    with pytest.raises(StorageError):
        lg.rollback(12345, 1)
    lg.rollback(genesis, 1)
    assert lg.root() == genesis and lg.s.accounts() == []


def test_rollback_keeps_nullifiers_spent(tmp_path):
    lg = mk(tmp_path, "sqlite")
    r = lg.root()
    lg.deposit("a", 1, 0)
    lg.spend_nullifier(42, 0)
    lg.rollback(r, 1)
    with pytest.raises(ValueError):
        lg.spend_nullifier(42, 2)


def test_rollback_persists_across_reopen(tmp_path):
    p = tmp_path / "l.db"
    lg = Ledger(SQLiteStorage(p), depth=12)
    lg.deposit("a", START, 0)
    r = lg.root()
    lg.deposit("b", START, 1)
    lg.rollback(r, 2)
    lg2 = Ledger(SQLiteStorage(p), depth=12)
    assert lg2.root() == r and lg2.verify_state() and lg2.balance("b") == 0


@pytest.mark.parametrize("mode", ["linear", "fixed"])
def test_demurrage_conservation_with_tree(tmp_path, mode):
    lg = Ledger(SQLiteStorage(tmp_path / "c.db"), mode=mode, depth=10)
    accts = {f"u{i}": START // (i + 1) for i in range(5)}
    for a, v in accts.items():
        lg.deposit(a, v, 0)
    total = sum(accts.values())
    for day in range(1, 8):
        lg.apply_decay_all(day * 86_400)
        assert sum(lg.balance(a) for a in accts) + lg.vault() == total
        assert lg.verify_state()
    lg.redistribute("u4", lg.vault(), 8 * 86_400)
    assert sum(lg.balance(a) for a in accts) + lg.vault() == total
    assert all(lg.balance(a) >= 0 for a in accts)


def test_failed_op_does_not_move_root(tmp_path):
    lg = mk(tmp_path, "sqlite")
    lg.deposit("a", START, 100)
    r = lg.root()
    with pytest.raises(ValueError):
        lg.settle("a", 50)  # time went backwards
    with pytest.raises(ValueError):
        lg.deposit("a", -1, 200)
    assert lg.root() == r and len(lg.s.journal()) == 1


def test_compat_imports():
    from tcl import logs
    from tcl.ledger.storage import SQLiteStorage as S1
    from tcl.storage.db import SQLiteStorage as S2
    from tcl.utils.logger import get_logger
    assert S1 is S2 and logs.get_logger is get_logger
