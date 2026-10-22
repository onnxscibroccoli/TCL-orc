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
