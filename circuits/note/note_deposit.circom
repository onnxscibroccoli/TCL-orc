pragma circom 2.1.6;

// TCL reusable bearer note: deposit (re-entry) proof. Spec docs/11 §4.2, §4.4.
//
// SIMULATION NOTICE: in the target design the note secret lives in a secure
// element (SE) and never leaves it; the PUF response comes from the substrate.
// In this MVP the secret is a field element known to the prover and pufHash is
// an opaque field element. No real hardware is involved.
//
// Proves knowledge of noteSecret, pufHash, serial such that
//   pkHash = Poseidon(noteSecret)                       (stand-in for pk_id)
//   id     = Poseidon(10, pkHash, pufHash, Poseidon(denom, serial))
//   nf     = Poseidon(12, id, era, ctx)
// and binds the proof to recipient (anti front-running) and ctx (chain+contract).
// (id packs (d, serial) into one hash because the reference Poseidon supports
// at most 4 inputs; documented deviation from H(10, pk, H(rho), d, serial).)
//
// Public inputs (snarkjs order): id, denom, era, ctx, recipient, nf.

include "../lib/tcl/poseidon.circom";
include "../lib/tcl/bitify.circom";

template NoteDeposit() {
    signal input id;
    signal input denom;
    signal input era;
    signal input ctx;
    signal input recipient;
    signal input nf;

    signal input noteSecret;
    signal input pufHash;
    signal input serial;

    // range checks: denom < 2^32, era < 2^64, recipient < 2^160, serial < 2^64
    component dBits = Num2Bits(32);
    dBits.in <== denom;
    component eBits = Num2Bits(64);
    eBits.in <== era;
    component rBits = Num2Bits(160);
    rBits.in <== recipient;
    component sBits = Num2Bits(64);
    sBits.in <== serial;

    component pk = Poseidon(1);
    pk.inputs[0] <== noteSecret;

    component ds = Poseidon(2);
    ds.inputs[0] <== denom;
    ds.inputs[1] <== serial;

    component idH = Poseidon(4);
    idH.inputs[0] <== 10;
    idH.inputs[1] <== pk.out;
    idH.inputs[2] <== pufHash;
    idH.inputs[3] <== ds.out;
    idH.out === id;

    component nfH = Poseidon(4);
    nfH.inputs[0] <== 12;
    nfH.inputs[1] <== id;
    nfH.inputs[2] <== era;
    nfH.inputs[3] <== ctx;
    nfH.out === nf;

    // recipient is bound as a Groth16 public input (snarkjs adds a
    // per-public-input constraint), and range-checked above.
}

component main {public [id, denom, era, ctx, recipient, nf]} = NoteDeposit();
