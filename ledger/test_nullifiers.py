import pytest
from ledger.nullifiers import NullifierSet

def test_mark_and_reject_replay():
    s = NullifierSet()
    n = 0xdeadbeef
    assert not s.is_spent(n)
    s.mark_spent(n)
    assert s.is_spent(n)
    with pytest.raises(ValueError):
        s.mark_spent(n)
