// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "../PhysicalMintController.sol";
import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/interfaces/IERC1271.sol";
import "./ProtocolTimelock.sol";

/// @notice Deployment policy and enrollment admission around the unchanged redemption statement.
contract ProductionMintController is PhysicalMintController, EIP712 {
    bytes32 private constant ENROLLMENT_TYPEHASH = keccak256(
        "Enrollment(bytes32 commitment,uint256 value,address depositor,bytes32 enrollmentId,uint256 expiresAt,bytes32 releaseManifest)"
    );
    bytes32 public immutable releaseManifestHash;
    bytes32 public immutable verifierCodeHash;
    address public immutable governance;
    address public immutable guardian;
    address public enrollmentAuthority;
    bool public depositsPaused = true;
    bool public redemptionsPaused;
    mapping(bytes32 => bool) public usedEnrollments;

    event DepositsPaused(address indexed caller);
    event DepositsResumed();
    event RedemptionsPaused(address indexed caller);
    event RedemptionsResumed();
    event EnrollmentAuthorityChanged(address indexed previous, address indexed current);
    event EnrollmentConsumed(bytes32 indexed enrollmentId, bytes32 indexed commitment);

    modifier onlyGovernance() { require(msg.sender == governance, "delayed governance only"); _; }
    modifier emergencyAuthority() { require(msg.sender == guardian || msg.sender == governance, "incident authority only"); _; }

    constructor(address engine, address verifier_, address governance_, address guardian_,
        bytes32 releaseHash, bytes32 expectedVerifierCodeHash, address issuer)
        PhysicalMintController(engine, verifier_)
        EIP712("TCL Physical Enrollment", "1")
    {
        require(releaseHash != bytes32(0), "release manifest required");
        require(verifier_.codehash == expectedVerifierCodeHash, "verifier code mismatch");
        require(governance_.code.length > 0 && guardian_ != address(0) && issuer != address(0), "policy address");
        require(governance_.codehash == keccak256(type(ProtocolTimelock).runtimeCode), "governance implementation");
        require(ProtocolTimelock(payable(governance_)).getMinDelay() >= 2 days, "governance delay");
        governance = governance_;
        guardian = guardian_;
        releaseManifestHash = releaseHash;
        verifierCodeHash = expectedVerifierCodeHash;
        enrollmentAuthority = issuer;
    }

    /// @dev Plain locks are disabled in this production admission adapter.
    function lockNote(bytes32, uint256) public pure override { revert("enrollment required"); }

    function lockEnrolledNote(bytes32 commitment, uint256 value, bytes32 enrollmentId,
        uint256 expiresAt, bytes calldata signature) external
    {
        require(enrollmentId != bytes32(0) && !usedEnrollments[enrollmentId], "enrollment reused");
        require(expiresAt >= block.timestamp && expiresAt <= block.timestamp + 7 days, "enrollment expiry");
        bytes32 digest = _hashTypedDataV4(keccak256(abi.encode(ENROLLMENT_TYPEHASH,
            commitment, value, msg.sender, enrollmentId, expiresAt, releaseManifestHash)));
        require(_validEnrollmentSignature(digest, signature), "enrollment signature");
        usedEnrollments[enrollmentId] = true;
        // The base lock owns the reentrancy guard and atomic ledger debit.
        super.lockNote(commitment, value);
        emit EnrollmentConsumed(enrollmentId, commitment);
    }

    function _validEnrollmentSignature(bytes32 digest, bytes calldata signature) private view returns (bool) {
        if (enrollmentAuthority.code.length == 0) {
            (address recovered, ECDSA.RecoverError error,) = ECDSA.tryRecover(digest, signature);
            return error == ECDSA.RecoverError.NoError && recovered == enrollmentAuthority;
        }
        (bool success, bytes memory result) = enrollmentAuthority.staticcall(
            abi.encodeCall(IERC1271.isValidSignature, (digest, signature)));
        return success && result.length >= 32 && abi.decode(result, (bytes4)) == IERC1271.isValidSignature.selector;
    }

    function pauseDeposits() external emergencyAuthority { depositsPaused = true; emit DepositsPaused(msg.sender); }
    function resumeDeposits() external onlyGovernance { depositsPaused = false; emit DepositsResumed(); }
    function pauseRedemptions() external emergencyAuthority { redemptionsPaused = true; emit RedemptionsPaused(msg.sender); }
    function resumeRedemptions() external onlyGovernance { redemptionsPaused = false; emit RedemptionsResumed(); }
    function setEnrollmentAuthority(address issuer) external onlyGovernance {
        require(issuer != address(0), "zero issuer");
        address previous = enrollmentAuthority;
        enrollmentAuthority = issuer;
        emit EnrollmentAuthorityChanged(previous, issuer);
    }

    function _beforeLock() internal view override { require(!depositsPaused, "deposits paused"); }
    function _beforeRedeem() internal view override {
        require(!redemptionsPaused, "redemptions paused");
        require(address(verifier).codehash == verifierCodeHash, "verifier code changed");
    }
}
