"""Property-based tests (hypothesis) for demurrage, ledger and Poseidon tree."""
from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tcl.crypto.poseidon import to_field
from tcl.ledger import Ledger
from tcl.ledger.demurrage import T as T_LIN
from tcl.ledger.demurrage import apply_demurrage, redistribute
from tcl.ledger.demurrage_fixed import MAX_STEP_SECONDS, SCALE, T, apply_demurrage_fixed
from tcl.ledger.nullifiers import NullifierSet
from tcl.ledger.poseidon_tree import FIELD_P, PoseidonMerkleTree, compute_root, verify_proof
from tcl.storage import VAULT, InMemoryStorage

SLOW = settings(max_examples=100, deadline=None,
                suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
FAST = settings(max_examples=1000, deadline=None)

balances = st.integers(min_value=0, max_value=2**128 - 1)


# ---------------------------------------------------------------- demurrage
@FAST
@given(balances, st.integers(min_value=-10, max_value=10**5))
def test_linear_conserves_and_bounded(bal, days):
    new, fee = apply_demurrage(bal, days)
    assert new + fee == bal
    assert 0 <= fee
    assert new >= min(bal, T_LIN)  # never decays below threshold
    if bal <= T_LIN or days <= 0:
        assert fee == 0


@FAST
@given(balances, st.integers(min_value=-10, max_value=5 * 365 * 86_400))
def test_fixed_conserves_and_bounded(bal, dt):
    new, fee = apply_demurrage_fixed(bal, dt)
    assert new + fee == bal
    assert fee >= 0
    assert new >= min(bal, T)
    if bal <= T or dt <= 0:
        assert fee == 0


@FAST
@given(st.integers(min_value=T + 1, max_value=2**128 - 1),
       st.integers(min_value=1, max_value=MAX_STEP_SECONDS),
       st.integers(min_value=1, max_value=MAX_STEP_SECONDS))
def test_fixed_monotone_in_time(bal, a, b):
    lo, hi = sorted((a, b))
    assert apply_demurrage_fixed(bal, lo)[1] <= apply_demurrage_fixed(bal, hi)[1]


@FAST
@given(st.integers(min_value=0, max_value=2**128 - 1),
       st.integers(min_value=0, max_value=2**128 - 1),
       st.integers(min_value=1, max_value=MAX_STEP_SECONDS))
def test_fixed_monotone_in_balance(a, b, dt):
    lo, hi = sorted((a, b))
    # a richer holder never ends with less than a poorer one
    assert apply_demurrage_fixed(lo, dt)[0] <= apply_demurrage_fixed(hi, dt)[0]


@FAST
@given(st.integers(min_value=T + SCALE, max_value=2**100), st.integers(1, 7))
def test_fixed_rate_close_to_daily_target(bal, days):
    _, fee = apply_demurrage_fixed(bal, days * 86_400)
    surplus = bal - T
    # e^{-λ d} with r=0.0005/day: fee/surplus ≈ 1-(1-0.0005)^d within 1e-6 relative
    target = surplus - surplus * (9995**days) // (10_000**days)
    assert abs(fee - target) <= max(1, surplus // 10**6)


@FAST
@given(st.integers(min_value=0, max_value=2**128), st.integers(min_value=-5, max_value=2**129))
def test_redistribute_never_overdraws(vault, amount):
    if 0 <= amount <= vault:
        nv, out = redistribute(vault, amount)
        assert nv + out == vault and nv >= 0
    else:
        with pytest.raises(ValueError):
            redistribute(vault, amount)


# ------------------------------------------------------------------- ledger
ops = st.lists(
    st.tuples(st.sampled_from(["deposit", "settle", "redistribute", "decay_all"]),
              st.integers(0, 3),
              st.integers(1, 10 * T),
              st.integers(0, 10 * 86_400)),
    min_size=1, max_size=12)


@SLOW
@given(ops)
def test_ledger_conservation_and_root_integrity(seq):
    lg = Ledger(InMemoryStorage(), depth=6)
    now, deposited = 0, 0
    accts = [f"acct{i}" for i in range(4)]
    for op, who, amt, dt in seq:
        now += dt
        a = accts[who]
        if op == "deposit":
            lg.deposit(a, amt, now)
            deposited += amt
        elif op == "settle":
            lg.settle(a, now)
        elif op == "decay_all":
            lg.apply_decay_all(now)
        elif lg.vault() > 0:
            lg.redistribute(a, min(amt, lg.vault()), now)
        total = sum(lg.balance(x) for x in accts) + lg.vault()
        assert total == deposited
        assert lg.vault() >= 0
    assert lg.verify_state()
    for x in accts + [VAULT]:
        if lg.s.leaf_index(x) is not None:
            assert verify_proof(lg.root(), lg.proof(x))


@SLOW
@given(st.integers(1, 10 * T), st.integers(1, 10**6))
def test_ledger_time_cannot_go_backwards(amt, dt):
    lg = Ledger(InMemoryStorage(), depth=4)
    lg.deposit("a", amt, dt)
    with pytest.raises(ValueError):
        lg.settle("a", dt - 1)


@SLOW
@given(st.integers(-(2**64), 0))
def test_ledger_rejects_nonpositive_deposit(amt):
    lg = Ledger(InMemoryStorage(), depth=4)
    with pytest.raises(ValueError):
        lg.deposit("a", amt, 0)


# --------------------------------------------------------------------- tree
leaves = st.dictionaries(st.integers(0, 15), st.integers(0, FIELD_P - 1), max_size=8)


@SLOW
@given(leaves)
def test_tree_incremental_equals_rebuild(m):
    t = PoseidonMerkleTree(InMemoryStorage(), depth=4)
    for i, v in m.items():
        t.update(i, v)
    assert t.root() == t.rebuild_root(m)
    for i in m:
        p = t.proof(i)
        assert t.verify(p)
        assert compute_root(p.leaf, i, p.siblings) == t.root()


@SLOW
@given(leaves.filter(bool), st.integers(0, 15), st.integers(1, FIELD_P - 1))
def test_tree_tampered_proof_rejected(m, idx, delta):
    t = PoseidonMerkleTree(InMemoryStorage(), depth=4)
    for i, v in m.items():
        t.update(i, v)
    p = t.proof(idx)
    bad_leaf = type(p)(index=p.index, leaf=(p.leaf + delta) % FIELD_P,
                       siblings=p.siblings, root=p.root)
    assert not t.verify(bad_leaf)
    sibs = list(p.siblings)
    sibs[delta % len(sibs)] = (sibs[delta % len(sibs)] + 1) % FIELD_P
    assert not t.verify(type(p)(index=p.index, leaf=p.leaf, siblings=sibs, root=p.root))


@FAST
@given(st.one_of(st.integers(max_value=-1), st.integers(min_value=FIELD_P, max_value=2**300)))
def test_tree_rejects_out_of_field_leaf(v):
    t = PoseidonMerkleTree(InMemoryStorage(), depth=2)
    with pytest.raises(ValueError):
        t.update(0, v)


@FAST
@given(st.integers(min_value=-(2**300), max_value=2**300))
def test_to_field_canonical(x):
    try:
        f = to_field(x)
    except ValueError:
        return
    assert 0 <= f < FIELD_P


# --------------------------------------------------------------- nullifiers
@FAST
@given(st.lists(st.integers(0, FIELD_P - 1), max_size=30))
def test_nullifier_no_double_spend(ns):
    s = NullifierSet()
    seen = set()
    for n in ns:
        if n in seen:
            with pytest.raises(ValueError):
                s.mark_spent(n)
        else:
            s.mark_spent(n)
            seen.add(n)
        assert s.is_spent(n)
