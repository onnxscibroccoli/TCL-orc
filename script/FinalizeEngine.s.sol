// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {CirculatoryEngine} from "../contracts/CirculatoryEngine.sol";
import {GovernanceTimelock} from "../contracts/governance/GovernanceTimelock.sol";

/// @notice Key-removal stage 3 (spec §10.4): build the governance proposal that makes the engine immutable by
///         renouncing ownership from the timelock. `_authorizeUpgrade` is `onlyOwner`, so with owner == 0 every
///         upgrade reverts permanently (as do pause/unpause/redistribute/setSettler).
///         IRREVERSIBLE. Gates (all fail-closed):
///         - engine owner is the GovernanceTimelock (stage 2 done) with production delays;
///         - engine is not paused (it could never be unpaused again);
///         - settler is a real, non-timelock address (settlement must survive the owner);
///         - the emergency guardian is not active (it would only be able to fail);
///         - CONFIRM_FINALIZE env equals the engine address.
///         Output: target/value/calldata to submit through ConvictionVoting.propose (constitutional, 30d).
contract FinalizeEngine is Script {
    error NotTimelockOwned(address owner);
    error TimelockDelayTooShort();
    error EnginePaused();
    error BadSettler(address settler);
    error GuardianStillActive();
    error NotConfirmed();

    function run() external view returns (address target, uint256 value, bytes memory data) {
        CirculatoryEngine engine = CirculatoryEngine(vm.envAddress("ENGINE"));
        GovernanceTimelock tl = GovernanceTimelock(payable(vm.envAddress("TIMELOCK")));
        if (vm.envOr("CONFIRM_FINALIZE", address(0)) != address(engine)) revert NotConfirmed();
        (target, value, data) = build(engine, tl);
        console2.log("propose to ConvictionVoting -> target", target);
        console2.logBytes(data);
    }

    function build(CirculatoryEngine engine, GovernanceTimelock tl)
        public
        view
        returns (address target, uint256 value, bytes memory data)
    {
        if (engine.owner() != address(tl)) revert NotTimelockOwned(engine.owner());
        if (tl.paramDelay() < tl.PROD_PARAM_DELAY() || tl.upgradeDelay() < tl.PROD_UPGRADE_DELAY()) {
            revert TimelockDelayTooShort();
        }
        if (engine.paused()) revert EnginePaused();
        address s = engine.settler();
        if (s == address(0) || s == address(tl)) revert BadSettler(s);
        if (tl.guardianActive()) revert GuardianStillActive();
        return (address(engine), 0, abi.encodeWithSignature("renounceOwnership()"));
    }
}
