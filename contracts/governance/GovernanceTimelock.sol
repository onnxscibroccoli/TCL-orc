// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

interface IPausableTarget {
    function pause() external;
}

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
    error NotGuardian();
    error GuardianExpired();
    error NotPauseTarget(address target);
    error SunsetTooLong(uint256 sunset);
    error PauseFailed(address target);

    /// @notice Longest emergency-guardian term that may ever be granted.
    uint256 public constant MAX_GUARDIAN_SUNSET = 180 days;

    /// @notice Emergency guardian (key-removal stage 1.5): may only call `pause()` on allowlisted targets
    ///         through this timelock (which owns them). Cannot unpause, upgrade or move funds.
    address public guardian;
    uint256 public guardianExpiry;
    mapping(address => bool) public pauseTarget;

    event GuardianSet(address indexed guardian, uint256 expiry);
    event PauseTargetSet(address indexed target, bool allowed);
    event GuardianPaused(address indexed guardian, address indexed target);

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

    /// @notice Grant (or replace) the guardian for `sunset` seconds from now. Admin during bootstrap;
    ///         afterwards only this timelock itself, i.e. a constitutional proposal with the 30-day delay.
    function setGuardian(address guardian_, uint256 sunset) external {
        if (!hasRole(DEFAULT_ADMIN_ROLE, msg.sender)) revert NotAdmin();
        if (sunset > MAX_GUARDIAN_SUNSET) revert SunsetTooLong(sunset);
        guardian = guardian_;
        guardianExpiry = guardian_ == address(0) ? 0 : block.timestamp + sunset;
        emit GuardianSet(guardian_, guardianExpiry);
    }

    /// @notice Revoke the guardian. Callable by admin/self (timelock proposal) or by the guardian itself.
    function revokeGuardian() external {
        if (!hasRole(DEFAULT_ADMIN_ROLE, msg.sender) && msg.sender != guardian) revert NotAdmin();
        guardian = address(0);
        guardianExpiry = 0;
        emit GuardianSet(address(0), 0);
    }

    function setPauseTarget(address target, bool allowed) external {
        if (!hasRole(DEFAULT_ADMIN_ROLE, msg.sender)) revert NotAdmin();
        pauseTarget[target] = allowed;
        emit PauseTargetSet(target, allowed);
    }

    function guardianActive() public view returns (bool) {
        return guardian != address(0) && block.timestamp < guardianExpiry;
    }

    /// @notice Emergency pause. Only the hard-coded `pause()` selector can ever be sent.
    function guardianPause(address target) external {
        if (msg.sender != guardian || guardian == address(0)) revert NotGuardian();
        if (block.timestamp >= guardianExpiry) revert GuardianExpired();
        if (!pauseTarget[target]) revert NotPauseTarget(target);
        emit GuardianPaused(msg.sender, target);
        try IPausableTarget(target).pause() {}
        catch {
            revert PauseFailed(target);
        }
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
