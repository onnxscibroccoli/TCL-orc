// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/// @notice Fixed-supply research ledger; governance is a trusted redistribution authority.
contract CirculatoryEngine {
    string public constant name = "Thresholded Circulatory Ledger";
    string public constant symbol = "AETH";
    uint8 public constant decimals = 18;
    uint256 public constant T = 5_000_000 ether;
    uint256 public constant DELTA = 5e14; // 0.0005 per day, scaled by 1e18.
    uint256 private constant SCALE = 1e27;
    // floor(exp(-0.0005 / 86400) * SCALE), independently reproducible with Decimal.
    uint256 private constant SECOND_FACTOR = 999999994212962979707861764;
    uint256 public immutable totalSupply;
    address public governor;
    address public pendingGovernor;
    address public physicalMint;
    uint256 public vaultBalance;
    mapping(address => uint256) public balances;
    mapping(address => uint256) public lastSettled;
    mapping(address => uint256) private feeRemainder;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Approval(address indexed owner, address indexed spender, uint256 amount);
    event DemurrageApplied(address indexed account, uint256 amount);
    event Redistributed(address indexed recipient, uint256 amount);
    event PhysicalMintConfigured(address indexed controller);
    event GovernorProposed(address indexed candidate);
    event GovernorChanged(address indexed governor);

    modifier onlyGovernor() { require(msg.sender == governor, "governor only"); _; }

    constructor(address governor_, address holder, uint256 supply) {
        require(governor_ != address(0) && holder != address(0), "zero address");
        // Bound products used by the demurrage calculation.
        require(supply > 0 && supply <= type(uint128).max, "supply range");
        governor = governor_;
        totalSupply = supply;
        balances[holder] = supply;
        lastSettled[holder] = block.timestamp;
        emit Transfer(address(0), holder, supply);
    }

    function proposeGovernor(address candidate) external onlyGovernor {
        require(candidate != address(0), "zero address");
        pendingGovernor = candidate;
        emit GovernorProposed(candidate);
    }

    function acceptGovernance() external {
        require(msg.sender == pendingGovernor, "candidate only");
        governor = msg.sender;
        pendingGovernor = address(0);
        emit GovernorChanged(msg.sender);
    }

    function setPhysicalMint(address controller) external onlyGovernor {
        require(physicalMint == address(0), "already configured");
        require(controller.code.length > 0, "controller code required");
        _settle(controller);
        physicalMint = controller;
        feeRemainder[controller] = 0;
        emit PhysicalMintConfigured(controller);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        require(spender != address(0), "zero spender");
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 permitted = allowance[from][msg.sender];
        require(permitted >= amount, "allowance");
        if (permitted != type(uint256).max) {
            allowance[from][msg.sender] = permitted - amount;
            emit Approval(from, msg.sender, permitted - amount);
        }
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) private {
        require(to != address(0), "zero recipient");
        _settle(from);
        if (to != from) _settle(to);
        require(balances[from] >= amount, "balance");
        balances[from] -= amount;
        balances[to] += amount;
        emit Transfer(from, to, amount);
    }

    /// @notice Caller cannot supply elapsed time; repeated calls cannot charge a past interval twice.
    function applyDemurrage(address account) external { _settle(account); }

    function _settle(address account) private {
        uint256 previous = lastSettled[account];
        lastSettled[account] = block.timestamp;
        uint256 balance = balances[account];
        if (previous == 0 || balance <= T || account == physicalMint) {
            feeRemainder[account] = 0;
            return;
        }
        uint256 elapsed = block.timestamp - previous;
        uint256 surplus = balance - T;
        uint256 factor = _decayFactor(elapsed);
        uint256 numerator = surplus * (SCALE - factor) + feeRemainder[account];
        uint256 fee = numerator / SCALE;
        feeRemainder[account] = numerator % SCALE;
        if (fee >= surplus) { fee = surplus; feeRemainder[account] = 0; }
        balances[account] -= fee;
        vaultBalance += fee;
        if (fee > 0) emit DemurrageApplied(account, fee);
    }

    function _decayFactor(uint256 elapsed) private pure returns (uint256 result) {
        result = SCALE;
        uint256 factor = SECOND_FACTOR;
        while (elapsed > 0) {
            if (elapsed & 1 == 1) result = result * factor / SCALE;
            elapsed >>= 1;
            if (elapsed > 0) factor = factor * factor / SCALE;
        }
    }

    function redistribute(address recipient, uint256 amount) external onlyGovernor {
        require(recipient != address(0) && recipient != physicalMint, "invalid recipient");
        _settle(recipient);
        require(amount <= vaultBalance, "insufficient vault");
        vaultBalance -= amount;
        balances[recipient] += amount;
        emit Redistributed(recipient, amount);
    }
}
