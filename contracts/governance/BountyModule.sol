// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

/// @notice Verifies one class of on-chain evidence (spec §10.10). MUST be deterministic and return
///         false on anything malformed.
interface IEvidenceVerifier {
    function verify(bytes32 offenderId, bytes calldata evidence) external view returns (bool);
}

/// @title BountyModule (skeleton)
/// @notice Bonded fraud reporting (spec §10.10). Offenders (vouchers, provers, minters) bond ETH under an
///         id; a reporter posts bond b_r with evidence; a valid report slashes b_o and pays
///         R = min(sigma*b_o + tau, b_o) with tau <= tauMax, the remainder going to the vault.
///         An invalid report forfeits b_r to the vault. Since R <= b_o, self-reporting nets <= 0.
/// @dev Evidence types without a registered verifier are rejected (fail-closed). Verifiers and
///      sigma/tau are set only by the timelock. Payouts use pull-payments.
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

    uint256 public sigmaWad; // in (0, 1)
    uint256 public tau; // fixed top-up component, <= tauMax, still capped by b_o

    mapping(Offence => IEvidenceVerifier) public verifiers;
    mapping(bytes32 => uint256) public bonds; // b_o per offender id
    mapping(bytes32 => address) public bondOwner;
    mapping(bytes32 => bool) public evidenceUsed;
    mapping(address => uint256) public withdrawable;

    event Bonded(bytes32 indexed offenderId, address indexed owner, uint256 amount);
    event Slashed(
        bytes32 indexed offenderId, Offence offence, address indexed reporter, uint256 reward, uint256 toVault
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

    constructor(address timelock_, address vault_, uint256 reporterBond_, uint256 tauMax_, uint256 sigmaWad_) {
        if (timelock_ == address(0) || vault_ == address(0)) revert ZeroAddress();
        if (reporterBond_ == 0 || sigmaWad_ == 0 || sigmaWad_ >= WAD) revert BadParams();
        timelock = timelock_;
        vault = vault_;
        reporterBond = reporterBond_;
        tauMax = tauMax_;
        sigmaWad = sigmaWad_;
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

    /// @notice Bond under an offender id (e.g. keccak of a vouch, prover or minter key).
    function bond(bytes32 offenderId) external payable {
        if (msg.value == 0) revert BadParams();
        address o = bondOwner[offenderId];
        if (o == address(0)) bondOwner[offenderId] = msg.sender;
        else if (o != msg.sender) revert NotBondOwner();
        bonds[offenderId] += msg.value;
        emit Bonded(offenderId, msg.sender, msg.value);
    }

    /// @notice R = min(sigma*b_o + tau, b_o).
    function rewardFor(uint256 bo) public view returns (uint256) {
        uint256 r = (sigmaWad * bo) / WAD + tau;
        return r > bo ? bo : r;
    }

    /// @notice Report an offence. Send exactly `reporterBond`. Returns true if the report was valid.
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
        uint256 r = rewardFor(bo);
        bonds[offenderId] = 0;
        withdrawable[msg.sender] += r + msg.value;
        withdrawable[vault] += bo - r;
        emit Slashed(offenderId, offence, msg.sender, r, bo - r);
        return true;
    }

    function withdraw() external {
        uint256 a = withdrawable[msg.sender];
        withdrawable[msg.sender] = 0;
        (bool ok,) = payable(msg.sender).call{value: a}("");
        if (!ok) revert TransferFailed();
    }
}
