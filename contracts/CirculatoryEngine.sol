// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import {PausableUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";
import {ReentrancyGuardUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";

/// @notice Groth16 verifier exported by snarkjs for circuits/puf_verify.circom
/// (public signals: newRoot, fee, oldRoot, newTime).
interface IStateVerifier {
    function verifyProof(
        uint256[2] calldata pA,
        uint256[2][2] calldata pB,
        uint256[2] calldata pC,
        uint256[4] calldata pubSignals
    ) external view returns (bool);
}

/// @title CirculatoryEngine
/// @notice Thresholded demurrage vault with lazy O(1) decay and ZK-verified
///         off-chain state settlement. Deployed behind an ERC1967/UUPS proxy.
/// @dev On-chain decay is linear per second on the surplus above `threshold`,
///      applied lazily on touch: fee = min(surplus, surplus * rate * dt / 1e18).
///      Every state-changing function follows checks-effects-interactions.
///      Conservation invariant: address(this).balance == totalBalances + vault.
contract CirculatoryEngine is
    Initializable,
    UUPSUpgradeable,
    OwnableUpgradeable,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
{
    uint256 public constant RATE_SCALE = 1e18;
    /// @dev BN254 scalar field; public signals must be canonical field elements.
    uint256 public constant SNARK_FIELD =
        21888242871839275222246405745257275088548364400416034343698204186575808495617;

    struct Account {
        uint128 balance;
        uint64 lastUpdate;
    }

    IStateVerifier public verifier;
    address public settler;
    uint256 public threshold;
    uint256 public ratePerSecond; // fraction of surplus per second, scaled by 1e18

    mapping(address account => Account) private _accounts;
    uint256 public totalBalances; // sum of stored (settled) balances
    uint256 public vault; // fees collected on-chain, available for redistribution

    uint256 public stateRoot; // latest ZK-verified off-chain Merkle root
    uint64 public lastSettledTime;
    uint64 public epoch;
    uint256 public offchainFeesSettled;

    event Deposited(address indexed account, uint256 amount);
    event Withdrawn(address indexed account, uint256 amount);
    event DemurrageApplied(address indexed account, uint256 fee);
    event Redistributed(address indexed recipient, uint256 amount);
    event SettlerUpdated(address indexed settler);
    event StateSettled(
        uint64 indexed epoch, uint256 oldRoot, uint256 newRoot, uint256 fee, uint64 newTime
    );

    error ZeroAmount();
    error ZeroAddress();
    error InsufficientBalance(uint256 available, uint256 requested);
    error InsufficientVault(uint256 available, uint256 requested);
    error TransferFailed();
    error NotSettler();
    error StaleRoot(uint256 expected, uint256 provided);
    error InvalidEpochTime(uint64 last, uint256 provided);
    error SignalOutOfField(uint256 index);
    error InvalidProof();
    error RateTooHigh();
    error AmountTooLarge();

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(
        address owner_,
        IStateVerifier verifier_,
        uint256 threshold_,
        uint256 ratePerSecond_,
        uint256 genesisRoot,
        uint64 genesisTime
    ) external initializer {
        if (owner_ == address(0) || address(verifier_) == address(0)) revert ZeroAddress();
        if (ratePerSecond_ > RATE_SCALE) revert RateTooHigh();
        if (genesisRoot >= SNARK_FIELD) revert SignalOutOfField(2);
        __Ownable_init(owner_);
        __Pausable_init();
        __ReentrancyGuard_init();
        verifier = verifier_;
        settler = owner_;
        threshold = threshold_;
        ratePerSecond = ratePerSecond_;
        stateRoot = genesisRoot;
        lastSettledTime = genesisTime;
    }

    modifier onlySettler() {
        if (msg.sender != settler && msg.sender != owner()) revert NotSettler();
        _;
    }

    // ------------------------------------------------------------------ views

    /// @notice Pending (unsettled) demurrage fee for `account`. O(1).
    function pendingFee(address account) public view returns (uint256) {
        Account memory a = _accounts[account];
        return _fee(a.balance, block.timestamp - a.lastUpdate);
    }

    /// @notice Balance after lazily applying accrued demurrage. O(1).
    function balanceOf(address account) external view returns (uint256) {
        return _accounts[account].balance - pendingFee(account);
    }

    function storedBalance(address account) external view returns (uint256 balance, uint64 lastUpdate) {
        Account memory a = _accounts[account];
        return (a.balance, a.lastUpdate);
    }

    function _fee(uint256 balance, uint256 dt) internal view returns (uint256) {
        // slither-disable-next-line incorrect-equality,timestamp
        if (balance <= threshold || dt == 0) return 0;
        uint256 surplus = balance - threshold;
        uint256 f = (surplus * ratePerSecond * dt) / RATE_SCALE;
        return f > surplus ? surplus : f;
    }

    // ------------------------------------------------------- account actions

    /// @notice Apply accrued demurrage to `account`; fee moves to the vault.
    function settle(address account) external whenNotPaused returns (uint256) {
        return _settle(account);
    }

    function _settle(address account) internal returns (uint256 newBalance) {
        Account storage a = _accounts[account];
        uint256 fee = _fee(a.balance, block.timestamp - a.lastUpdate);
        newBalance = a.balance - fee;
        a.balance = uint128(newBalance);
        a.lastUpdate = uint64(block.timestamp);
        if (fee != 0) {
            totalBalances -= fee;
            vault += fee;
            emit DemurrageApplied(account, fee);
        }
    }

    function deposit() external payable nonReentrant whenNotPaused {
        if (msg.value == 0) revert ZeroAmount();
        uint256 bal = _settle(msg.sender);
        if (bal + msg.value > type(uint128).max) revert AmountTooLarge();
        _accounts[msg.sender].balance = uint128(bal + msg.value);
        totalBalances += msg.value;
        emit Deposited(msg.sender, msg.value);
    }

    function withdraw(uint256 amount) external nonReentrant whenNotPaused {
        // checks
        if (amount == 0) revert ZeroAmount();
        uint256 bal = _settle(msg.sender);
        if (amount > bal) revert InsufficientBalance(bal, amount);
        // effects
        _accounts[msg.sender].balance = uint128(bal - amount);
        totalBalances -= amount;
        emit Withdrawn(msg.sender, amount);
        // interactions
        (bool ok,) = msg.sender.call{value: amount}("");
        if (!ok) revert TransferFailed();
    }

    // ---------------------------------------------------------- owner actions

    /// @notice Move `amount` from the vault to `recipient`'s balance (no ETH leaves).
    function redistribute(address recipient, uint256 amount) external nonReentrant onlyOwner {
        if (recipient == address(0)) revert ZeroAddress();
        if (amount == 0) revert ZeroAmount();
        if (amount > vault) revert InsufficientVault(vault, amount);
        uint256 bal = _settle(recipient);
        if (bal + amount > type(uint128).max) revert AmountTooLarge();
        vault -= amount;
        _accounts[recipient].balance = uint128(bal + amount);
        totalBalances += amount;
        emit Redistributed(recipient, amount);
    }

    function setSettler(address settler_) external onlyOwner {
        if (settler_ == address(0)) revert ZeroAddress();
        settler = settler_;
        emit SettlerUpdated(settler_);
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    // ----------------------------------------------------- epoch settlement

    /// @notice Settle one off-chain demurrage epoch proven by a Groth16 proof.
    /// @param pubSignals [newRoot, fee, oldRoot, newTime] (snarkjs order).
    function settleEpoch(
        uint256[2] calldata pA,
        uint256[2][2] calldata pB,
        uint256[2] calldata pC,
        uint256[4] calldata pubSignals
    ) external nonReentrant onlySettler whenNotPaused {
        // checks
        for (uint256 i = 0; i < 4; ++i) {
            if (pubSignals[i] >= SNARK_FIELD) revert SignalOutOfField(i);
        }
        uint256 oldRoot = stateRoot;
        if (pubSignals[2] != oldRoot) revert StaleRoot(oldRoot, pubSignals[2]);
        if (pubSignals[3] <= lastSettledTime || pubSignals[3] > block.timestamp) {
            revert InvalidEpochTime(lastSettledTime, pubSignals[3]);
        }
        if (!verifier.verifyProof(pA, pB, pC, pubSignals)) revert InvalidProof();
        // effects (no external interactions follow)
        uint64 newTime = uint64(pubSignals[3]);
        stateRoot = pubSignals[0];
        lastSettledTime = newTime;
        offchainFeesSettled += pubSignals[1];
        uint64 e = ++epoch;
        emit StateSettled(e, oldRoot, pubSignals[0], pubSignals[1], newTime);
    }

    function _authorizeUpgrade(address) internal override onlyOwner {}
}
