// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

/// @title ParameterRegistry
/// @notice Bounded, rate-limited governance parameters (spec §10.3).
/// @dev Bounds and per-cycle steps are fixed at construction and can never be changed: even a captured
///      governance can only move x within [min, max] and by at most one step per cycle.
///      All fractional values are 1e18 fixed point; amounts are in base units (1e18 per S); durations in seconds.
///      Only the governance timelock may write. Unknown keys revert (fail-closed).
contract ParameterRegistry {
    uint256 public constant WAD = 1e18;
    uint256 public constant BPS = 10_000;

    // Parameter keys (spec §2 / §10.3 notation).
    bytes32 public constant DEMURRAGE_RATE = keccak256("r_d"); // per-day rate
    bytes32 public constant DEMURRAGE_THRESHOLD = keccak256("T");
    bytes32 public constant REENTRY_THRESHOLD = keccak256("Theta");
    bytes32 public constant HOARD_MULTIPLIER = keccak256("kappa");
    bytes32 public constant GRACE_PERIOD = keccak256("g");
    bytes32 public constant HOARD_HORIZON = keccak256("H");
    bytes32 public constant RELEASE_WINDOW = keccak256("W_r");
    bytes32 public constant MINT_REWARD = keccak256("rho");
    bytes32 public constant COST_CAP = keccak256("gamma_c");
    bytes32 public constant POOL_SHARE = keccak256("phi");
    bytes32 public constant EMISSION_RATE = keccak256("eta");
    bytes32 public constant VOTE_HALF_LIFE = keccak256("t_half"); // conviction half-life (seconds)

    struct Param {
        uint256 value;
        uint256 min;
        uint256 max;
        uint256 maxStepAbs; // used when > 0
        uint256 maxStepBps; // relative step to the old value, used when maxStepAbs == 0
        uint64 lastChangeCycle;
        bool exists;
    }

    address public immutable timelock;
    uint256 public immutable cycleLength;
    uint256 public immutable genesis;

    mapping(bytes32 => Param) private _params;
    bytes32[] private _keys;

    event ParameterChanged(bytes32 indexed key, uint256 oldValue, uint256 newValue, uint256 cycle);

    error NotTimelock();
    error UnknownParameter(bytes32 key);
    error OutOfBounds(bytes32 key, uint256 value);
    error StepTooLarge(bytes32 key, uint256 oldValue, uint256 newValue);
    error AlreadyChangedThisCycle(bytes32 key);
    error ZeroAddress();
    error ZeroCycle();

    constructor(address timelock_, uint256 cycleLength_) {
        if (timelock_ == address(0)) revert ZeroAddress();
        if (cycleLength_ == 0) revert ZeroCycle();
        timelock = timelock_;
        cycleLength = cycleLength_;
        genesis = block.timestamp;

        uint256 s = WAD; // one unit S
        _add(DEMURRAGE_RATE, 0.0005e18, 0.0001e18, 0.002e18, 0, 2_500);
        _add(DEMURRAGE_THRESHOLD, 5e6 * s, 1e5 * s, 1e8 * s, 0, 2_500);
        _add(REENTRY_THRESHOLD, 5e6 * s, 1e5 * s, 1e8 * s, 0, 2_500);
        _add(HOARD_MULTIPLIER, 9, 0, 20, 2, 0);
        _add(GRACE_PERIOD, 7 days, 1 days, 365 days, 0, 5_000);
        _add(HOARD_HORIZON, 90 days, 1 days, 365 days, 0, 5_000);
        _add(RELEASE_WINDOW, 30 days, 1 days, 365 days, 0, 5_000);
        _add(MINT_REWARD, 0.002e18, 0, 0.01e18, 0.001e18, 0);
        _add(COST_CAP, 0.02e18, 0, 0.05e18, 0.005e18, 0);
        _add(POOL_SHARE, 0.2e18, 0, 0.5e18, 0.05e18, 0);
        _add(EMISSION_RATE, 0.05e18, 0.01e18, 0.25e18, 0.02e18, 0);
        _add(VOTE_HALF_LIFE, 7 days, 3 days, 30 days, 0, 5_000);
    }

    function _add(bytes32 key, uint256 value, uint256 min, uint256 max, uint256 stepAbs, uint256 stepBps) private {
        _params[key] = Param(value, min, max, stepAbs, stepBps, type(uint64).max, true);
        _keys.push(key);
    }

    function currentCycle() public view returns (uint256) {
        return (block.timestamp - genesis) / cycleLength;
    }

    function get(bytes32 key) external view returns (uint256) {
        Param storage p = _params[key];
        if (!p.exists) revert UnknownParameter(key);
        return p.value;
    }

    function getParam(bytes32 key) external view returns (Param memory) {
        if (!_params[key].exists) revert UnknownParameter(key);
        return _params[key];
    }

    function keys() external view returns (bytes32[] memory) {
        return _keys;
    }

    /// @notice Change one parameter. Callable only by the governance timelock (after its delay).
    function setParameter(bytes32 key, uint256 newValue) external {
        if (msg.sender != timelock) revert NotTimelock();
        Param storage p = _params[key];
        if (!p.exists) revert UnknownParameter(key);
        if (newValue < p.min || newValue > p.max) revert OutOfBounds(key, newValue);
        uint256 cycle = currentCycle();
        if (p.lastChangeCycle == cycle) revert AlreadyChangedThisCycle(key);
        uint256 old = p.value;
        uint256 diff = newValue > old ? newValue - old : old - newValue;
        uint256 maxStep = p.maxStepAbs > 0 ? p.maxStepAbs : (old * p.maxStepBps) / BPS;
        if (diff > maxStep) revert StepTooLarge(key, old, newValue);
        p.value = newValue;
        p.lastChangeCycle = uint64(cycle);
        emit ParameterChanged(key, old, newValue, cycle);
    }
}
