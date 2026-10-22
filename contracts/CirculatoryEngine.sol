// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/// @title CirculatoryEngine
/// @notice Skeleton for thresholded demurrage and redistribution.
contract CirculatoryEngine {
    uint256 public constant T = 5_000_000 ether; // immunity threshold
    uint256 public constant DELTA = 5e14;       // 0.0005 day^-1 scaled (placeholder)

    mapping(address => uint256) public balances;
    uint256 public vaultBalance;

    event DemurrageApplied(address indexed account, uint256 amount);
    event Redistributed(address indexed recipient, uint256 amount);

    function applyDemurrage(address account, uint256 deltaT) external {
        uint256 bal = balances[account];
        if (bal <= T) return;
        uint256 surplus = bal - T;
        uint256 fee = (surplus * DELTA * deltaT) / 1e18;
        if (fee > surplus) fee = surplus;
        balances[account] -= fee;
        vaultBalance += fee;
        emit DemurrageApplied(account, fee);
    }

    function redistribute(address recipient, uint256 amount) external {
        require(amount <= vaultBalance, "insufficient vault");
        vaultBalance -= amount;
        balances[recipient] += amount;
        emit Redistributed(recipient, amount);
    }
}
