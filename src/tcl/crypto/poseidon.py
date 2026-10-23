"""Poseidon hash over the BN254 scalar field, bit-compatible with circomlib.

Port of iden3/circomlibjs ``poseidon_reference.js`` (Circom ``Poseidon(n)``).
Round constants C and MDS matrices M are loaded verbatim from
``poseidon_bn254_t2_t5.json``, extracted from circomlibjs
src/poseidon_constants.js @48b3ab3 (sha256 of the source file:
05158e0b3fab993603d5bb2eaffffc66bfa068f2188d0e75cd596afad263d7b0).
Supports 1..4 inputs (t = 2..5). Correctness is pinned by known-answer tests
against circomlib vectors in tests/test_poseidon.py.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib import resources
from typing import Sequence

FIELD_P = 21888242871839275222246405745257275088548364400416034343698204186575808495617
N_ROUNDS_F = 8
N_ROUNDS_P = [56, 57, 56, 60]  # t = 2..5
MAX_INPUTS = len(N_ROUNDS_P)


@lru_cache(maxsize=1)
def _constants() -> tuple[list[list[int]], list[list[list[int]]]]:
    raw = json.loads(resources.files(__package__).joinpath("poseidon_bn254_t2_t5.json").read_text())
    C = [[int(x, 16) for x in row] for row in raw["C"]]
    M = [[[int(x, 16) for x in r] for r in m] for m in raw["M"]]
    for i, m in enumerate(M):
        t = i + 2
        if len(m) != t or len(C[i]) != (N_ROUNDS_F + N_ROUNDS_P[i]) * t:
            raise RuntimeError(f"corrupt Poseidon constants for t={t}")
    return C, M


def to_field(x: int) -> int:
    if not isinstance(x, int) or isinstance(x, bool):
        raise TypeError("Poseidon inputs must be int")
    if x < 0 or x >= FIELD_P:
        raise ValueError("input outside BN254 scalar field")
    return x


def poseidon(inputs: Sequence[int]) -> int:
    n = len(inputs)
    if not 1 <= n <= MAX_INPUTS:
        raise ValueError(f"poseidon supports 1..{MAX_INPUTS} inputs")
    C_all, M_all = _constants()
    t = n + 1
    C, M, nP = C_all[t - 2], M_all[t - 2], N_ROUNDS_P[t - 2]
    p = FIELD_P
    state = [0] + [to_field(a) for a in inputs]
    half = N_ROUNDS_F // 2
    for r in range(N_ROUNDS_F + nP):
        base = r * t
        state = [(a + C[base + i]) % p for i, a in enumerate(state)]
        if r < half or r >= half + nP:
            state = [pow(a, 5, p) for a in state]
        else:
            state[0] = pow(state[0], 5, p)
        state = [sum(M[i][j] * state[j] for j in range(t)) % p for i in range(t)]
    return state[0]


def hash_account_id(account: str) -> int:
    """Map an account label to a field element (sha256 mod p)."""
    return int.from_bytes(hashlib.sha256(account.encode()).digest(), "big") % FIELD_P
