// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/// @title PhysicalMintController
/// @notice Skeleton for zero-knowledge physical note minting and escrow.
contract PhysicalMintController {
    struct NoteCommitment {
        bytes32 commitment;
        bool locked;
        uint256 value;
    }

    mapping(bytes32 => NoteCommitment) public notes;
    address public circulatoryEngine;

    event NoteLocked(bytes32 indexed commitment, uint256 value);
    event NoteRedeemed(bytes32 indexed commitment);

    function lockNote(bytes32 commitment, uint256 value) external {
        require(notes[commitment].commitment == bytes32(0), "already exists");
        notes[commitment] = NoteCommitment({
            commitment: commitment,
            locked: true,
            value: value
        });
        emit NoteLocked(commitment, value);
    }

    function redeemNote(bytes32 commitment, bytes calldata zkProof) external {
        NoteCommitment storage note = notes[commitment];
        require(note.locked, "not locked");
        // Placeholder: verify ZK proof of PUF ownership and nullifier
        // require(verifyPufProof(zkProof, commitment), "invalid proof");
        note.locked = false;
        emit NoteRedeemed(commitment);
    }
}
