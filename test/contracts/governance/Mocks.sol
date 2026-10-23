// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {IPersonhood} from "../../../contracts/governance/IPersonhood.sol";
import {IEvidenceVerifier} from "../../../contracts/governance/BountyModule.sol";

/// @notice TEST ONLY Semaphore-style mock: identity commitment = keccak(secret);
///         nullifierHash = keccak(15, secret, externalNullifier); proof = abi.encode(secret).
contract MockSemaphorePersonhood is IPersonhood {
    mapping(bytes32 => bool) public members;
    uint256 public override personCount;

    function addMember(uint256 secret) external {
        bytes32 c = keccak256(abi.encode(secret));
        if (!members[c]) {
            members[c] = true;
            personCount++;
        }
    }

    function nullifierOf(uint256 secret, uint256 ext) public pure returns (uint256) {
        return uint256(keccak256(abi.encode(uint256(15), secret, ext)));
    }

    function verifyPersonhood(uint256 nullifierHash, uint256 ext, uint256, bytes calldata proof)
        external
        view
        override
        returns (bool)
    {
        if (proof.length != 32) return false;
        uint256 secret = abi.decode(proof, (uint256));
        return members[keccak256(abi.encode(secret))] && nullifierOf(secret, ext) == nullifierHash;
    }
}

contract MockEvidenceVerifier is IEvidenceVerifier {
    function verify(bytes32 offenderId, bytes calldata evidence) external pure returns (bool) {
        return keccak256(evidence) == keccak256(abi.encode("valid", offenderId));
    }
}
