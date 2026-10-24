"""Pure-Python BN254 (alt_bn128) arithmetic and optimal-ate pairing. Stdlib only.

CAVEATS (read before relying on this):
  * NOT constant-time. Python big-int arithmetic leaks timing. This is fine for *verification*
    (all inputs are public) but must never be used for anything that handles secrets.
  * SLOW: one multi-pairing (4 Miller loops + 1 final exponentiation) takes on the order of a
    second in CPython. Adequate for a localnet / reference node, not for mainnet throughput.
  * Structure follows the well-known py_ecc reference construction (FQ12 = Fp[w]/(w^12-18w^6+82),
    i -> w^6 - 9), with G2 kept in Fp2 and lines evaluated into FQ12 so no FQ12 inversion is needed.
"""
from __future__ import annotations

P = 21888242871839275222246405745257275088696311157297823662689037894645226208583
R = 21888242871839275222246405745257275088548364400416034343698204186575808495617
ATE_LOOP = 29793968203157093288

# ---------------- Fp2 = Fp[i]/(i^2+1) as (c0, c1) ----------------
def f2add(a, b): return ((a[0] + b[0]) % P, (a[1] + b[1]) % P)
def f2sub(a, b): return ((a[0] - b[0]) % P, (a[1] - b[1]) % P)
def f2neg(a): return ((-a[0]) % P, (-a[1]) % P)
def f2mul(a, b):
    return ((a[0] * b[0] - a[1] * b[1]) % P, (a[0] * b[1] + a[1] * b[0]) % P)
def f2muls(a, k): return (a[0] * k % P, a[1] * k % P)
def f2inv(a):
    d = pow((a[0] * a[0] + a[1] * a[1]) % P, P - 2, P)
    return (a[0] * d % P, (-a[1]) * d % P)
def f2conj(a): return (a[0], (-a[1]) % P)
def f2pow(a, e):
    r, b = (1, 0), a
    while e:
        if e & 1:
            r = f2mul(r, b)
        b = f2mul(b, b)
        e >>= 1
    return r

