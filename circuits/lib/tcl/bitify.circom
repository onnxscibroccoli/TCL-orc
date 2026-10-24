// SPDX-License-Identifier: Apache-2.0
// TCL in-house bit decomposition gadgets (clean-room; not derived from circomlib).
pragma circom 2.1.6;

// Decompose `in` into n little-endian bits; constrains each bit to {0,1}
// and their weighted sum to equal `in` (so `in` < 2^n when n < 254).
template Num2Bits(n) {
    signal input in;
    signal output out[n];
    var acc = 0;
    var w = 1;
    for (var i = 0; i < n; i++) {
        out[i] <-- (in >> i) & 1;
        out[i] * (out[i] - 1) === 0;
        acc += out[i] * w;
        w = w + w;
    }
    acc === in;
}

// Recompose n little-endian bits into a number (bits are NOT range-checked here).
template Bits2Num(n) {
    signal input in[n];
    signal output out;
    var acc = 0;
    var w = 1;
    for (var i = 0; i < n; i++) {
        acc += in[i] * w;
        w = w + w;
    }
    out <== acc;
}
