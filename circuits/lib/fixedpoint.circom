pragma circom 2.1.6;

include "circomlib/circuits/bitify.circom";
include "circomlib/circuits/comparators.circom";

// q = floor(num / den) for a compile-time constant 0 < den < 2^64.
// Constrained: num === q*den + r, 0 <= r <= den-1 (two 64-bit range checks),
// and q < 2^qBits with qBits in {64, 128} (selected at compile time).
// These are the only `<--` hints in the repo; both targets are fully bound by
// the === plus the range checks (circomspect CS0005 is expected here).
template DivConst(den, qBits) {
    assert(den > 0);
    assert(den < 2 ** 64);
    assert(qBits == 64 || qBits == 128);
    signal input num;
    signal output q;
    signal r;

    q <-- num \ den;
    r <-- num % den;
    num === q * den + r;

    component rr = Num2Bits(64);
    rr.in <== r;
    component rmax = Num2Bits(64);          // den - 1 - r >= 0
    rmax.in <== den - 1 - r;
    if (qBits == 64) {
        component q64 = Num2Bits(64);
        q64.in <== q;
    } else {
        component q128 = Num2Bits(128);
        q128.in <== q;
    }
}

// out = 1 iff c < x, for a constant c < 2^128 and x range-checked to 128 bits.
template GtConst128(c) {
    assert(c < 2 ** 128);
    signal input x;
    signal output out;
    component b = Num2Bits(129);
    b.in <== x + (2 ** 128) - c - 1;
    out <== b.out[128];
}

// Mirrors src/tcl/ledger/demurrage_fixed.py for a SINGLE Taylor step
// (0 <= dt <= MAX_STEP_SECONDS = 604800). Every intermediate is constrained.
//   x   = LAMBDA * dt
//   t1 = x; t_k = floor(t_{k-1} * x / (k * S))
//   m   = S - t1 + t2 - t3 + t4 - t5   (always > 0 for dt <= 7d)
//   if dt >= 86400: m -= 55
//   surplus = bal > T ? bal - T : 0
//   fee = surplus - floor(surplus * m / S)
template DemurrageStep() {
    var S = 1000000000000000000;            // 1e18
    var T = 5000000000000000000000000;      // 5e6 * 1e18
    var LAMBDA = 5788484761;
    var MAX_STEP = 604800;

    signal input balance;
    signal input dt;
    signal output fee;
    signal output newBalance;

    // range: balance < 2^128, 0 <= dt <= MAX_STEP
    component balBits = Num2Bits(128);
    balBits.in <== balance;
    component dtBits = Num2Bits(20);
    dtBits.in <== dt;
    component dtMax = LessEqThan(20);
    dtMax.in[0] <== dt;
    dtMax.in[1] <== MAX_STEP;
    dtMax.out === 1;

    signal x <== LAMBDA * dt;               // < 2^52

    signal p2 <== x * x;
    component d2 = DivConst(2 * S, 64);
    d2.num <== p2;
    signal p3 <== d2.q * x;
    component d3 = DivConst(3 * S, 64);
    d3.num <== p3;
    signal p4 <== d3.q * x;
    component d4 = DivConst(4 * S, 64);
    d4.num <== p4;
    signal p5 <== d4.q * x;
    component d5 = DivConst(5 * S, 64);
    d5.num <== p5;

    signal m0 <== S - x + d2.q - d3.q + d4.q - d5.q;
    component mBits = Num2Bits(64);         // proves m0 >= 0 (no field wrap)
    mBits.in <== m0;

    component dayGe = GreaterEqThan(20);
    dayGe.in[0] <== dt;
    dayGe.in[1] <== 86400;
    signal mult <== m0 - 55 * dayGe.out;
    component multBits = Num2Bits(64);
    multBits.in <== mult;

    component above = GtConst128(T);
    above.x <== balance;
    signal surplus <== above.out * (balance - T);

    signal prod <== surplus * mult;         // < 2^192
    component dd = DivConst(S, 128);
    dd.num <== prod;

    fee <== surplus - dd.q;
    component feeBits = Num2Bits(128);      // decayed <= surplus
    feeBits.in <== fee;
    newBalance <== balance - fee;
}
