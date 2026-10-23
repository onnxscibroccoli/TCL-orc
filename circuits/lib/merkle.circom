pragma circom 2.1.6;

include "circomlib/circuits/poseidon.circom";

// Boolean-constrained 2-way swap: bit=0 -> (cur, sib), bit=1 -> (sib, cur).
template OrderedPair() {
    signal input cur;
    signal input sib;
    signal input bit;
    signal output left;
    signal output right;

    bit * (bit - 1) === 0;
    left <== cur + bit * (sib - cur);
    right <== cur + sib - left;
}

// Recomputes a Poseidon(2) binary Merkle root from a leaf and a bottom-up path.
// Matches src/tcl/ledger/poseidon_tree.py: node = Poseidon(left, right).
template MerkleRoot(depth) {
    signal input leaf;
    signal input pathElements[depth];
    signal input pathIndices[depth];
    signal output root;

    component pairs[depth];
    component hashers[depth];
    signal levels[depth + 1];
    levels[0] <== leaf;
    for (var i = 0; i < depth; i++) {
        pairs[i] = OrderedPair();
        pairs[i].cur <== levels[i];
        pairs[i].sib <== pathElements[i];
        pairs[i].bit <== pathIndices[i];
        hashers[i] = Poseidon(2);
        hashers[i].inputs[0] <== pairs[i].left;
        hashers[i].inputs[1] <== pairs[i].right;
        levels[i + 1] <== hashers[i].out;
    }
    root <== levels[depth];
}
