// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

/// @title TieredPersonhoodLimits
/// @notice Tiered personhood hook (spec §10.9): tier n needs effective (plural-discounted) vouches
///         eff >= v_n and allows at most L_n per window per pseudonym. Requires L_n / v_n non-increasing.
/// @dev Fail-closed: an unknown pseudonym is tier 0 with limit 0. `attestor` is the personhood
///      layer (ceremony + graph oracle / ZK verifier) that reports effective vouches; `consumer` is the
///      settlement path (CirculatoryEngine / PhysicalMintController integration is future work).
///      Funneling aggregation (§10.9) is off-chain/circuit-side and reported via `chargeFunneled`.
contract TieredPersonhoodLimits {
    uint256 public constant WAD = 1e18;

    address public immutable timelock;
    address public immutable attestor;
    address public immutable consumer;
    uint256 public immutable window;

    uint256[] public vouchesRequired; // WAD-scaled effective vouches, strictly increasing
    uint256[] public limits; // L_n, strictly increasing

    struct Account {
        uint256 effVouches; // WAD
        uint256 windowId;
        uint256 used;
    }

    mapping(bytes32 => Account) public accounts;

    event VouchesAttested(bytes32 indexed pseudonym, uint256 effVouches, uint256 tier);
    event Consumed(bytes32 indexed pseudonym, uint256 amount, uint256 used, uint256 limit);

    error NotAttestor();
    error NotConsumer();
    error BadTiers();
    error LimitExceeded(bytes32 pseudonym, uint256 requested, uint256 remaining);
    error ZeroAddress();

    constructor(
        address timelock_,
        address attestor_,
        address consumer_,
        uint256 window_,
        uint256[] memory vouchesRequired_,
        uint256[] memory limits_
    ) {
        if (timelock_ == address(0) || attestor_ == address(0) || consumer_ == address(0)) {
            revert ZeroAddress();
        }
        uint256 n = vouchesRequired_.length;
        if (n == 0 || n != limits_.length || window_ == 0) revert BadTiers();
        for (uint256 i = 0; i < n; ++i) {
            if (vouchesRequired_[i] == 0 || limits_[i] == 0) revert BadTiers();
            if (i > 0) {
                if (vouchesRequired_[i] <= vouchesRequired_[i - 1] || limits_[i] <= limits_[i - 1]) revert BadTiers();
                // L_i / v_i <= L_{i-1} / v_{i-1}  (cross-multiplied)
                if (limits_[i] * vouchesRequired_[i - 1] > limits_[i - 1] * vouchesRequired_[i]) revert BadTiers();
            }
        }
        timelock = timelock_;
        attestor = attestor_;
        consumer = consumer_;
        window = window_;
        vouchesRequired = vouchesRequired_;
        limits = limits_;
    }

    function tierOf(bytes32 pseudonym) public view returns (uint256 tier) {
        uint256 e = accounts[pseudonym].effVouches;
        for (uint256 i = 0; i < vouchesRequired.length; ++i) {
            if (e >= vouchesRequired[i]) tier = i + 1;
            else break;
        }
    }

    function limitOf(bytes32 pseudonym) public view returns (uint256) {
        uint256 t = tierOf(pseudonym);
        return t == 0 ? 0 : limits[t - 1];
    }

    function remaining(bytes32 pseudonym) public view returns (uint256) {
        Account storage a = accounts[pseudonym];
        // slither-disable-next-line incorrect-equality (justified in docs/SECURITY.md)
        uint256 used = a.windowId == block.timestamp / window ? a.used : 0;
        uint256 lim = limitOf(pseudonym);
        return used >= lim ? 0 : lim - used;
    }

    function attestVouches(bytes32 pseudonym, uint256 effVouches) external {
        if (msg.sender != attestor) revert NotAttestor();
        accounts[pseudonym].effVouches = effVouches;
        emit VouchesAttested(pseudonym, effVouches, tierOf(pseudonym));
    }

    /// @notice Charge `amount` against the pseudonym's per-window limit; reverts if it would exceed L_n.
    function consume(bytes32 pseudonym, uint256 amount) external {
        if (msg.sender != consumer) revert NotConsumer();
        _charge(pseudonym, amount, true);
    }

    /// @notice Charge flagged funneled inflow to the recipient's limit (§10.9); saturates instead of reverting.
    function chargeFunneled(bytes32 recipient, uint256 amount) external {
        if (msg.sender != attestor) revert NotAttestor();
        _charge(recipient, amount, false);
    }

    function _charge(bytes32 p, uint256 amount, bool strict) internal {
        Account storage a = accounts[p];
        uint256 w = block.timestamp / window;
        if (a.windowId != w) {
            a.windowId = w;
            a.used = 0;
        }
        uint256 lim = limitOf(p);
        uint256 rem = a.used >= lim ? 0 : lim - a.used;
        if (strict && amount > rem) revert LimitExceeded(p, amount, rem);
        a.used += amount;
        emit Consumed(p, amount, a.used, lim);
    }
}
