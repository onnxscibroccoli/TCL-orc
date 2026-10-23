// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {CirculatoryEngine} from "../../contracts/CirculatoryEngine.sol";

/// @notice Deploy a new CirculatoryEngine implementation and either upgrade the proxy
///         directly (owner = signer, dev chains) or print the calldata to queue on the
///         timelock (production). Rollback = run again with IMPLEMENTATION set to the
///         previous implementation address (no new deploy).
///   Required env: PROXY.  Optional: IMPLEMENTATION (reuse existing impl), EXECUTE=true.
contract UpgradeEngine is Script {
    function run() external {
        CirculatoryEngine proxy = CirculatoryEngine(payable(vm.envAddress("PROXY")));
        address impl = vm.envOr("IMPLEMENTATION", address(0));
        bool execute = vm.envOr("EXECUTE", false);
        vm.startBroadcast();
        if (impl == address(0)) impl = address(new CirculatoryEngine());
        bytes memory data = abi.encodeCall(proxy.upgradeToAndCall, (impl, bytes("")));
        if (execute) proxy.upgradeToAndCall(impl, "");
        vm.stopBroadcast();
        console2.log("implementation", impl);
        console2.log("upgrade calldata (target = PROXY):");
        console2.logBytes(data);
        bytes32 slot = vm.load(address(proxy), bytes32(uint256(keccak256("eip1967.proxy.implementation")) - 1));
        if (execute) require(address(uint160(uint256(slot))) == impl, "upgrade not applied");
    }
}
