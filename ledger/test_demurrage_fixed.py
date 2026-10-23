from ledger.demurrage_fixed import apply_demurrage_fixed, T, SCALE

def test_subthreshold_unchanged():
    bal, fee = apply_demurrage_fixed(T - 1, 86_400)
    assert bal == T - 1
    assert fee == 0

def test_one_day_extracts_positive():
    start = T + 1_000_000 * SCALE
    bal, fee = apply_demurrage_fixed(start, 86_400)
    assert fee > 0
    assert bal == start - fee
    assert bal >= T

def test_long_interval_piecewise():
    start = T + 10_000_000 * SCALE
    bal, fee = apply_demurrage_fixed(start, 30 * 86_400)
    assert fee > 0
    assert bal < start
    assert bal >= T
