// SPDX-License-Identifier: Apache-2.0
// TCL in-house multiplexers (clean-room; not derived from circomlib).
pragma circom 2.1.6;

// Select c[s] for each of n lanes; s must be boolean (constrained here).
template MultiMux1(n) {
    signal input c[n][2];
    signal input s;
    signal output out[n];
    s * (s - 1) === 0;
    for (var i = 0; i < n; i++) {
        out[i] <== c[i][0] + s * (c[i][1] - c[i][0]);
    }
}

template Mux1() {
    signal input c[2];
    signal input s;
    signal output out;
    component m = MultiMux1(1);
    m.c[0][0] <== c[0];
    m.c[0][1] <== c[1];
    m.s <== s;
    out <== m.out[0];
}
