// SPDX-License-Identifier: Apache-2.0
// TCL in-house comparison gadgets (clean-room; not derived from circomlib).
pragma circom 2.1.6;

include "bitify.circom";

// out = 1 iff in == 0. Uses the inverse trick: out = 1 - in * inv, in * out = 0.
template IsZero() {
    signal input in;
    signal output out;
    signal inv;
    inv <-- in != 0 ? 1 / in : 0;
    out <== 1 - in * inv;
    in * out === 0;
}

// out = 1 iff in[0] == in[1].
template IsEqual() {
    signal input in[2];
    signal output out;
    component z = IsZero();
    z.in <== in[1] - in[0];
    out <== z.out;
}

// out = 1 iff in[0] < in[1], for inputs already known to fit in n bits (n <= 252).
// 2^n + a - b has bit n set exactly when a >= b.
template LessThan(n) {
    assert(n <= 252);
    signal input in[2];
    signal output out;
    component b = Num2Bits(n + 1);
    b.in <== in[0] + (1 << n) - in[1];
    out <== 1 - b.out[n];
}

// out = 1 iff in[0] <= in[1].
template LessEqThan(n) {
    signal input in[2];
    signal output out;
    component lt = LessThan(n);
    lt.in[0] <== in[0];
    lt.in[1] <== in[1] + 1;
    out <== lt.out;
}

// out = 1 iff in[0] > in[1].
template GreaterThan(n) {
    signal input in[2];
    signal output out;
    component lt = LessThan(n);
    lt.in[0] <== in[1];
    lt.in[1] <== in[0];
    out <== lt.out;
}

// out = 1 iff in[0] >= in[1].
template GreaterEqThan(n) {
    signal input in[2];
    signal output out;
    component lt = LessThan(n);
    lt.in[0] <== in[1];
    lt.in[1] <== in[0] + 1;
    out <== lt.out;
}
