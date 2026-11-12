/* Standalone, no-import deterministic Q32.32 module.
 *
 * Signed i64 ABI: INT64_MIN is reserved for any invalid arithmetic.
 * Multiplication uses four 32-bit limbs and a 128-bit *addition* path,
 * avoiding compiler-rt's __multi3 runtime dependency on wasm32.
 * All fractions truncate toward zero, matching tcl.vos.fixedpoint.
 * No float instructions, clocks, I/O, imports, or globals are required.
 */
#include <stdint.h>
#include <limits.h>

#define ERR INT64_MIN

static int64_t valid(__int128 n) {
    if (n <= (__int128)INT64_MIN || n > (__int128)INT64_MAX) return ERR;
    return (int64_t)n;
}

__attribute__((visibility("default")))
int64_t q32_add(int64_t a, int64_t b) {
    if (a == ERR || b == ERR) return ERR;
    return valid((__int128)a + (__int128)b);
}

__attribute__((visibility("default")))
int64_t q32_mul(int64_t a, int64_t b) {
    if (a == ERR || b == ERR) return ERR;
    int negative = (a < 0) != (b < 0);
    uint64_t ua = a < 0 ? (uint64_t)(-(a + 1)) + 1 : (uint64_t)a;
    uint64_t ub = b < 0 ? (uint64_t)(-(b + 1)) + 1 : (uint64_t)b;
    uint64_t a0 = (uint32_t)ua, a1 = ua >> 32;
    uint64_t b0 = (uint32_t)ub, b1 = ub >> 32;
    __uint128_t product = (__uint128_t)(a0 * b0)
        + (((__uint128_t)(a0 * b1) + (a1 * b0)) << 32)
        + ((__uint128_t)(a1 * b1) << 64);
    __uint128_t magnitude = product >> 32;
    if (magnitude > (uint64_t)INT64_MAX) return ERR;
    return negative ? -(int64_t)magnitude : (int64_t)magnitude;
}

__attribute__((visibility("default")))
int64_t q32_velocity(int64_t v, int64_t a, int64_t dt) {
    if (dt < 0) return ERR;
    return q32_add(v, q32_mul(a, dt));
}

__attribute__((visibility("default")))
int64_t q32_position(int64_t p, int64_t v, int64_t a, int64_t dt) {
    if (dt < 0) return ERR;
    int64_t v2 = q32_velocity(v, a, dt);
    if (v2 == ERR) return ERR;
    return q32_add(p, q32_mul(v2, dt));
}
