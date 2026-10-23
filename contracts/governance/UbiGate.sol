// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

/// @title UbiGate
/// @notice G_UBI from spec decision 3. Toggling is not a ParameterRegistry call, so the timelock
///         classifies it as constitutional: 30-day delay and a >66% supermajority in ConvictionVoting.
contract UbiGate {
    address public immutable timelock;
    bool public enabled;

    event UbiGateSet(bool enabled);

    error NotTimelock();

    constructor(address timelock_) {
        timelock = timelock_;
    }

    function setEnabled(bool enabled_) external {
        if (msg.sender != timelock) revert NotTimelock();
        enabled = enabled_;
        emit UbiGateSet(enabled_);
    }
}
