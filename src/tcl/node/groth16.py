"""In-node Groth16 verifier over BN254 (snarkjs verification_key.json format). Stdlib only.

Fail-closed: any malformed key/proof/signal, off-curve or wrong-subgroup point, or public signal
>= the scalar field order returns False (never raises to the caller via `verify`).
CAVEATS: pure Python, non-constant-time (inputs are public, so acceptable for verification),
roughly 0.5-1.5 s per proof in CPython. See bn254.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import bn254 as c


class BadInput(ValueError):
    pass


def _int(v: Any) -> int:
    if isinstance(v, bool):
        raise BadInput("bool")
    if isinstance(v, int):
        x = v
    elif isinstance(v, str):
        x = int(v, 16) if v.lower().startswith("0x") else int(v, 10)
    else:
        raise BadInput(f"not a number: {v!r}")
    if x < 0:
        raise BadInput("negative")
    return x


def _g1(v) -> tuple:
    if len(v) == 3 and _int(v[2]) != 1:
        raise BadInput("G1 must be affine (z=1)")
    pt = (_int(v[0]), _int(v[1]))
    if pt == (0, 0) or not c.g1_on_curve(pt):  # infinity not accepted in proofs/keys
        raise BadInput("G1 point not on curve")
    return pt


def _g2(v) -> tuple:
    """snarkjs order: [[x.c0, x.c1], [y.c0, y.c1], [1, 0]]."""
    if len(v) == 3 and [_int(z) for z in v[2]] != [1, 0]:
        raise BadInput("G2 must be affine")
    pt = ((_int(v[0][0]), _int(v[0][1])), (_int(v[1][0]), _int(v[1][1])))
    if not c.g2_on_curve(pt) or not c.g2_in_subgroup(pt):
        raise BadInput("G2 point not on curve / not in subgroup")
    return pt


class VerifyingKey:
    def __init__(self, d: dict) -> None:
        if d.get("protocol") != "groth16" or d.get("curve") != "bn128":
            raise BadInput("only groth16/bn128 keys")
        self.n_public = int(d["nPublic"])
        self.alpha = _g1(d["vk_alpha_1"])
        self.beta = _g2(d["vk_beta_2"])
        self.gamma = _g2(d["vk_gamma_2"])
        self.delta = _g2(d["vk_delta_2"])
        self.ic = [_g1(p) for p in d["IC"]]
        if len(self.ic) != self.n_public + 1:
            raise BadInput("IC length != nPublic+1")

    @classmethod
    def load(cls, path: str | Path) -> "VerifyingKey":
        return cls(json.loads(Path(path).read_text()))


def solidity_to_snarkjs(a, b, cc) -> dict:
    """Convert EVM-calldata order (G2 coords as [c1, c0]) to a snarkjs proof dict."""
    return {"pi_a": [a[0], a[1], "1"],
            "pi_b": [[b[0][1], b[0][0]], [b[1][1], b[1][0]], ["1", "0"]],
            "pi_c": [cc[0], cc[1], "1"], "protocol": "groth16", "curve": "bn128"}


def verify(vk: VerifyingKey, proof: dict, public: list) -> bool:
    try:
        sig = [_int(s) for s in public]
        if len(sig) != vk.n_public or any(s >= c.R for s in sig):
            return False
        a, b, cc = _g1(proof["pi_a"]), _g2(proof["pi_b"]), _g1(proof["pi_c"])
    except (BadInput, KeyError, TypeError, ValueError, IndexError):
        return False
    acc = vk.ic[0]
    for s, ic in zip(sig, vk.ic[1:]):
        acc = c.g1_add(acc, c.g1_mul(ic, s))
    return c.pairing_product_is_one([(c.g1_neg(a), b), (vk.alpha, vk.beta),
                                     (acc, vk.gamma), (cc, vk.delta)])
