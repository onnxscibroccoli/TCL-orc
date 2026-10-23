// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

/// @notice offenderId convention for key-based offenders: bytes32(uint256(uint160(signerAddress))).
library OffenderId {
    function toAddress(bytes32 id) internal pure returns (address a, bool ok) {
        if (uint256(id) >> 160 != 0) return (address(0), false);
        return (address(uint160(uint256(id))), true);
    }
}
