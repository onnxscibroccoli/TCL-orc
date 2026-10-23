// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

/// @title IPersonhood
/// @notice Pluggable anonymous proof-of-personhood verifier (spec §10.2, §10.5).
/// @dev A production implementation wraps a Semaphore-style Groth16 verifier: `nullifierHash` is
///      H(15, personSecret, externalNullifier) and the proof shows membership of the person's
///      identity commitment in the current personhood Merkle root without revealing it.
///      Implementations MUST return false (never revert-true) on any malformed input: fail-closed.
interface IPersonhood {
    /// @return true iff `proof` proves that a registered, unexpired person produced `nullifierHash`
    ///         for `externalNullifier` (e.g. a proposal id) and bound it to `signal`.
    function verifyPersonhood(uint256 nullifierHash, uint256 externalNullifier, uint256 signal, bytes calldata proof)
        external
        view
        returns (bool);

    /// @return number of currently valid personhood credentials (electorate size N for quorum).
    function personCount() external view returns (uint256);
}
