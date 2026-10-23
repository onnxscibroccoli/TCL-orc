import pytest

from tcl.ledger.demurrage_fixed import SCALE
from tcl.ledger.notes import DAY, NoteError, NoteRegistry, State
from tcl.zk.note import SimulatedNote, note_ctx, nullifier

U = SCALE
CTX = note_ctx(31337, "0xC0ffee0000000000000000000000000000000001")


def reg(**kw):
    r = NoteRegistry(**kw)
    r.balances = {"m": 10**9 * U, "f": 10**9 * U}
    r.fund_pool("f", 10**6 * U)
    return r


def nf(n, k):
    return nullifier(n.id, k, CTX)


NOTE = SimulatedNote(11, 22, 100, 1)


def test_nullifier_domain_and_binding():
    assert nf(NOTE, 0) != nf(NOTE, 1)
    assert nullifier(NOTE.id, 0, CTX) != nullifier(NOTE.id, 0, note_ctx(1, 1))
    with pytest.raises(ValueError):
        nullifier(NOTE.id, -1, CTX)
    with pytest.raises(ValueError):
        SimulatedNote(0, 1, 100, 1).id


def test_lifecycle_replay_rearm():
    r = reg()
    r.mint("m", NOTE.id, 100, 10**30, 0)
    r.deposit(NOTE.id, 0, nf(NOTE, 0), nf(NOTE, 0), "bob", 10)
    assert r.notes[NOTE.id].state is State.DORMANT
    with pytest.raises(NoteError):
        r.deposit(NOTE.id, 0, nf(NOTE, 0), nf(NOTE, 0), "bob", 11)
    r.rearm("m", NOTE.id, 0, 20)
    assert r.notes[NOTE.id].era == 1 and r.notes[NOTE.id].state is State.LIVE
    with pytest.raises(NoteError):  # stale era
        r.deposit(NOTE.id, 0, nf(NOTE, 0), nf(NOTE, 0), "bob", 21)
    r.deposit(NOTE.id, 1, nf(NOTE, 1), nf(NOTE, 1), "bob", 22)
    assert r.balances["bob"] == 200 * U


def test_denominations_and_reward_bounds():
    r = reg()
    with pytest.raises(NoteError):
        r.mint("m", 1, 1000, 0, 0)
    with pytest.raises(NoteError):
        r.mint("m", 1, 7, 0, 0)
    c, rw = r.mint("m", 1, 500, 10**30, 0)
    assert rw == 5 * U and c == r.cost_cap[500]
    with pytest.raises(NoteError):
        NoteRegistry(reward_bps=101)


def test_epoch_budget_one_to_one():
    r = reg(epoch_budget=8 * U)
    assert sum(r.mint("m", 1, 500, 10**30, 0)) > 0
    assert r.mint("m", 2, 500, 10**30, 1) == (0, 0)
    assert sum(r.mint("m", 3, 500, 10**30, DAY)) > 0


def test_gradual_unlock_and_conservation():
    r = reg(theta=1_000 * U)
    total = r.total()
    for i in range(1, 16):
        r.mint("m", i, 100, 0, 0)
    for i in range(1, 16):
        r.deposit(i, 0, 1000 + i, 1000 + i, "bob", 0)
    assert r.balances["bob"] == 1_000 * U and r.locked_total == 500 * U
    assert r.release("bob", 15 * DAY) == 250 * U
    assert r.release("bob", 60 * DAY) == 250 * U
    assert r.total() == total


def test_accelerated_decay_idle_vs_spent():
    def run(spend):
        r = reg(theta=1_000 * U, release_window=365 * DAY)
        total = r.total()
        for i in range(1, 21):
            r.mint("m", i, 100, 0, 0)
            r.deposit(i, 0, i, i, "bob", 0)
        if spend:
            r.record_outflow("bob", 10**9 * U)
        p = r.apply_bucket_decay("bob", 0, 120 * DAY)
        assert r.total() == total
        return p
    idle, spent = run(False), run(True)
    assert idle > spent > 0
    # no decay applies below threshold
    r = reg()
    r.mint("m", 1, 100, 0, 0)
    r.deposit(1, 0, 1, 1, "bob", 0)
    assert r.apply_bucket_decay("bob", 0, 100 * DAY) == 0
    with pytest.raises(NoteError):
        r.apply_bucket_decay("bob", 0, 1, kappa=21)
