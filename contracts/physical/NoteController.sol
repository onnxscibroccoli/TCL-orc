// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {AccessControlUpgradeable} from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import {PausableUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";
import {ReentrancyGuardUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";

interface INoteDepositVerifier {
    function verifyProof(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256[6] calldata pub
    ) external view returns (bool);
}

/// @title NoteController (MVP of docs/11 §4, §6.1-6.2, §7)
/// @notice Reusable bearer notes with a permanent id and per-deposit era k.
///  - mint/re-arm: burn V_d (escrowed as physical backing), credit C_d + R_d from the
///    incentive pool within an epoch budget; over budget => pure 1:1 (C = R = 0).
///  - deposit: Groth16 proof of note-secret knowledge with nf = H(12, id, k, ctx),
///    ctx = chainid << 160 | address(this); note goes DORMANT; re-arm moves to k+1.
///  - re-entry anti-hoarding: excess over Theta in a rolling window unlocks linearly,
///    U = Theta + E * min(1, dt / W_r). Accelerated idle decay is ledger-side (Python).
/// SIMULATION: no real secure element / PUF attestation is checked on-chain.
// solhint-disable-next-line max-states-count
contract NoteController is
    Initializable,
    UUPSUpgradeable,
    AccessControlUpgradeable,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
{
    using SafeERC20 for IERC20;

    bytes32 public constant PARAMS_ROLE = keccak256("PARAMS_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
    bytes32 public constant UPGRADER_ROLE = keccak256("UPGRADER_ROLE");

    uint256 internal constant FIELD_P = 21888242871839275222246405745257275088548364400416034343698204186575808495617;
    uint32 public constant MAX_DENOM = 500;
    uint16 public constant MAX_REWARD_BPS = 100; // rho_max = 1%
    uint16 public constant MAX_COST_BPS = 2000; // C_d <= 20% of V_d (hard bound)
    uint64 public constant MIN_WINDOW = 1 days;
    uint64 public constant MAX_WINDOW = 365 days;

    enum State {
        NONE,
        LIVE,
        DORMANT
    }

    struct Note {
        uint32 denom;
        uint64 era;
        uint64 mintTime;
        State state;
    }

    struct Bucket {
        uint64 windowStart;
        uint64 t0;
        uint128 windowAmount; // A
        uint128 excess; // E (locked at t0)
        uint128 released; // part of E already paid
    }

    IERC20 public token;
    INoteDepositVerifier public verifier;
    uint256 public unit; // token units per face value 1

    mapping(uint32 => bool) public denomAllowed;
    mapping(uint32 => uint256) public costCap; // c^max_d in token units
    mapping(uint256 => Note) public notes;
    mapping(uint256 => bool) public nullifierUsed;
    mapping(address => Bucket) public buckets;

    uint256 public physicalOutstanding; // P = sum of V over LIVE notes
    uint256 public pool; // incentive pool I
    uint256 public lockedTotal; // sum over buckets of (excess - released)

    uint16 public rewardBps; // rho
    uint64 public epochLength;
    uint256 public epochBudget; // max sum(C + R) per epoch
    uint256 public currentEpoch;
    uint256 public spentInEpoch;

    uint256 public theta; // Theta, token units
    uint64 public aggWindow; // W_a
    uint64 public releaseWindow; // W_r

    event DenominationSet(uint32 denom, bool allowed, uint256 costCap);
    event NoteMinted(uint256 indexed id, uint64 era, uint32 denom, address minter, uint256 cost, uint256 reward);
    event NoteDeposited(
        uint256 indexed id, uint64 era, uint256 nullifier, address recipient, uint256 immediate, uint256 locked
    );
    event PoolFunded(address from, uint256 amount);
    event Released(address indexed account, uint256 amount);
    event ParamsSet(
        uint16 rewardBps, uint64 epochLength, uint256 epochBudget, uint256 theta, uint64 aggWindow, uint64 releaseWindow
    );

    error BadDenomination();
    error BadState();
    error BadId();
    error NullifierUsed();
    error BadPublicInputs();
    error InvalidProof();
    error OutOfBounds();
    error ZeroAddress();

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(address admin, IERC20 token_, INoteDepositVerifier verifier_, uint256 unit_)
        external
        initializer
    {
        if (admin == address(0) || address(token_) == address(0) || address(verifier_) == address(0)) {
            revert ZeroAddress();
        }
        if (unit_ == 0) revert OutOfBounds();
        __AccessControl_init();
        __Pausable_init();
        __ReentrancyGuard_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PARAMS_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
        _grantRole(UPGRADER_ROLE, admin);
        token = token_;
        verifier = verifier_;
        unit = unit_;
        uint32[7] memory ds = [uint32(5), 10, 20, 50, 100, 200, 500];
        for (uint256 i = 0; i < ds.length; i++) {
            _setDenomination(ds[i], true, (uint256(ds[i]) * unit_ * 5) / 1000); // 0.5% default cap
        }
        _setParams(100, 1 days, 1_000 * unit_, 5_000_000 * unit_, 30 days, 30 days);
    }

    // ------------------------------------------------------------ governance (bounded)

    function setDenomination(uint32 d, bool allowed, uint256 cap) external onlyRole(PARAMS_ROLE) {
        _setDenomination(d, allowed, cap);
    }

    function setParams(
        uint16 rewardBps_,
        uint64 epochLength_,
        uint256 epochBudget_,
        uint256 theta_,
        uint64 aggWindow_,
        uint64 releaseWindow_
    ) external onlyRole(PARAMS_ROLE) {
        _setParams(rewardBps_, epochLength_, epochBudget_, theta_, aggWindow_, releaseWindow_);
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    function _authorizeUpgrade(address) internal override onlyRole(UPGRADER_ROLE) {}

    function _setDenomination(uint32 d, bool allowed, uint256 cap) internal {
        if (d == 0 || d > MAX_DENOM) revert BadDenomination();
        if (cap * 10_000 > uint256(d) * unit * MAX_COST_BPS) revert OutOfBounds();
        denomAllowed[d] = allowed;
        costCap[d] = cap;
        emit DenominationSet(d, allowed, cap);
    }

    function _setParams(uint16 r, uint64 el, uint256 eb, uint256 th, uint64 wa, uint64 wr) internal {
        if (r > MAX_REWARD_BPS) revert OutOfBounds();
        if (el < 1 hours || el > MAX_WINDOW) revert OutOfBounds();
        if (wa < MIN_WINDOW || wa > MAX_WINDOW || wr < MIN_WINDOW || wr > MAX_WINDOW) revert OutOfBounds();
        if (th == 0 || th > type(uint128).max) revert OutOfBounds();
        rewardBps = r;
        epochLength = el;
        epochBudget = eb;
        theta = th;
        aggWindow = wa;
        releaseWindow = wr;
        emit ParamsSet(r, el, eb, th, wa, wr);
    }

    // ------------------------------------------------------------ pool

    function fundPool(uint256 amount) external nonReentrant {
        token.safeTransferFrom(msg.sender, address(this), amount);
        pool += amount;
        emit PoolFunded(msg.sender, amount);
    }

    // ------------------------------------------------------------ mint / re-arm

    function context() public view returns (uint256) {
        return (block.chainid << 160) | uint256(uint160(address(this)));
    }

    function faceValue(uint32 d) public view returns (uint256) {
        return uint256(d) * unit;
    }

    /// @notice First mint of a new note id (era 0).
    function mint(uint256 id, uint32 denom, uint256 attestedCost)
        external
        nonReentrant
        whenNotPaused
        returns (uint256 cost, uint256 reward)
    {
        if (id == 0 || id >= FIELD_P) revert BadId();
        if (notes[id].state != State.NONE) revert BadState();
        if (!denomAllowed[denom]) revert BadDenomination();
        notes[id] = Note({denom: denom, era: 0, mintTime: uint64(block.timestamp), state: State.LIVE});
        return _burnAndCredit(id, 0, denom, attestedCost);
    }

    /// @notice Re-arm a DORMANT note: same id, era k+1, LIVE again.
    function rearm(uint256 id, uint256 attestedCost)
        external
        nonReentrant
        whenNotPaused
        returns (uint256 cost, uint256 reward)
    {
        Note storage n = notes[id];
        if (n.state != State.DORMANT) revert BadState();
        if (!denomAllowed[n.denom]) revert BadDenomination();
        n.era += 1;
        n.state = State.LIVE;
        n.mintTime = uint64(block.timestamp);
        return _burnAndCredit(id, n.era, n.denom, attestedCost);
    }

    function quote(uint32 denom, uint256 attestedCost) public view returns (uint256 cost, uint256 reward) {
        uint256 v = faceValue(denom);
        cost = attestedCost < costCap[denom] ? attestedCost : costCap[denom];
        reward = (v * rewardBps) / 10_000;
        // slither-disable-next-line incorrect-equality (justified in docs/SECURITY.md)
        uint256 spent = (block.timestamp / epochLength == currentEpoch) ? spentInEpoch : 0;
        uint256 total = cost + reward;
        if (total > pool || spent + total > epochBudget) return (0, 0); // pure 1:1 conversion
    }

    function _burnAndCredit(uint256 id, uint64 era, uint32 denom, uint256 attestedCost)
        internal
        returns (uint256 cost, uint256 reward)
    {
        uint256 e = block.timestamp / epochLength;
        if (e != currentEpoch) {
            currentEpoch = e;
            spentInEpoch = 0;
        }
        (cost, reward) = quote(denom, attestedCost);
        uint256 v = faceValue(denom);
        physicalOutstanding += v;
        uint256 credit = cost + reward;
        if (credit != 0) {
            pool -= credit;
            spentInEpoch += credit;
        }
        emit NoteMinted(id, era, denom, msg.sender, cost, reward);
        token.safeTransferFrom(msg.sender, address(this), v);
        if (credit != 0) token.safeTransfer(msg.sender, credit);
    }

    // ------------------------------------------------------------ deposit (re-entry)

    function deposit(
        uint256 id,
        uint256 nf,
        address recipient,
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c
    ) external nonReentrant whenNotPaused {
        if (recipient == address(0)) revert ZeroAddress();
        Note storage n = notes[id];
        if (n.state != State.LIVE) revert BadState();
        if (nf == 0 || nf >= FIELD_P) revert BadPublicInputs();
        if (nullifierUsed[nf]) revert NullifierUsed();
        uint256[6] memory pub = [id, uint256(n.denom), uint256(n.era), context(), uint256(uint160(recipient)), nf];
        if (!_verify(a, b, c, pub)) revert InvalidProof();

        nullifierUsed[nf] = true;
        n.state = State.DORMANT;
        uint256 v = faceValue(n.denom);
        physicalOutstanding -= v;

        (uint256 immediate, uint256 locked) = _reentry(recipient, v);
        emit NoteDeposited(id, n.era, nf, recipient, immediate, locked);
        if (immediate != 0) token.safeTransfer(recipient, immediate);
    }

    function _verify(uint256[2] calldata a, uint256[2][2] calldata b, uint256[2] calldata c, uint256[6] memory pub)
        internal
        view
        returns (bool)
    {
        // fail closed: any revert or malformed return from the verifier is a rejection
        try verifier.verifyProof(a, b, c, pub) returns (bool ok) {
            return ok;
        } catch {
            return false;
        }
    }

    // ------------------------------------------------------------ anti-hoarding (§6.1-6.2)

    function unlocked(address acct) public view returns (uint256) {
        Bucket storage bk = buckets[acct];
        if (bk.excess == 0) return 0;
        uint256 dt = block.timestamp - bk.t0;
        uint256 vested = dt >= releaseWindow ? bk.excess : (uint256(bk.excess) * dt) / releaseWindow;
        return vested - bk.released;
    }

    function locked(address acct) external view returns (uint256) {
        Bucket storage bk = buckets[acct];
        return uint256(bk.excess) - bk.released - unlocked(acct);
    }

    function release() external nonReentrant whenNotPaused returns (uint256 amt) {
        amt = _release(msg.sender);
        if (amt != 0) token.safeTransfer(msg.sender, amt);
    }

    function _release(address acct) internal returns (uint256 amt) {
        amt = unlocked(acct);
        // slither-disable-next-line incorrect-equality (justified in docs/SECURITY.md)
        if (amt == 0) return 0;
        Bucket storage bk = buckets[acct];
        bk.released += uint128(amt);
        lockedTotal -= amt;
        emit Released(acct, amt);
    }

    /// @dev Per-recipient aggregation is a stand-in for per-person pseudonym nu (§6.5),
    ///      which needs the personhood layer (not in this MVP).
    function _reentry(address acct, uint256 v) internal returns (uint256 immediate, uint256 lockedAmt) {
        Bucket storage bk = buckets[acct];
        if (block.timestamp >= uint256(bk.windowStart) + aggWindow) {
            bk.windowStart = uint64(block.timestamp);
            bk.windowAmount = 0;
        }
        uint256 prevA = bk.windowAmount;
        uint256 newA = prevA + v;
        bk.windowAmount = uint128(newA);
        uint256 prevEx = prevA > theta ? prevA - theta : 0;
        uint256 newEx = newA > theta ? newA - theta : 0;
        lockedAmt = newEx - prevEx;
        immediate = v - lockedAmt;
        if (lockedAmt != 0) {
            immediate += _release(acct); // pay out what had vested, then restart the ramp
            uint256 remaining = uint256(bk.excess) - bk.released;
            bk.excess = uint128(remaining + lockedAmt);
            bk.released = 0;
            bk.t0 = uint64(block.timestamp);
            lockedTotal += lockedAmt;
        }
    }

    uint256[30] private __gap;
}
