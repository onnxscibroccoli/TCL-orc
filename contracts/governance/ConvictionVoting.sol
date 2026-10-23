// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {IPersonhood} from "./IPersonhood.sol";

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
}

/// @title ConvictionVoting
/// @notice One-anonymous-person-one-vote conviction voting (spec §10.6, decision 5).
/// @dev Normalized update Y(t) = alpha*Y(t-1) + (1-alpha)*W*S(t), with per-second
///      alpha = 2^(-1/604800) (7-day half-life). Over dt seconds with constant support S this is
///      Y(t+dt) = alpha^dt * Y(t) + (1 - alpha^dt) * S. Because the update is linear and every
///      person stakes exactly one unit (S in {0,1}, W = 1 until the plural-discount W_i lands), the
///      aggregate conviction of a proposal is tracked lazily with the same formula.
///      A brand-new vote contributes 0 in its own block and ~1.15e-6 per second after, so a flash
///      vote cannot pass anything. Token balances confer no power.
///      Identity: each vote is bound to the Semaphore nullifier H(15, personSecret, proposalId);
///      a nullifier can be used to cast exactly once per proposal.
contract ConvictionVoting {
    uint256 public constant WAD = 1e18;
    /// @notice 2^(-1/604800) in WAD: 7-day half-life per second.
    uint256 public constant ALPHA_PER_SECOND = 999998853923969408;

    IPersonhood public immutable personhood;
    ITimelockScheduler public immutable timelock;
    /// @notice Quorum as a WAD fraction of registered persons that must reach steady-state support.
    uint256 public immutable quorumWad;

    struct Proposal {
        bytes32 actionHash; // keccak256(abi.encode(targets, values, payloads, salt))
        uint256 support; // S_total: active supporters (persons)
        uint256 conviction; // Y_total at lastUpdate, WAD-scaled persons
        uint64 lastUpdate;
        bool scheduled;
        bool exists;
    }

    mapping(uint256 => Proposal) public proposals;
    mapping(uint256 => mapping(uint256 => uint8)) public voteState; // proposalId => nullifier => 0 none,1 active,2 withdrawn
    uint256 public proposalCount;

    event Proposed(uint256 indexed id, bytes32 actionHash);
    event VoteCast(uint256 indexed id, uint256 nullifierHash);
    event VoteWithdrawn(uint256 indexed id, uint256 nullifierHash);
    event Scheduled(uint256 indexed id, uint256 conviction, uint256 threshold);

    error ZeroAddress();
    error BadQuorum();
    error UnknownProposal();
    error NullifierUsed();
    error NotActiveVoter();
    error InvalidPersonhoodProof();
    error ThresholdNotMet(uint256 conviction, uint256 threshold);
    error AlreadyScheduled();
    error ActionMismatch();
    error NoElectorate();

    constructor(IPersonhood personhood_, ITimelockScheduler timelock_, uint256 quorumWad_) {
        if (address(personhood_) == address(0) || address(timelock_) == address(0)) revert ZeroAddress();
        if (quorumWad_ == 0 || quorumWad_ > WAD) revert BadQuorum();
        personhood = personhood_;
        timelock = timelock_;
        quorumWad = quorumWad_;
    }

    // ------------------------------------------------------------------ math

    /// @dev x^n in WAD fixed point (square-and-multiply, round-half-up).
    function rpow(uint256 x, uint256 n) public pure returns (uint256 z) {
        z = n % 2 != 0 ? x : WAD;
        for (n /= 2; n != 0; n /= 2) {
            x = (x * x + WAD / 2) / WAD;
            if (n % 2 != 0) z = (z * x + WAD / 2) / WAD;
        }
    }

    function _decayed(Proposal storage p) internal view returns (uint256) {
        uint256 dt = block.timestamp - p.lastUpdate;
        if (dt == 0) return p.conviction;
        uint256 a = rpow(ALPHA_PER_SECOND, dt);
        return (a * p.conviction + (WAD - a) * p.support * WAD) / WAD;
    }

    function _checkpoint(Proposal storage p) internal {
        p.conviction = _decayed(p);
        p.lastUpdate = uint64(block.timestamp);
    }

    /// @notice Current aggregate conviction Y (WAD-scaled persons).
    function convictionOf(uint256 id) public view returns (uint256) {
        Proposal storage p = proposals[id];
        if (!p.exists) revert UnknownProposal();
        return _decayed(p);
    }

    /// @notice Conviction required: quorum share of the electorate at full steady state.
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

    /// @notice Anyone may propose an action batch; it executes only via the timelock.
    function propose(address[] calldata targets, uint256[] calldata values, bytes[] calldata payloads, bytes32 salt)
        external
        returns (uint256 id)
    {
        id = ++proposalCount;
        bytes32 h = hashAction(targets, values, payloads, salt);
        proposals[id] = Proposal(h, 0, 0, uint64(block.timestamp), false, true);
        emit Proposed(id, h);
    }

    /// @notice Cast one person's support. `nullifierHash` = H(15, personSecret, id); usable once.
    function castVote(uint256 id, uint256 nullifierHash, bytes calldata proof) external {
        Proposal storage p = proposals[id];
        if (!p.exists) revert UnknownProposal();
        if (p.scheduled) revert AlreadyScheduled();
        if (voteState[id][nullifierHash] != 0) revert NullifierUsed();
        if (!personhood.verifyPersonhood(nullifierHash, id, 1, proof)) revert InvalidPersonhoodProof();
        _checkpoint(p);
        voteState[id][nullifierHash] = 1;
        p.support += 1;
        emit VoteCast(id, nullifierHash);
    }

    /// @notice Withdraw support (proof re-binds the same nullifier with signal 0). Cannot re-vote.
    function withdrawVote(uint256 id, uint256 nullifierHash, bytes calldata proof) external {
        Proposal storage p = proposals[id];
        if (!p.exists) revert UnknownProposal();
        if (voteState[id][nullifierHash] != 1) revert NotActiveVoter();
        if (!personhood.verifyPersonhood(nullifierHash, id, 0, proof)) revert InvalidPersonhoodProof();
        _checkpoint(p);
        voteState[id][nullifierHash] = 2;
        p.support -= 1;
        emit VoteWithdrawn(id, nullifierHash);
    }

    /// @notice Once conviction crosses the threshold, hand the batch to the timelock with the
    ///         strictest delay any call in it requires.
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
        _requireThreshold(id, p);
        p.scheduled = true;
        uint256 delay = _maxDelay(targets, payloads);
        bytes32 tlSalt = keccak256(abi.encode(id, salt));
        timelock.scheduleBatch(targets, values, payloads, bytes32(0), tlSalt, delay);
    }

    function _requireThreshold(uint256 id, Proposal storage p) internal {
        uint256 y = _decayed(p);
        uint256 thr = threshold();
        if (y < thr) revert ThresholdNotMet(y, thr);
        emit Scheduled(id, y, thr);
    }

    function _maxDelay(address[] calldata targets, bytes[] calldata payloads) internal view returns (uint256 delay) {
        for (uint256 i = 0; i < targets.length; ++i) {
            uint256 d = timelock.requiredDelay(targets[i], payloads[i]);
            if (d > delay) delay = d;
        }
    }
}
