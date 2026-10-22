import pytest
from ledger.demurrage import apply_demurrage, redistribute, T

def test_below_threshold_unchanged():
    bal, fee = apply_demurrage(T - 1, 10)
    assert bal == T - 1
    assert fee == 0

def test_exactly_threshold_unchanged():
    bal, fee = apply_demurrage(T, 365)
    assert bal == T
    assert fee == 0

def test_above_threshold_extracts():
    start = T + 1_000_000 * 10**18
    bal, fee = apply_demurrage(start, 1)
    assert fee > 0
    assert bal == start - fee
    assert bal >= T

def test_redistribute_reduces_vault():
    new_vault, credited = redistribute(1000, 300)
    assert new_vault == 700
    assert credited == 300

def test_redistribute_rejects_overdraw():
    with pytest.raises(ValueError):
        redistribute(100, 200)

def test_compounding_matches_daily_exponential():
    from decimal import Decimal, localcontext
    with localcontext() as ctx:
        ctx.prec=80
        start=T+10**24
        balance,_=apply_demurrage(start,365)
        expected=T+int(Decimal(start-T)*(-Decimal('0.0005')*365).exp())
        # One-second factor is floored at 1e27; accumulated error is measured in wei.
        assert abs(balance-expected)<=2*86400*365*(start-T)//10**27+128

def test_invalid_inputs_rejected():
    for balance,days in [(-1,1),(T,-1),(True,1),(T,1.5)]:
        with pytest.raises(ValueError):apply_demurrage(balance,days)
