// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

/// @title IPluralWeight
/// @notice Plural (correlation) discount hook, spec §10.6: W_i = 1 / sqrt(1 + sum_j sigma(i,j)).
/// @dev Returns a WAD weight in (0, 1e18]; ConvictionVoting clamps to 1e18 and uses 1e18 when unset.
///      A production implementation would verify a ZK proof of the voter's graph neighbourhood overlap
///      bound to the vote nullifier; none exists yet.
interface IPluralWeight {
    function weightOf(uint256 proposalId, uint256 nullifierHash) external view returns (uint256);
}
