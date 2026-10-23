// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {CirculatoryEngine} from "../contracts/CirculatoryEngine.sol";
import {GovernanceTimelock} from "../contracts/governance/GovernanceTimelock.sol";

/// @notice Stage 0 -> Stage 2 key removal (spec §10.4): transfer CirculatoryEngine ownership (and with it
///         UUPS upgrade authority, pause, redistribute) from the owner EOA to the GovernanceTimelock.
///         Fail-closed: aborts unless the timelock has code, production delays (>= 14d / >= 30d), and
///         no open admin. Optional SETTLER env replaces the owner-defaulted settler first.
/// Usage: ENGINE=0x.. TIMELOCK=0x.. [SETTLER=0x..] forge script script/HandoffEngineOwnership.s.sol \
///        --rpc-url $RPC --private-key $OWNER_KEY --broadcast
contract HandoffEngineOwnership is Script {
    error TimelockHasNoCode();
    error TimelockDelayTooShort(uint256 paramDelay, uint256 upgradeDelay);
    error TimelockAdminNotRenounced(address admin);
    error CallerNotOwner(address caller, address owner);
    error HandoffFailed();

    function run() external {
        CirculatoryEngine engine = CirculatoryEngine(vm.envAddress("ENGINE"));
        GovernanceTimelock tl = GovernanceTimelock(payable(vm.envAddress("TIMELOCK")));
        address settler = vm.envOr("SETTLER", address(0));
        handoff(engine, tl, settler, msg.sender);
    }

    function check(GovernanceTimelock tl, address oldOwner) public view {
        if (address(tl).code.length == 0) revert TimelockHasNoCode();
        uint256 pd = tl.paramDelay();
        uint256 ud = tl.upgradeDelay();
        if (pd < tl.PROD_PARAM_DELAY() || ud < tl.PROD_UPGRADE_DELAY() || tl.getMinDelay() < pd) {
            revert TimelockDelayTooShort(pd, ud);
        }
        if (tl.hasRole(tl.DEFAULT_ADMIN_ROLE(), oldOwner)) revert TimelockAdminNotRenounced(oldOwner);
    }

    function handoff(CirculatoryEngine engine, GovernanceTimelock tl, address settler, address caller) public {
        check(tl, caller);
        if (engine.owner() != caller) revert CallerNotOwner(caller, engine.owner());
        vm.startBroadcast(caller);
        if (settler != address(0)) engine.setSettler(settler);
        engine.transferOwnership(address(tl));
        vm.stopBroadcast();
        if (engine.owner() != address(tl)) revert HandoffFailed();
        console2.log("CirculatoryEngine owner ->", address(tl));
    }
}
