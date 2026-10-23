pragma circom 2.1.6;

// TCL state-transition circuit.
//
// Proves the COMPLETE off-chain Ledger.settle(account, newTime) transition
// (src/tcl/ledger/service.py), i.e. two sequential leaf updates:
//
//   oldRoot --(account leaf)--> midRoot --(vault leaf)--> newRoot
//
//   1. leaf = Poseidon(accountId, oldBalance, oldTime) is in oldRoot at a
//      private index != VAULT_INDEX, and accountId != VAULT_ID;
//   2. fee/newBalance follow one fixed-point demurrage step
//      (src/tcl/ledger/demurrage_fixed.py, single step dt <= 7 days);
//   3. replacing that leaf with Poseidon(accountId, newBalance, newTime)
//      gives midRoot;
//   4. the vault leaf at the reserved index VAULT_INDEX (= 0) in midRoot is
//      either empty (vaultExists = 0, vault balance/time 0) or
//      Poseidon(VAULT_ID, vaultBalance, vaultTime);
//   5. if fee != 0 the vault leaf becomes Poseidon(VAULT_ID, vaultBalance + fee,
//      newTime), otherwise it is unchanged (the ledger skips the vault write);
//      the result is newRoot == Ledger.root() after settle.
// Public signals (in order): newRoot, fee, oldRoot, newTime.
// (circom lists outputs first, then public inputs.)

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/bitify.circom";
include "circomlib/circuits/comparators.circom";
include "lib/merkle.circom";
include "lib/fixedpoint.circom";

// hash_account_id("__vault__") = sha256(label) mod p; pinned by tests/test_circuit_hygiene.py
function VAULT_ID() {
    return 12660422312174870211468219279201038222137982397726769215734775967146706617803;
}

template StateTransition(depth) {
    signal input oldRoot;
    signal input newTime;

    signal input accountId;
    signal input oldBalance;
    signal input oldTime;
    signal input pathElements[depth];
    signal input pathIndices[depth];

    signal input vaultExists;
    signal input vaultBalance;
    signal input vaultTime;
    signal input vaultPathElements[depth];   // siblings of leaf 0 in midRoot

    signal output newRoot;
    signal output fee;

    component tBits = Num2Bits(64);
    tBits.in <== oldTime;
    signal dt <== newTime - oldTime;          // range-checked inside DemurrageStep

    component step = DemurrageStep();
    step.balance <== oldBalance;
    step.dt <== dt;
    fee <== step.fee;

    // ---- account is not the vault (distinct id AND distinct leaf index) ----
    component idIsVault = IsEqual();
    idIsVault.in[0] <== accountId;
    idIsVault.in[1] <== VAULT_ID();
    idIsVault.out === 0;
    var idx = 0;
    var pow = 1;
    for (var i = 0; i < depth; i++) {
        idx += pathIndices[i] * pow;          // bits are boolean-constrained in MerkleRoot
        pow *= 2;
    }
    component idxIsZero = IsZero();
    idxIsZero.in <== idx;
    idxIsZero.out === 0;

    // ---- step 1: account leaf, oldRoot -> midRoot ----
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
    component midPath = MerkleRoot(depth);
    midPath.leaf <== newLeaf.out;
    for (var i = 0; i < depth; i++) {
        midPath.pathElements[i] <== pathElements[i];
        midPath.pathIndices[i] <== pathIndices[i];
    }
    signal midRoot <== midPath.root;

    // ---- step 2: vault leaf (index 0), midRoot -> newRoot ----
    vaultExists * (vaultExists - 1) === 0;
    // an absent vault has no balance/time (so its leaf is the empty value 0)
    (1 - vaultExists) * vaultBalance === 0;
    (1 - vaultExists) * vaultTime === 0;
    component vbBits = Num2Bits(128);
    vbBits.in <== vaultBalance;
    signal newVaultBalance <== vaultBalance + fee;
    component nvbBits = Num2Bits(128);
    nvbBits.in <== newVaultBalance;

    component vOld = Poseidon(3);
    vOld.inputs[0] <== VAULT_ID();
    vOld.inputs[1] <== vaultBalance;
    vOld.inputs[2] <== vaultTime;
    signal vaultOldLeaf <== vaultExists * vOld.out;

    component vMid = MerkleRoot(depth);
    vMid.leaf <== vaultOldLeaf;
    for (var i = 0; i < depth; i++) {
        vMid.pathElements[i] <== vaultPathElements[i];
        vMid.pathIndices[i] <== 0;
    }
    vMid.root === midRoot;

    component vNew = Poseidon(3);
    vNew.inputs[0] <== VAULT_ID();
    vNew.inputs[1] <== newVaultBalance;
    vNew.inputs[2] <== newTime;
    component feeIsZero = IsZero();
    feeIsZero.in <== fee;
    // fee == 0 -> vault leaf unchanged; else updated leaf
    signal vaultNewLeaf <== vNew.out + feeIsZero.out * (vaultOldLeaf - vNew.out);

    component vPath = MerkleRoot(depth);
    vPath.leaf <== vaultNewLeaf;
    for (var i = 0; i < depth; i++) {
        vPath.pathElements[i] <== vaultPathElements[i];
        vPath.pathIndices[i] <== 0;
    }
    newRoot <== vPath.root;
}
