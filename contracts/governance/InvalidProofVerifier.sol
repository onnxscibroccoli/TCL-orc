// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {IEvidenceVerifier} from "./BountyModule.sol";
import {OffenderId} from "./OffenderId.sol";
import {IStateVerifier} from "../CirculatoryEngine.sol";

/// @title InvalidProofVerifier
/// @notice Evidence: a Groth16 settlement proof the offender (prover) signed as a submission, which the
///         canonical state verifier rejects. Submission digest = keccak256(abi.encode(DOMAIN, chainid,
///         stateVerifier, pA, pB, pC, pubSignals)).
contract InvalidProofVerifier is IEvidenceVerifier {
    bytes32 public constant DOMAIN = keccak256("TCL-PROOF-SUBMISSION-v1");
    IStateVerifier public immutable stateVerifier;

    constructor(IStateVerifier stateVerifier_) {
        stateVerifier = stateVerifier_;
    }

    function submissionDigest(
        uint256[2] memory pA,
        uint256[2][2] memory pB,
        uint256[2] memory pC,
        uint256[4] memory pubSignals
    ) public view returns (bytes32) {
        return keccak256(abi.encode(DOMAIN, block.chainid, address(stateVerifier), pA, pB, pC, pubSignals));
    }

    /// @param evidence abi.encode(pA, pB, pC, pubSignals, sig)
    function verify(bytes32 offenderId, bytes calldata evidence) external view returns (bool) {
        (address offender, bool ok) = OffenderId.toAddress(offenderId);
        if (!ok || offender == address(0)) return false;
        (uint256[2] memory pA, uint256[2][2] memory pB, uint256[2] memory pC, uint256[4] memory ps, bytes memory sig) =
            abi.decode(evidence, (uint256[2], uint256[2][2], uint256[2], uint256[4], bytes));
        bytes32 d = MessageHashUtils.toEthSignedMessageHash(submissionDigest(pA, pB, pC, ps));
        (address r, ECDSA.RecoverError e,) = ECDSA.tryRecover(d, sig);
        if (e != ECDSA.RecoverError.NoError || r != offender) return false;
        // Fail-closed: a reverting verifier call counts as "not proven invalid".
        try stateVerifier.verifyProof(pA, pB, pC, ps) returns (bool valid) {
            return !valid;
        } catch {
            return false;
        }
    }
}
