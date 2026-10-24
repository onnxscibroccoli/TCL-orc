"""Native notes module (port of NoteController) incl. conservation invariants."""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from tcl.node.groth16 import VerifyingKey, solidity_to_snarkjs
from tcl.node.notes import (DORMANT, LIVE, DictBank, NoteError, NotesModule,
                            make_groth16_verifier)

FX = Path(__file__).resolve().parents[1] / "test/contracts/fixtures"
U = 10**6


def mk(verify=lambda p, s: p == {"ok": True}, pool=0, unit=U):
    bank = DictBank({"m": 10**15, "funder": 10**15})
    m = NotesModule.genesis(unit, 77, bank, verify)
    if pool:
        m.fund_pool("funder", pool)
    return m, bank


def total(m, bank):
    return bank.total() + m.s.escrow


def test_mint_cost_credit_reward_and_fallback():
    m, bank = mk(pool=10**9)
    t0 = total(m, bank)
    cost, reward = m.mint("m", 1, 100, 10**9, now=1000)        # attested cost above caps
    assert cost == 3 * U and reward == 100 * U * 20 // 10_000   # c_abs binds; rho = 0.2%
    assert m.s.notes["1"]["state"] == LIVE and total(m, bank) == t0
    m.check_invariants()
    m2, b2 = mk(pool=0)                                         # empty pool -> pure 1:1 swap
    assert m2.mint("m", 1, 100, 10**9, now=1000) == (0, 0)
    assert b2.bal["m"] == 10**15 - 100 * U
    m2.check_invariants()


