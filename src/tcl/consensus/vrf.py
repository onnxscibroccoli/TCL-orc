"""ECVRF-EDWARDS25519-SHA512-TAI (RFC 9381 §5, suite 0x03) on the in-house Ed25519 code.
Stdlib only. validate_key = TRUE (small-order public keys are rejected).
NOT constant-time (the prover's secret leaks via timing): fine for localnet/dev, must be replaced
by a constant-time implementation before validators hold value-bearing keys."""
from __future__ import annotations

from .ed25519 import G, L, _add, _compress, _decompress, _eq, _expand, _mul, _sha512, p

SUITE = b"\x03"
IDENTITY = (0, 1, 1, 0)


def _neg(P):
    return ((-P[0]) % p, P[1], P[2], (-P[3]) % p)


def _encode_to_curve(salt: bytes, alpha: bytes):
    for ctr in range(256):
        h = _sha512(SUITE + b"\x01" + salt + alpha + bytes([ctr]) + b"\x00")
        H = _decompress(h[:32])
        if H is not None:
            H = _mul(8, H)
            if not _eq(H, IDENTITY):
                return H
    raise ValueError("encode_to_curve failed")  # probability ~2^-256


def _challenge(*pts) -> int:
    s = SUITE + b"\x02" + b"".join(_compress(P) for P in pts) + b"\x00"
    return int.from_bytes(_sha512(s)[:16], "little")


def prove(sk: bytes, alpha: bytes) -> bytes:
    x, _prefix = _expand(sk)
    pk = _compress(_mul(x, G))
    H = _encode_to_curve(pk, alpha)
    gamma = _mul(x, H)
    k = int.from_bytes(_sha512(_sha512(sk)[32:] + _compress(H)), "little") % L
    c = _challenge(_mul(x, G), H, gamma, _mul(k, G), _mul(k, H))
    s = (k + c * x) % L
    return _compress(gamma) + c.to_bytes(16, "little") + s.to_bytes(32, "little")


def proof_to_hash(pi: bytes) -> bytes:
    gamma = _decompress(pi[:32])
    if gamma is None:
        raise ValueError("invalid proof")
    return _sha512(SUITE + b"\x03" + _compress(_mul(8, gamma)) + b"\x00")


def verify(pk: bytes, alpha: bytes, pi: bytes) -> bytes | None:
    """Return beta (64 bytes) if valid, else None (fail-closed on any malformed input)."""
    if not isinstance(pi, (bytes, bytearray)) or len(pi) != 80 or len(pk) != 32:
        return None
    Y = _decompress(pk)
    if Y is None or _eq(_mul(8, Y), IDENTITY):
        return None
    gamma = _decompress(pi[:32])
    if gamma is None:
        return None
    c = int.from_bytes(pi[32:48], "little")
    s = int.from_bytes(pi[48:], "little")
    if s >= L:
        return None
    H = _encode_to_curve(pk, alpha)
    U = _add(_mul(s, G), _neg(_mul(c, Y)))
    V = _add(_mul(s, H), _neg(_mul(c, gamma)))
    if _challenge(Y, H, gamma, U, V) != c:
        return None
    return proof_to_hash(bytes(pi))
