pragma circom 2.2.0;
include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/bitify.circom";
include "circomlib/circuits/comparators.circom";

// Proves knowledge of an enrolled, reconstructed secret, not physical unclonability.
template PufVerify() {
    signal input secret;
    signal input blinding;
    signal input commitment;
    signal input nullifier;
    signal input value;
    signal input recipient;
    signal input context;

    component valueBits = Num2Bits(128);
    valueBits.in <== value;
    component recipientBits = Num2Bits(160);
    recipientBits.in <== recipient;
    component nonzeroValue = IsZero();
    nonzeroValue.in <== value;
    nonzeroValue.out === 0;
    component nonzeroRecipient = IsZero();
    nonzeroRecipient.in <== recipient;
    nonzeroRecipient.out === 0;
    component nonzeroSecret = IsZero();
    nonzeroSecret.in <== secret;
    nonzeroSecret.out === 0;

    component note = Poseidon(5);
    note.inputs[0] <== 1;
    note.inputs[1] <== secret;
    note.inputs[2] <== blinding;
    note.inputs[3] <== value;
    note.inputs[4] <== context;
    note.out === commitment;

    component spent = Poseidon(5);
    spent.inputs[0] <== 2;
    spent.inputs[1] <== secret;
    spent.inputs[2] <== commitment;
    spent.inputs[3] <== context;
    spent.inputs[4] <== recipient;
    spent.out === nullifier;
}

// Input order is shared by the controller and generated Groth16 verifier.
component main {public [commitment, nullifier, value, recipient, context]} = PufVerify();
