"""Known-answer tests: Poseidon must match circomlib/circomlibjs bit-for-bit."""
import pytest

from tcl.crypto.poseidon import FIELD_P, poseidon

# Vectors from iden3/circomlibjs test/poseidon.js and circomlib test/poseidoncircuit.js
KAT = [
    ([1, 2], 7853200120776062878684798364095072458815029376092732009249414926327459813530),
    ([1, 2], int("0x115cc0f5e7d690413df64c6b9662e9cf2a3617f2743245519e19607a4417189a", 16)),
    ([1, 2, 3, 4], int("0x299c867db6c1fdd79dcefa40e4510b9837e60ebb1ce0663dbaa525df65250465", 16)),
    ([1], 18586133768512220936620570745912940619677854269274689475585506675881198879027),
    ([0, 0], 14744269619966411208579211824598458697587494354926760081771325075741142829156),
]


@pytest.mark.parametrize("inp,exp", KAT)
def test_kat(inp, exp):
    assert poseidon(inp) == exp


def test_field_and_arity_guards():
    with pytest.raises(ValueError):
        poseidon([FIELD_P])
    with pytest.raises(ValueError):
        poseidon([-1])
    with pytest.raises(ValueError):
        poseidon([])
    with pytest.raises(ValueError):
        poseidon([1, 2, 3, 4, 5])
    with pytest.raises(TypeError):
        poseidon([True])


def test_order_sensitive():
    assert poseidon([1, 2]) != poseidon([2, 1])
