// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

interface ICirculatoryLedger {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function physicalMint() external view returns (address);
}
interface IPufVerifier {
    function verifyProof(uint256[2] calldata a, uint256[2][2] calldata b,
        uint256[2] calldata c, uint256[5] calldata publicSignals) external view returns (bool);
}

/// @notice Transparent escrow with private-secret proofs; not a shielded payment pool.
contract PhysicalMintController {
    uint256 public constant FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617;
    struct NoteCommitment { bool exists; bool locked; uint128 value; }
    mapping(bytes32 => NoteCommitment) public notes;
    mapping(bytes32 => bool) public usedNullifiers;
    ICirculatoryLedger public immutable circulatoryEngine;
    IPufVerifier public immutable verifier;
    uint256 public immutable context;
    uint256 public totalEscrowed;
    bool private entered;

    event NoteLocked(bytes32 indexed commitment, uint256 value);
    event NoteRedeemed(bytes32 indexed commitment, bytes32 indexed nullifier, address indexed recipient, uint256 value);
    modifier nonReentrant() { require(!entered, "reentrant"); entered = true; _; entered = false; }

    constructor(address engine, address verifier_) {
        require(engine.code.length > 0 && verifier_.code.length > 0, "contract required");
        circulatoryEngine = ICirculatoryLedger(engine);
        verifier = IPufVerifier(verifier_);
        context = uint256(keccak256(abi.encode(block.chainid, address(this)))) % FIELD;
    }

    function lockNote(bytes32 commitment, uint256 value) external nonReentrant {
        require(circulatoryEngine.physicalMint() == address(this), "not configured");
        require(uint256(commitment) > 0 && uint256(commitment) < FIELD, "commitment range");
        require(value > 0 && value <= type(uint128).max, "value range");
        require(!notes[commitment].exists, "already exists");
        notes[commitment] = NoteCommitment(true, true, uint128(value));
        totalEscrowed += value;
        require(circulatoryEngine.transferFrom(msg.sender, address(this), value), "escrow transfer");
        emit NoteLocked(commitment, value);
    }

    /// @dev Proof encodes a, b, c as eight uint256 coordinates in snarkjs Solidity order.
    function redeemNote(bytes32 commitment, bytes32 nullifier, address recipient, bytes calldata proof) external nonReentrant {
        NoteCommitment storage note = notes[commitment];
        require(note.locked, "not locked");
        require(recipient != address(0) && recipient != address(this), "invalid recipient");
        require(uint256(nullifier) > 0 && uint256(nullifier) < FIELD, "nullifier range");
        require(!usedNullifiers[nullifier], "spent nullifier");
        require(proof.length == 256, "proof length");
        (uint256[2] memory a, uint256[2][2] memory b, uint256[2] memory c) =
            abi.decode(proof, (uint256[2], uint256[2][2], uint256[2]));
        uint256[5] memory signals = [uint256(commitment), uint256(nullifier), uint256(note.value), uint256(uint160(recipient)), context];
        require(verifier.verifyProof(a, b, c, signals), "invalid proof");
        note.locked = false;
        usedNullifiers[nullifier] = true;
        totalEscrowed -= note.value;
        require(circulatoryEngine.transfer(recipient, note.value), "redemption transfer");
        emit NoteRedeemed(commitment, nullifier, recipient, note.value);
    }
}
