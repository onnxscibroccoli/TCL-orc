"""Deterministic Q32.32 math shared by Civitas-99 kinematics and WASM golden tests.

Pure integer arithmetic: signed 64-bit with INT64_MIN reserved as an error
sentinel for the WASM ABI. Multiplication truncates toward zero, not Python's
floor for negatives. No floating point, wall clock, or host state is involved.
"""
from __future__ import annotations

from dataclasses import dataclass

Q = 1 << 32
ERROR_SENTINEL = -(1 << 63)
MIN_VALID = ERROR_SENTINEL + 1
MAX_VALID = (1 << 63) - 1


class FixedPointError(ValueError):
    """Invalid fixed-point input or arithmetic overflow."""


def check(value: int) -> int:
    if type(value) is not int or not MIN_VALID <= value <= MAX_VALID:
        raise FixedPointError("Q32.32 operand/result outside valid signed i64 range")
    return value


def add(a: int, b: int) -> int:
    return check(check(a) + check(b))


def _trunc_div(n: int, d: int) -> int:
    if d == 0:
        raise FixedPointError("division by zero")
    quotient = abs(n) // abs(d)
    return -quotient if (n < 0) != (d < 0) else quotient


def ratio(n: int, d: int = 1) -> int:
    if type(n) is not int or type(d) is not int:
        raise FixedPointError("ratio requires integer operands")
    return check(_trunc_div(n * Q, d))


def mul(a: int, b: int) -> int:
    product = check(a) * check(b)
    return check(_trunc_div(product, Q))


def div(a: int, b: int) -> int:
    return check(_trunc_div(check(a) * Q, check(b)))


@dataclass(frozen=True)
class Motion:
    position: int = 0
    velocity: int = 0

    def advance(self, acceleration: int, dt: int = Q) -> "Motion":
        check(self.position)
        check(self.velocity)
        check(acceleration)
        if type(dt) is not int or not 0 <= dt <= Q:
            raise FixedPointError("time step must be a Q32.32 interval [0, 1]")
        velocity = add(self.velocity, mul(acceleration, dt))
        position = add(self.position, mul(velocity, dt))
        return Motion(position, velocity)


def golden_vectors() -> list[dict[str, object]]:
    # All values are signed decimal strings to avoid JS's 53-bit Number limit.
    vectors = [
        (0, 0, 0, 0),
        (Q, Q, Q, Q),
        (Q // 2, -(3 * Q // 2), Q // 4, Q // 2),
        (-Q, Q // 3, -(Q // 2), Q // 2),
        (MAX_VALID, 0, 0, 0),
        (0, MAX_VALID, Q, Q),
        (0, -MAX_VALID, Q, Q),
        (Q * 7, -(Q * 2), Q // 4, Q),
    ]
    for idx in range(1, 101):
        sign = -1 if idx % 2 else 1
        vectors.append((sign * ratio(idx, 7), ratio((idx * 13) % 100 - 50, 11),
                        sign * ratio(idx % 7, 9), ratio(idx % 10, 10)))

    def safe(operation, *args: int) -> int:
        try:
            return operation(*args)
        except FixedPointError:
            return ERROR_SENTINEL

    out: list[dict[str, object]] = []
    for p, v, a, dt in vectors:
        delta_v = safe(mul, a, dt)
        velocity = (ERROR_SENTINEL if delta_v == ERROR_SENTINEL
                    else safe(add, v, delta_v))
        delta_p = (ERROR_SENTINEL if velocity == ERROR_SENTINEL
                   else safe(mul, velocity, dt))
        position = (ERROR_SENTINEL if delta_p == ERROR_SENTINEL
                    else safe(add, p, delta_p))
        out.append({"p": str(p), "v": str(v), "a": str(a), "dt": str(dt),
                    "velocity": str(velocity), "position": str(position),
                    "product": str(safe(mul, p, v)),
                    "sum": str(safe(add, p, v))})
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(golden_vectors(), sort_keys=True, separators=(",", ":")))
