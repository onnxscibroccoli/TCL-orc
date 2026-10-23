// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {IPersonhood} from "./IPersonhood.sol";
import {IPluralWeight} from "./IPluralWeight.sol";

interface ITimelockScheduler {
    function scheduleBatch(
        address[] calldata targets,
        uint256[] calldata values,
        bytes[] calldata payloads,
        bytes32 predecessor,
        bytes32 salt,
        uint256 delay
    ) external;
    function requiredDelay(address target, bytes calldata data) external view returns (uint256);
    function paramDelay() external view returns (uint256);
}

interface IParamReader {
    function get(bytes32 key) external view returns (uint256);
    function VOTE_HALF_LIFE() external view returns (bytes32);
}

/// @title ConvictionVoting
/// @notice One-anonymous-person-one-vote conviction voting with for/against (spec §10.6, decision 5).
/// @dev Normalized update Y(t) = a*Y(t-1) + (1-a)*W*S(t) with a = 2^(-1/t_half) per second, t_half read
///      from ParameterRegistry (bounded there to [3d, 30d], default 7d). Over dt with constant S:
///      Y(t+dt) = 2^(-dt/t_half)*Y(t) + (1 - 2^(-dt/t_half))*S. Tracked lazily per side; the half-life
///      in force at each checkpoint applies to the whole elapsed interval.
///      A new vote contributes 0 in its own block, so flash votes cannot pass anything.
///      Pass rule: Y_for >= quorum*N and Y_for > m*(Y_for + Y_against) with m = 50%, or 66% for
///      constitutional proposals (any call needing more than the parameter delay, e.g. UBI gate, upgrades).
contract ConvictionVoting {
    uint256 public constant WAD = 1e18;
    uint256 public constant BPS = 10_000;
    uint256 public constant SIMPLE_MAJORITY_BPS = 5_000;
    uint256 public constant SUPERMAJORITY_BPS = 6_600;
    uint256 private constant LN2_WAD = 693147180559945309;

    IPersonhood public immutable personhood;
    ITimelockScheduler public immutable timelock;
    IParamReader public immutable registry;
    IPluralWeight public immutable pluralWeight; // address(0) => W = 1
    uint256 public immutable quorumWad;

    struct Proposal {
        bytes32 actionHash;
        uint256 supportFor; // sum of weights (WAD)
        uint256 supportAgainst;
        uint256 convFor; // WAD-scaled persons
        uint256 convAgainst;
        uint64 lastUpdate;
        bool constitutional;
        bool scheduled;
        bool exists;
    }

    struct Vote {
        uint8 state; // 0 none, 1 active, 2 withdrawn
        bool against;
        uint256 weight;
    }

    mapping(uint256 => Proposal) public proposals;
    mapping(uint256 => mapping(uint256 => Vote)) public votes;
    uint256 public proposalCount;

    event Proposed(uint256 indexed id, bytes32 actionHash, bool constitutional);
    event VoteCast(uint256 indexed id, uint256 nullifierHash, bool against, uint256 weight);
    event VoteWithdrawn(uint256 indexed id, uint256 nullifierHash);
    event Scheduled(uint256 indexed id, uint256 convFor, uint256 convAgainst, uint256 threshold);

    error ZeroAddress();
    error BadQuorum();
    error UnknownProposal();
    error NullifierUsed();
    error NotActiveVoter();
    error InvalidPersonhoodProof();
    error ThresholdNotMet(uint256 conviction, uint256 threshold);
    error MajorityNotMet(uint256 convFor, uint256 convAgainst, uint256 requiredBps);
    error AlreadyScheduled();
    error ActionMismatch();
    error NoElectorate();
    error EmptyProposal();

    constructor(
        IPersonhood personhood_,
        ITimelockScheduler timelock_,
        IParamReader registry_,
        IPluralWeight pluralWeight_,
        uint256 quorumWad_
    ) {
        if (address(personhood_) == address(0) || address(timelock_) == address(0) || address(registry_) == address(0))
        {
            revert ZeroAddress();
        }
        if (quorumWad_ == 0 || quorumWad_ > WAD) revert BadQuorum();
        personhood = personhood_;
        timelock = timelock_;
        registry = registry_;
        pluralWeight = pluralWeight_;
        quorumWad = quorumWad_;
    }

    // ------------------------------------------------------------------ math

    function halfLife() public view returns (uint256) {
        return registry.get(registry.VOTE_HALF_LIFE());
    }

    /// @notice 2^(-dt/h) in WAD.
    function decayFactor(uint256 dt, uint256 h) public pure returns (uint256) {
        uint256 k = dt / h;
        if (k >= 128) return 0;
        // slither-disable-next-line weak-prng (justified in docs/SECURITY.md)
        uint256 y = (LN2_WAD * (dt % h)) / h; // in [0, ln2)
        // exp(-y) by Taylor series; y < 0.7 so terms vanish quickly
        uint256 sum = WAD;
        uint256 term = WAD;
        bool neg = true;
        for (uint256 i = 1; i < 30 && term != 0; ++i) {
            // slither-disable-next-line divide-before-multiply (justified in docs/SECURITY.md)
            term = (term * y) / (i * WAD);
            if (neg) sum -= term;
            else sum += term;
            neg = !neg;
        }
        return sum >> k;
    }

    function _decay(uint256 y, uint256 s, uint256 a) private pure returns (uint256) {
        return (a * y + (WAD - a) * s) / WAD;
    }

    /// @return convFor, convAgainst at the current timestamp (WAD-scaled persons).
    function convictionOf(uint256 id) public view returns (uint256, uint256) {
        Proposal storage p = proposals[id];
        if (!p.exists) revert UnknownProposal();
        uint256 dt = block.timestamp - p.lastUpdate;
        // slither-disable-next-line incorrect-equality (justified in docs/SECURITY.md)
        if (dt == 0) return (p.convFor, p.convAgainst);
        uint256 a = decayFactor(dt, halfLife());
        return (_decay(p.convFor, p.supportFor, a), _decay(p.convAgainst, p.supportAgainst, a));
    }

    function _checkpoint(uint256 id, Proposal storage p) private {
        (p.convFor, p.convAgainst) = convictionOf(id);
        p.lastUpdate = uint64(block.timestamp);
    }

    function threshold() public view returns (uint256) {
        uint256 n = personhood.personCount();
        if (n == 0) revert NoElectorate();
        return n * quorumWad;
    }

    // ------------------------------------------------------------------ actions

    function hashAction(address[] calldata targets, uint256[] calldata values, bytes[] calldata payloads, bytes32 salt)
        public
        pure
        returns (bytes32)
    {
        return keccak256(abi.encode(targets, values, payloads, salt));
    }

    function propose(address[] calldata targets, uint256[] calldata values, bytes[] calldata payloads, bytes32 salt)
        external
        returns (uint256 id)
    {
        if (targets.length == 0) revert EmptyProposal();
        id = ++proposalCount;
        bytes32 h = hashAction(targets, values, payloads, salt);
        bool constitutional = _maxDelay(targets, payloads) > timelock.paramDelay();
        Proposal storage p = proposals[id];
        p.actionHash = h;
        p.lastUpdate = uint64(block.timestamp);
        p.constitutional = constitutional;
        p.exists = true;
        emit Proposed(id, h, constitutional);
    }

    /// @notice Cast one person's vote. `nullifierHash` = H(15, personSecret, id); usable once per proposal.
    function castVote(uint256 id, uint256 nullifierHash, bytes calldata proof, bool against) external {
        Proposal storage p = proposals[id];
        if (!p.exists) revert UnknownProposal();
        if (p.scheduled) revert AlreadyScheduled();
        Vote storage v = votes[id][nullifierHash];
        if (v.state != 0) revert NullifierUsed();
        if (!personhood.verifyPersonhood(nullifierHash, id, against ? 2 : 1, proof)) revert InvalidPersonhoodProof();
        uint256 w = WAD;
        if (address(pluralWeight) != address(0)) {
            w = pluralWeight.weightOf(id, nullifierHash);
            if (w > WAD) w = WAD;
        }
        _checkpoint(id, p);
        (v.state, v.against, v.weight) = (1, against, w);
        if (against) p.supportAgainst += w;
        else p.supportFor += w;
        emit VoteCast(id, nullifierHash, against, w);
    }

    /// @notice Withdraw a vote (proof re-binds the same nullifier with signal 0). Cannot re-vote.
    function withdrawVote(uint256 id, uint256 nullifierHash, bytes calldata proof) external {
        Proposal storage p = proposals[id];
        if (!p.exists) revert UnknownProposal();
        Vote storage v = votes[id][nullifierHash];
        if (v.state != 1) revert NotActiveVoter();
        if (!personhood.verifyPersonhood(nullifierHash, id, 0, proof)) revert InvalidPersonhoodProof();
        _checkpoint(id, p);
        v.state = 2;
        if (v.against) p.supportAgainst -= v.weight;
        else p.supportFor -= v.weight;
        emit VoteWithdrawn(id, nullifierHash);
    }

    function schedule(
        uint256 id,
        address[] calldata targets,
        uint256[] calldata values,
        bytes[] calldata payloads,
        bytes32 salt
    ) external {
        Proposal storage p = proposals[id];
        if (!p.exists) revert UnknownProposal();
        if (p.scheduled) revert AlreadyScheduled();
        if (hashAction(targets, values, payloads, salt) != p.actionHash) revert ActionMismatch();
        _requirePassed(id, p);
        p.scheduled = true;
        uint256 delay = _maxDelay(targets, payloads);
        bytes32 tlSalt = keccak256(abi.encode(id, salt));
        timelock.scheduleBatch(targets, values, payloads, bytes32(0), tlSalt, delay);
    }

    function _requirePassed(uint256 id, Proposal storage p) private {
        (uint256 yf, uint256 ya) = convictionOf(id);
        uint256 thr = threshold();
        if (yf < thr) revert ThresholdNotMet(yf, thr);
        uint256 m = p.constitutional ? SUPERMAJORITY_BPS : SIMPLE_MAJORITY_BPS;
        if (yf * BPS <= m * (yf + ya)) revert MajorityNotMet(yf, ya, m);
        emit Scheduled(id, yf, ya, thr);
    }

    function _maxDelay(address[] calldata targets, bytes[] calldata payloads) private view returns (uint256 delay) {
        for (uint256 i = 0; i < targets.length; ++i) {
            uint256 d = timelock.requiredDelay(targets[i], payloads[i]);
            if (d > delay) delay = d;
        }
    }
}
