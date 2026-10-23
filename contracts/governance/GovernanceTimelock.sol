// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

/// @title GovernanceTimelock
/// @notice Two-class timelock (spec §10.3): bounded parameter changes wait `paramDelay` (14 days in
///         production); every other call (upgrades, ownership, pause, role changes, delay changes) waits
///         `upgradeDelay` (30 days). Classification is fail-closed: only an exact
///         `ParameterRegistry.setParameter` call on the registry qualifies for the short delay.
contract GovernanceTimelock is TimelockController {
    bytes4 public constant SET_PARAMETER_SELECTOR = bytes4(keccak256("setParameter(bytes32,uint256)"));
    uint256 public constant PROD_PARAM_DELAY = 14 days;
    uint256 public constant PROD_UPGRADE_DELAY = 30 days;

    uint256 public immutable paramDelay;
    uint256 public immutable upgradeDelay;
    address public registry;

    error DelayTooShort(uint256 given, uint256 required);
    error BadDelays();
    error RegistryAlreadySet();
    error NotAdmin();

    constructor(
        uint256 paramDelay_,
        uint256 upgradeDelay_,
        address[] memory proposers,
        address[] memory executors,
        address admin
    ) TimelockController(paramDelay_, proposers, executors, admin) {
        if (paramDelay_ == 0 || upgradeDelay_ < paramDelay_) revert BadDelays();
        paramDelay = paramDelay_;
        upgradeDelay = upgradeDelay_;
    }

    /// @notice One-shot binding of the ParameterRegistry (it needs this timelock's address first).
    function setRegistry(address registry_) external {
        if (!hasRole(DEFAULT_ADMIN_ROLE, msg.sender)) revert NotAdmin();
        if (registry != address(0)) revert RegistryAlreadySet();
        registry = registry_;
    }

    function requiredDelay(address target, bytes calldata data) public view returns (uint256) {
        if (
            registry != address(0) && target == registry && data.length == 68
                && bytes4(data[:4]) == SET_PARAMETER_SELECTOR
        ) {
            return paramDelay;
        }
        return upgradeDelay;
    }

    function schedule(
        address target,
        uint256 value,
        bytes calldata data,
        bytes32 predecessor,
        bytes32 salt,
        uint256 delay
    ) public override {
        uint256 req = requiredDelay(target, data);
        if (delay < req) revert DelayTooShort(delay, req);
        super.schedule(target, value, data, predecessor, salt, delay);
    }

    function scheduleBatch(
        address[] calldata targets,
        uint256[] calldata values,
        bytes[] calldata payloads,
        bytes32 predecessor,
        bytes32 salt,
        uint256 delay
    ) public override {
        uint256 req = paramDelay;
        for (uint256 i = 0; i < targets.length; ++i) {
            uint256 r = requiredDelay(targets[i], payloads[i]);
            if (r > req) req = r;
        }
        if (delay < req) revert DelayTooShort(delay, req);
        super.scheduleBatch(targets, values, payloads, predecessor, salt, delay);
    }
}