F2ONE, F2ZERO = (1, 0), (0, 0)
XI = (9, 1)
B2 = f2mul((3, 0), f2inv(XI))  # twist curve y^2 = x^3 + 3/(9+i)
GAMMA_X = f2pow(XI, (P - 1) // 3)
GAMMA_Y = f2pow(XI, (P - 1) // 2)

# ---------------- G1 (affine, None = infinity) ----------------
def g1_on_curve(pt) -> bool:
    if pt is None:
        return True
    x, y = pt
    return 0 <= x < P and 0 <= y < P and (y * y - x * x * x - 3) % P == 0

def g1_add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    (x1, y1), (x2, y2) = a, b
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        m = 3 * x1 * x1 * pow(2 * y1, P - 2, P) % P
    else:
        m = (y2 - y1) * pow(x2 - x1, P - 2, P) % P
    x3 = (m * m - x1 - x2) % P
    return (x3, (m * (x1 - x3) - y1) % P)

def g1_mul(pt, k: int):
    k %= R
    out = None
    while k:
        if k & 1:
            out = g1_add(out, pt)
        pt = g1_add(pt, pt)
        k >>= 1
    return out

def g1_neg(pt):
    return None if pt is None else (pt[0], (-pt[1]) % P)

# ---------------- G2 over Fp2 ----------------
def g2_on_curve(pt) -> bool:
    if pt is None:
        return True
    x, y = pt
    if not all(0 <= c < P for c in (*x, *y)):
        return False
    return f2sub(f2mul(y, y), f2add(f2mul(f2mul(x, x), x), B2)) == F2ZERO

def g2_add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    (x1, y1), (x2, y2) = a, b
    if x1 == x2:
        if f2add(y1, y2) == F2ZERO:
            return None
        m = f2mul(f2muls(f2mul(x1, x1), 3), f2inv(f2muls(y1, 2)))
    else:
        m = f2mul(f2sub(y2, y1), f2inv(f2sub(x2, x1)))
    x3 = f2sub(f2sub(f2mul(m, m), x1), x2)
    return (x3, f2sub(f2mul(m, f2sub(x1, x3)), y1))

def g2_mul(pt, k: int):
    out = None
    while k:
        if k & 1:
            out = g2_add(out, pt)
        pt = g2_add(pt, pt)
        k >>= 1
    return out

def g2_in_subgroup(pt) -> bool:
    return g2_mul(pt, R) is None

def g2_frob(pt):
    x, y = pt
    return (f2mul(f2conj(x), GAMMA_X), f2mul(f2conj(y), GAMMA_Y))

# ---------------- FQ12 = Fp[w]/(w^12 - 18 w^6 + 82), lists of 12 ints ----------------
ONE12 = [1] + [0] * 11

def f12mul(a, b):
    t = [0] * 23
    for i, ai in enumerate(a):
        if ai:
            for j, bj in enumerate(b):
                t[i + j] += ai * bj
    for k in range(22, 11, -1):  # w^k = 18 w^(k-6) - 82 w^(k-12)
        c = t[k]
        if c:
            t[k - 6] += 18 * c
            t[k - 12] -= 82 * c
    return [x % P for x in t[:12]]

def f12pow(a, e):
    r = ONE12
    for bit in bin(e)[2:]:
        r = f12mul(r, r)
        if bit == "1":
            r = f12mul(r, a)
    return r

def _embed(a, shift: int):
    """Fp2 element a = c0 + c1*i, i -> w^6 - 9, multiplied by w^shift (shift in 0..5)."""
    out = [0] * 12
    out[shift] = (a[0] - 9 * a[1]) % P
    out[shift + 6] = a[1] % P
    return out

def _line(r, t, px, py):
    """Line through twisted R,T (Fp2 affine) evaluated at P=(px,py) in G1, as FQ12."""
    (x1, y1), (x2, y2) = r, t
    if x1 == x2 and f2add(y1, y2) == F2ZERO:
        out = _embed(f2neg(x1), 2)          # xP - x_R*w^2
        out[0] = (out[0] + px) % P
        return out
    if x1 == x2:
        m = f2mul(f2muls(f2mul(x1, x1), 3), f2inv(f2muls(y1, 2)))
    else:
        m = f2mul(f2sub(y2, y1), f2inv(f2sub(x2, x1)))
    # m*w * (px - x1 w^2) - (py - y1 w^3) = px*m*w - m*x1*w^3 - py + y1*w^3
    a = _embed(f2muls(m, px), 1)
    b = _embed(f2sub(y1, f2mul(m, x1)), 3)
    out = [(a[i] + b[i]) % P for i in range(12)]
    out[0] = (out[0] - py) % P
    return out

def miller_loop(q, p):
    if q is None or p is None:
        return ONE12
    px, py = p
    rr, f = q, ONE12
    for i in range(ATE_LOOP.bit_length() - 2, -1, -1):
        f = f12mul(f12mul(f, f), _line(rr, rr, px, py))
        rr = g2_add(rr, rr)
        if ATE_LOOP & (1 << i):
            f = f12mul(f, _line(rr, q, px, py))
            rr = g2_add(rr, q)
    q1 = g2_frob(q)
    nq2 = g2_frob(q1)
    nq2 = (nq2[0], f2neg(nq2[1]))
    f = f12mul(f, _line(rr, q1, px, py))
    rr = g2_add(rr, q1)
    f = f12mul(f, _line(rr, nq2, px, py))
    return f

FINAL_EXP = (P ** 12 - 1) // R

def final_exp(f):
    return f12pow(f, FINAL_EXP)

def pairing_product_is_one(pairs) -> bool:
    """pairs: list of (G1 point, G2 point). True iff prod e(P_i, Q_i) == 1."""
    f = ONE12
    for p, q in pairs:
        f = f12mul(f, miller_loop(q, p))
    return final_exp(f) == ONE12

G1_GEN = (1, 2)
G2_GEN = ((10857046999023057135944570762232829481370756359578518086990519993285655852781,
           11559732032986387107991004021392285783925812861821192530917403151452391805634),
          (8495653923123431417604973247489272438418190587263600148770280649306958101930,
           4082367875863433681332203403145435568316851327593401208105741076214120093531))
