pragma circom 2.1.6;

// TCL state-transition circuit (Phase 3).
//
// Proves, without revealing the account or its balances, that:
//   1. leaf_old = Poseidon(accountId, oldBalance, oldTime) is in oldRoot,
//   2. the account was settled with the fixed-point demurrage step
//      (src/tcl/ledger/demurrage_fixed.py, single step dt <= 7 days),
//   3. leaf_new = Poseidon(accountId, newBalance, newTime) at the SAME index
//      yields newRoot, and newTime = oldTime + dt.
// Public signals (in order): newRoot, fee, oldRoot, newTime.
// (circom lists outputs first, then public inputs.)

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/bitify.circom";
include "lib/merkle.circom";
include "lib/fixedpoint.circom";

template StateTransition(depth) {
    signal input oldRoot;
    signal input newTime;

    signal input accountId;
    signal input oldBalance;
    signal input oldTime;
    signal input pathElements[depth];
    signal input pathIndices[depth];

    signal output newRoot;
    signal output fee;

    component tBits = Num2Bits(64);
    tBits.in <== oldTime;
    signal dt <== newTime - oldTime;          // range-checked inside DemurrageStep

    component step = DemurrageStep();
    step.balance <== oldBalance;
    step.dt <== dt;
    fee <== step.fee;

    component oldLeaf = Poseidon(3);
    oldLeaf.inputs[0] <== accountId;
    oldLeaf.inputs[1] <== oldBalance;
    oldLeaf.inputs[2] <== oldTime;
    component oldPath = MerkleRoot(depth);
    oldPath.leaf <== oldLeaf.out;
    for (var i = 0; i < depth; i++) {
        oldPath.pathElements[i] <== pathElements[i];
        oldPath.pathIndices[i] <== pathIndices[i];
    }
    oldPath.root === oldRoot;

    component newLeaf = Poseidon(3);
    newLeaf.inputs[0] <== accountId;
    newLeaf.inputs[1] <== step.newBalance;
    newLeaf.inputs[2] <== newTime;
    component newPath = MerkleRoot(depth);
    newPath.leaf <== newLeaf.out;
    for (var i = 0; i < depth; i++) {
        newPath.pathElements[i] <== pathElements[i];
        newPath.pathIndices[i] <== pathIndices[i];
    }
    newRoot <== newPath.root;
}
