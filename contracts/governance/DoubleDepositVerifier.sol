// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {IEvidenceVerifier} from "./BountyModule.sol";
import {OffenderId} from "./OffenderId.sol";

/// @title DoubleDepositVerifier
/// @notice Evidence: two distinct deposit authorizations, both signed (EIP-191) by the offender, for the same
///         note era (noteId, k). Message = keccak256(abi.encode(DOMAIN, chainid, verifyingContract, noteId, k,
///         recipient, amount)). Two different messages for one (noteId, k) is a conflicting-nullifier attempt
///         (spec §4.4–§4.6: the era nullifier must be spent exactly once).
contract DoubleDepositVerifier is IEvidenceVerifier {
    bytes32 public constant DOMAIN = keccak256("TCL-NOTE-DEPOSIT-v1");
    /// @notice Contract that deposit authorizations are bound to (e.g. NoteController).
    address public immutable depositContract;

    struct Auth {
        address recipient;
        uint256 amount;
        bytes sig;
    }

    constructor(address depositContract_) {
        depositContract = depositContract_;
    }

    function depositDigest(uint256 noteId, uint256 era, address recipient, uint256 amount)
        public
        view
        returns (bytes32)
    {
        return keccak256(abi.encode(DOMAIN, block.chainid, depositContract, noteId, era, recipient, amount));
    }

    /// @param evidence abi.encode(noteId, era, Auth a, Auth b)
    function verify(bytes32 offenderId, bytes calldata evidence) external view returns (bool) {
        (address offender, bool ok) = OffenderId.toAddress(offenderId);
        if (!ok || offender == address(0)) return false;
        (uint256 noteId, uint256 era, Auth memory a, Auth memory b) =
            abi.decode(evidence, (uint256, uint256, Auth, Auth));
        bytes32 da = depositDigest(noteId, era, a.recipient, a.amount);
        bytes32 db = depositDigest(noteId, era, b.recipient, b.amount);
        if (da == db) return false;
        return _signedBy(da, a.sig, offender) && _signedBy(db, b.sig, offender);
    }

    function _signedBy(bytes32 digest, bytes memory sig, address who) private pure returns (bool) {
        // slither-disable-next-line unused-return (justified in docs/SECURITY.md)
        (address r, ECDSA.RecoverError e,) = ECDSA.tryRecover(MessageHashUtils.toEthSignedMessageHash(digest), sig);
        return e == ECDSA.RecoverError.NoError && r == who;
    }
}
