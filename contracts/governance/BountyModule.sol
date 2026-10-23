// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

/// @notice Verifies one class of on-chain evidence (spec §10.10). MUST be deterministic and return
///         false on anything malformed.
interface IEvidenceVerifier {
    function verify(bytes32 offenderId, bytes calldata evidence) external view returns (bool);
}

/// @title BountyModule
/// @notice Bonded fraud reporting (spec §10.10).
///         - Offenders (vouchers, provers, minters) bond ETH under an id.
///         - Unbonding: `requestUnbond` starts a challenge window; reports are still accepted during it;
///           `withdrawBond` only after it ends. New bonds cannot be added while unbonding.
///         - A reporter sends exactly b_r with evidence. A valid report pays
///           R = min(sigma*b_o + tau, b_o): sigma*b_o from the slashed bond, the rest (<= tau <= tauMax)
///           from a separately funded top-up pool (capped by its balance). b_o - sigma*b_o goes to the vault.
///         - An invalid report forfeits b_r to the vault. Since R <= b_o, self-reporting nets <= 0.
/// @dev Evidence types without a registered verifier are rejected (fail-closed). Payouts are pull-based.
///      TODO(BribeOffer): verifier for a briber-signed (proposalId, vote) offer bound to a bribe key.
///      TODO(FakeIdentityVouch): needs the personhood dispute/ceremony-failure result as an on-chain fact.
contract BountyModule {
    uint256 public constant WAD = 1e18;

    enum Offence {
        BribeOffer,
        DoubleDeposit,
        FakeIdentityVouch,
        InvalidProof
    }

    address public immutable timelock;
    address public immutable vault;
    uint256 public immutable reporterBond; // b_r
    uint256 public immutable tauMax;
    uint256 public immutable challengeWindow;

    uint256 public sigmaWad; // in (0, 1)
    uint256 public tau; // pool top-up per report, <= tauMax
    uint256 public pool; // top-up pool balance

    mapping(Offence => IEvidenceVerifier) public verifiers;
    mapping(bytes32 => uint256) public bonds;
    mapping(bytes32 => address) public bondOwner;
    mapping(bytes32 => uint256) public unbondAt; // 0 = not unbonding
    mapping(bytes32 => bool) public evidenceUsed;
    mapping(address => uint256) public withdrawable;

    event Bonded(bytes32 indexed offenderId, address indexed owner, uint256 amount);
    event UnbondRequested(bytes32 indexed offenderId, uint256 unlockAt);
    event Unbonded(bytes32 indexed offenderId, uint256 amount);
    event PoolFunded(address indexed from, uint256 amount);
    event Slashed(
        bytes32 indexed offenderId,
        Offence offence,
        address indexed reporter,
        uint256 reward,
        uint256 fromPool,
        uint256 toVault
    );
    event FalseReport(bytes32 indexed offenderId, Offence offence, address indexed reporter);
    event VerifierSet(Offence offence, address verifier);

    error NotTimelock();
    error ZeroAddress();
    error BadParams();
    error WrongReporterBond();
    error NoVerifier();
    error NothingBonded();
    error NotBondOwner();
    error EvidenceReused();
    error TransferFailed();
    error Unbonding();
    error NotUnbonding();
    error ChallengeWindowOpen(uint256 unlockAt);

    constructor(
        address timelock_,
        address vault_,
        uint256 reporterBond_,
        uint256 tauMax_,
        uint256 sigmaWad_,
        uint256 challengeWindow_
    ) {
        if (timelock_ == address(0) || vault_ == address(0)) revert ZeroAddress();
        if (reporterBond_ == 0 || sigmaWad_ == 0 || sigmaWad_ >= WAD || challengeWindow_ == 0) revert BadParams();
        timelock = timelock_;
        vault = vault_;
        reporterBond = reporterBond_;
        tauMax = tauMax_;
        sigmaWad = sigmaWad_;
        challengeWindow = challengeWindow_;
    }

    modifier onlyTimelock() {
        if (msg.sender != timelock) revert NotTimelock();
        _;
    }

    function setVerifier(Offence offence, IEvidenceVerifier v) external onlyTimelock {
        verifiers[offence] = v;
        emit VerifierSet(offence, address(v));
    }

    function setRewardParams(uint256 sigmaWad_, uint256 tau_) external onlyTimelock {
        if (sigmaWad_ == 0 || sigmaWad_ >= WAD || tau_ > tauMax) revert BadParams();
        sigmaWad = sigmaWad_;
        tau = tau_;
    }

    /// @notice Fund the top-up pool (e.g. the vault's bounty share).
    function fundPool() external payable {
        pool += msg.value;
        emit PoolFunded(msg.sender, msg.value);
    }

    function bond(bytes32 offenderId) external payable {
        if (msg.value == 0) revert BadParams();
        if (unbondAt[offenderId] != 0) revert Unbonding();
        address o = bondOwner[offenderId];
        if (o == address(0)) bondOwner[offenderId] = msg.sender;
        else if (o != msg.sender) revert NotBondOwner();
        bonds[offenderId] += msg.value;
        emit Bonded(offenderId, msg.sender, msg.value);
    }

    function requestUnbond(bytes32 offenderId) external {
        if (bondOwner[offenderId] != msg.sender) revert NotBondOwner();
        if (bonds[offenderId] == 0) revert NothingBonded();
        if (unbondAt[offenderId] != 0) revert Unbonding();
        uint256 t = block.timestamp + challengeWindow;
        unbondAt[offenderId] = t;
        emit UnbondRequested(offenderId, t);
    }

    function withdrawBond(bytes32 offenderId) external {
        if (bondOwner[offenderId] != msg.sender) revert NotBondOwner();
        uint256 t = unbondAt[offenderId];
        if (t == 0) revert NotUnbonding();
        if (block.timestamp < t) revert ChallengeWindowOpen(t);
        uint256 a = bonds[offenderId];
        bonds[offenderId] = 0;
        unbondAt[offenderId] = 0;
        withdrawable[msg.sender] += a;
        emit Unbonded(offenderId, a);
    }

    /// @notice Split of R = min(sigma*b_o + tau, b_o): (fromBond, fromPool). fromPool also capped by pool.
    function rewardSplit(uint256 bo) public view returns (uint256 fromBond, uint256 fromPool) {
        fromBond = (sigmaWad * bo) / WAD;
        uint256 room = bo - fromBond; // keeps R <= b_o
        fromPool = tau < room ? tau : room;
        if (fromPool > pool) fromPool = pool;
    }

    function rewardFor(uint256 bo) external view returns (uint256) {
        (uint256 a, uint256 b) = rewardSplit(bo);
        return a + b;
    }

    function report(Offence offence, bytes32 offenderId, bytes calldata evidence) external payable returns (bool) {
        if (msg.value != reporterBond) revert WrongReporterBond();
        IEvidenceVerifier v = verifiers[offence];
        if (address(v) == address(0)) revert NoVerifier();
        uint256 bo = bonds[offenderId];
        if (bo == 0) revert NothingBonded();
        bytes32 eid = keccak256(abi.encode(offence, offenderId, evidence));
        if (evidenceUsed[eid]) revert EvidenceReused();
        evidenceUsed[eid] = true;

        if (!v.verify(offenderId, evidence)) {
            withdrawable[vault] += msg.value;
            emit FalseReport(offenderId, offence, msg.sender);
            return false;
        }
        (uint256 fromBond, uint256 fromPool) = rewardSplit(bo);
        bonds[offenderId] = 0;
        unbondAt[offenderId] = 0;
        pool -= fromPool;
        withdrawable[msg.sender] += fromBond + fromPool + msg.value;
        withdrawable[vault] += bo - fromBond;
        emit Slashed(offenderId, offence, msg.sender, fromBond + fromPool, fromPool, bo - fromBond);
        return true;
    }

    function withdraw() external {
        uint256 a = withdrawable[msg.sender];
        withdrawable[msg.sender] = 0;
        (bool ok,) = payable(msg.sender).call{value: a}("");
        if (!ok) revert TransferFailed();
    }
}
