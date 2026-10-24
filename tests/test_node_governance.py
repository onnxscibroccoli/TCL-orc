"""Native governance: bounded registry, timelock, conviction voting (1 person = 1 vote)."""
from __future__ import annotations

import pytest

from tcl.node import demurrage
from tcl.node.chain import DictPersonhood
from tcl.node.governance import WAD, GovError, Governance, GovState, decay_factor
from tcl.ledger.demurrage_fixed import T, apply_demurrage_fixed

DAY = 86_400


def gov(n=10):
    return Governance(GovState(), DictPersonhood({f"p{i}": 1 for i in range(n)}))


def test_decay_half_life():
    assert decay_factor(0, 7 * DAY) == WAD
    assert abs(decay_factor(7 * DAY, 7 * DAY) - WAD // 2) <= 2
    assert abs(decay_factor(14 * DAY, 7 * DAY) - WAD // 4) <= 2
    assert decay_factor(128 * 7 * DAY, 7 * DAY) == 0


def test_bounds_and_unknown():
    g = gov()
    with pytest.raises(GovError, match="OUT_OF_BOUNDS"):
        g.propose("VOTE_HALF_LIFE", 2 * DAY, 0)
    with pytest.raises(GovError, match="UNKNOWN_PARAM"):
        g.propose("MINT_EVERYTHING", 1, 0)


def test_conviction_pass_timelock_execute():
    g = gov(10)
    pid = g.propose("VOTE_HALF_LIFE", 10 * DAY, 0)
    for i in range(3):
        g.vote(pid, f"p{i}", False, 0)
    assert not g.passes(pid, 0, 10)                       # flash votes count 0 in their block
    with pytest.raises(GovError, match="NOT_PASSED"):
        g.queue(pid, 0, 10)
    assert g.conviction(pid, 7 * DAY)[0] == pytest.approx(1.5 * WAD, rel=1e-9)
    eta = g.queue(pid, 7 * DAY, 10)
    with pytest.raises(GovError, match="TIMELOCKED"):
        g.execute(pid, eta - 1)
    with pytest.raises(GovError, match="ALREADY_QUEUED"):
        g.vote(pid, "p5", False, 7 * DAY)
    g.execute(pid, eta)
    assert g.get("VOTE_HALF_LIFE") == 10 * DAY
    with pytest.raises(GovError):
        g.execute(pid, eta + 1)


def test_one_person_one_vote_and_majorities():
    g = gov(10)
    pid = g.propose("MINT_REWARD_BPS", 50, 0)
    g.vote(pid, "p0", False, 0)
    with pytest.raises(GovError, match="NULLIFIER_SPENT"):
        g.vote(pid, "p0", False, 0)                       # no second vote, regardless of wealth
    with pytest.raises(GovError, match="NOT_A_PERSON"):
        g.vote(pid, "whale-with-1e9-coins", False, 0)
    g.vote(pid, "p1", True, 0)
    assert not g.passes(pid, 30 * DAY, 10)                # 1 for vs 1 against: no majority
    g.withdraw(pid, "p1", 30 * DAY)
    assert not g.passes(pid, 60 * DAY, 10)                # 1 person < 10% quorum asymptote
    g.vote(pid, "p2", False, 30 * DAY)
    assert g.passes(pid, 60 * DAY, 10)
    # constitutional needs > 66%
    c = g.propose("UBI_ENABLED", 1, 0)
    for i in range(6):
        g.vote(c, f"p{i}", False, 0)
    for i in range(6, 10):
        g.vote(c, f"p{i}", True, 0)                       # 60% for
    assert not g.passes(c, 60 * DAY, 10)
    g.withdraw(c, "p9", 60 * DAY)                         # 6 vs 3 = 66.7%
    assert g.passes(c, 200 * DAY, 10)
    assert g.queue(c, 200 * DAY, 10) == 200 * DAY + g.s.constitutional_delay


def test_demurrage_module_matches_ledger_rule():
    for bal in (0, T, T + 1, T + 10**24, 3 * T):
        for dt in (0, 1, 3600, DAY, 7 * DAY, 30 * DAY + 5):
            assert demurrage.apply(bal, dt) == apply_demurrage_fixed(bal, dt)
            assert demurrage.apply(bal, dt, 499) is not None
    for ppm in (100, 500, 1000):
        new, out = demurrage.apply(2 * T, 30 * DAY, ppm)
        assert new + out == 2 * T and new >= T
    a = demurrage.apply(2 * T, 30 * DAY, 100)[1]
    b = demurrage.apply(2 * T, 30 * DAY, 1000)[1]
    assert a < demurrage.apply(2 * T, 30 * DAY)[1] < b
    assert demurrage.apply(T - 1, 365 * DAY, 1000) == (T - 1, 0)