def test_epoch_budget_falls_back():
    m, bank = mk(pool=10**12)
    m.set_params(20, 86_400, 2 * U, 10**12, 86_400, 86_400)    # budget 2, 1 for large notes
    m.s.policy["reserve_bps"] = 5000
    assert m.mint("m", 1, 100, 0, 10) == (0, U // 5)
    assert m.mint("m", 2, 500, 0, 20) == (0, 0)                 # 0.2 + 1 > 1 large share
    assert m.mint("m", 3, 5, 0, 30) == (0, U // 100 + U // 4)  # small notes still credited
    assert m.mint("m", 4, 5, 10**9, 40) == (0, 0)               # 2 + 0.26 > remaining budget
    assert m.mint("m", 5, 100, 0, 86_400 + 1) == (0, U // 5)    # new epoch
    m.check_invariants()


# PR #20 / docs/11 §7.2 spec table with UNIT = 1e18 (same numbers as the Foundry test)
E18 = 10**18
TABLE = [(5, 2 * E18, 26 * E18 // 100), (10, 3 * E18, 27 * E18 // 100),
         (20, 3 * E18, 29 * E18 // 100), (50, 3 * E18, E18 // 10), (100, 3 * E18, E18 // 5),
         (200, 3 * E18, 2 * E18 // 5), (500, 3 * E18, E18)]


def mk18():
    bank = DictBank({"m": 10**30, "funder": 10**30})
    m = NotesModule.genesis(E18, 77, bank, lambda p, s: p == {"ok": True})
    m.fund_pool("funder", 10**24)
    m.set_params(100, 86_400, 10**30, 5_000_000 * E18, 30 * 86_400, 30 * 86_400)
    return m, bank


@pytest.mark.parametrize("d,want_c,want_r", TABLE)
def test_default_schedule_all_denominations(d, want_c, want_r):
    m, bank = mk18()
    c, r = m.mint("m", 1, d, 2**256, 0)
    assert (c, r) == (want_c, want_r)
    assert c + r <= d * E18 * 6000 // 10_000
    m.check_invariants()


def test_honest_cost_is_paid_not_cap():
    m, _ = mk18()
    assert m.mint("m", 1, 5, 15 * E18 // 10, 0) == (15 * E18 // 10, 26 * E18 // 100)


def test_rearm_no_cost_reward_after_circulation():
    m, _ = mk18()
    m.mint("m", 1, 100, 2**256, 0)
    m.deposit(1, 11, "bob", {"ok": True}, 0)
    assert m.rearm("m", 1, 2**256, 0) == (0, 0)                 # < 7 days since era start
    m.deposit(1, 12, "bob", {"ok": True}, 0)
    assert m.rearm("m", 1, 2**256, 7 * 86_400) == (0, E18 // 5)  # rho*V, no small bonus
    m.check_invariants()


def test_small_note_reserve_survives_large_mints():
    m, _ = mk18()
    m.set_params(100, 86_400, 10 * E18, 5_000_000 * E18, 30 * 86_400, 30 * 86_400)
    assert sum(m.mint("m", 1, 500, 2**256, 0)) == 4 * E18
    assert sum(m.mint("m", 2, 500, 2**256, 0)) == 0
    assert sum(m.mint("m", 3, 5, 2**256, 0)) == 226 * E18 // 100
    m.check_invariants()


def test_missing_policy_fails_closed_to_one_to_one():
    m, _ = mk18()
    m.s.policy = {}
    assert m.mint("m", 1, 5, 2**256, 0) == (0, 0)


def test_cost_policy_hard_bounds():
    m, _ = mk18()
    for args in ((5001, 0, 0, 0, 7 * 86_400), (0, 10 * E18 + 1, 0, 0, 7 * 86_400),
                 (0, 0, E18 + 1, 0, 7 * 86_400), (0, 0, 0, 8001, 7 * 86_400),
                 (0, 0, 0, 0, 86_399), (0, 0, 0, 0, 90 * 86_400 + 1)):
        with pytest.raises(NoteError, match="OUT_OF_BOUNDS"):
            m.set_cost_policy(*args)
    with pytest.raises(NoteError, match="OUT_OF_BOUNDS"):
        m.set_cost_policy(4000, 0, 0, 0, 7 * 86_400, reward_bps=101)


def test_governance_moves_policy_within_bounds():
    from tcl.node.chain import ChainState
    d = ChainState.genesis("c", ["aa" * 32], {}, 1, 0, 1, 0, 1, E18)
    cs = ChainState(d, "c", DictBank({"m": 10**30, "f": 10**30}))
    cs.notes.fund_pool("f", 10**24)
    cs.gov.s.params["SMALL_BONUS_MILLI"] = 500
    cs._sync_note_policy()
    assert cs.notes.mint("m", 1, 5, 2**256, 0)[1] == 51 * E18 // 100
    with pytest.raises(Exception, match="OUT_OF_BOUNDS"):
        cs.gov.propose("COST_CAP_BPS", 5500, 0)                 # above hard 50%


def test_states_era_nullifiers_and_rearm():
    m, bank = mk()
    m.mint("m", 9, 50, 0, 0)
    with pytest.raises(NoteError, match="BAD_STATE"):
        m.mint("m", 9, 50, 0, 0)
    with pytest.raises(NoteError, match="BAD_STATE"):
        m.rearm("m", 9, 0, 0)                                    # LIVE cannot rearm
    with pytest.raises(NoteError, match="BAD_PROOF"):
        m.deposit(9, 5, "bob", {"ok": False}, 10)
    assert m.deposit(9, 5, "bob", {"ok": True}, 10) == (50 * U, 0)
    assert m.s.notes["9"]["state"] == DORMANT
    with pytest.raises(NoteError, match="BAD_STATE"):
        m.deposit(9, 6, "bob", {"ok": True}, 11)                 # DORMANT cannot deposit
    m.rearm("m", 9, 0, 20)
    assert m.s.notes["9"]["era"] == 1
    with pytest.raises(NoteError, match="NULLIFIER_SPENT"):
        m.deposit(9, 5, "bob", {"ok": True}, 30)
    m.deposit(9, 6, "bob", {"ok": True}, 30)
    m.check_invariants()
    for bad in ((0, 50), (2**254, 50), (3, 7), (3, 0)):
        with pytest.raises(NoteError):
            m.mint("m", bad[0], bad[1], 0, 0)


def test_cost_cap_hard_bound():
    m, _ = mk()
    m.set_denomination(100, True, 100 * U * 5000 // 10_000)     # exactly 50%: ok
    with pytest.raises(NoteError, match="OUT_OF_BOUNDS"):
        m.set_denomination(100, True, 100 * U * 5000 // 10_000 + 1)
    with pytest.raises(NoteError):
        m.set_params(101, 86_400, 0, 1, 86_400, 86_400)          # reward > 1%


def test_hoarding_threshold_locks_and_vests():
    m, bank = mk()
    m.set_params(100, 86_400, 0, 150 * U, 86_400, 10 * 86_400)
    for i in (1, 2):
        m.mint("m", i, 100, 0, 0)
    assert m.deposit(1, 11, "h", {"ok": True}, 0) == (100 * U, 0)
    assert m.deposit(2, 12, "h", {"ok": True}, 0) == (50 * U, 50 * U)
    assert m.s.locked_total == 50 * U
    assert m.release("h", 5 * 86_400) == 25 * U
    assert m.release("h", 10 * 86_400) == 25 * U and m.s.locked_total == 0
    m.check_invariants()


def test_conservation_random_walk():
    rng = random.Random(1)
    m, bank = mk(pool=10**10)
    t0 = total(m, bank)
    ids = []
    for step in range(400):
        now = step * 3600
        op = rng.choice(["mint", "dep", "rearm", "rel", "fund"])
        try:
            if op == "mint":
                i = rng.randrange(1, 60)
                m.mint("m", i, rng.choice([5, 10, 20, 50, 100, 200, 500]),
                       rng.randrange(0, 10 * U), now)
                ids.append(i)
            elif op == "dep" and ids:
                m.deposit(rng.choice(ids), rng.randrange(1, 10**6), rng.choice("abc"),
                          {"ok": True}, now)
            elif op == "rearm" and ids:
                m.rearm("m", rng.choice(ids), rng.randrange(0, U), now)
            elif op == "rel":
                m.release(rng.choice("abc"), now)
            else:
                m.fund_pool("funder", rng.randrange(1, 10**6))
        except NoteError:
            pass
        m.check_invariants()
        assert total(m, bank) == t0


def test_real_groth16_deposit_from_fixture():
    n = json.loads((FX / "note_deposit.json").read_text())
    vk = VerifyingKey.load(Path(__file__).resolve().parents[1] /
                           "circuits/note/verification_key.json")
    ctx = int(n["era0"]["pubSignals"][3], 16)
    bank = DictBank({"m": 10**9})
    m = NotesModule.genesis(1, ctx, bank, make_groth16_verifier(vk))
    nid, rcpt = int(n["id"], 16), int(n["recipient"], 16)
    p = {k: solidity_to_snarkjs(n[k]["a"], n[k]["b"], n[k]["c"]) for k in ("era0", "era1",
                                                                            "wrongCtx")}
    nf = {k: int(n[k]["pubSignals"][5], 16) for k in p}
    m.mint("m", nid, 100, 0, 0)
    with pytest.raises(NoteError, match="BAD_PROOF"):
        m.deposit(nid, nf["era1"], rcpt, p["era1"], 1)           # future-era proof
    with pytest.raises(NoteError, match="BAD_PROOF"):
        m.deposit(nid, nf["wrongCtx"], rcpt, p["wrongCtx"], 1)   # other deployment's proof
    with pytest.raises(NoteError, match="BAD_PROOF"):
        m.deposit(nid, nf["era0"], rcpt + 1, p["era0"], 1)       # redirected recipient
    assert m.deposit(nid, nf["era0"], rcpt, p["era0"], 1) == (100, 0)
    m.rearm("m", nid, 0, 2)
    with pytest.raises(NoteError, match="BAD_PROOF"):
        m.deposit(nid, nf["era0"] + 1, rcpt, p["era0"], 3)       # old-era proof
    m.deposit(nid, nf["era1"], rcpt, p["era1"], 3)
    m.check_invariants()
