// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import {PausableUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {CirculatoryEngine, IStateVerifier} from "../../../contracts/CirculatoryEngine.sol";
import {ParameterRegistry} from "../../../contracts/governance/ParameterRegistry.sol";
import {GovernanceTimelock} from "../../../contracts/governance/GovernanceTimelock.sol";
import {ConvictionVoting, ITimelockScheduler, IParamReader} from "../../../contracts/governance/ConvictionVoting.sol";
import {IPluralWeight} from "../../../contracts/governance/IPluralWeight.sol";
import {UbiGate} from "../../../contracts/governance/UbiGate.sol";
import {BountyModule, IEvidenceVerifier} from "../../../contracts/governance/BountyModule.sol";
import {DoubleDepositVerifier} from "../../../contracts/governance/DoubleDepositVerifier.sol";
import {InvalidProofVerifier} from "../../../contracts/governance/InvalidProofVerifier.sol";
import {FinalizeEngine} from "../../../script/FinalizeEngine.s.sol";
import {HandoffEngineOwnership} from "../../../script/HandoffEngineOwnership.s.sol";
import {MockSemaphorePersonhood} from "./Mocks.sol";
import {GovernanceBase, AcceptAll} from "./Governance.t.sol";

contract RejectAll is IStateVerifier {
    function verifyProof(uint256[2] calldata, uint256[2][2] calldata, uint256[2] calldata, uint256[4] calldata)
        external
        pure
        returns (bool)
    {
        return false;
    }
}

contract RevertAll is IStateVerifier {
    function verifyProof(uint256[2] calldata, uint256[2][2] calldata, uint256[2] calldata, uint256[4] calldata)
        external
        pure
        returns (bool)
    {
        revert("boom");
    }
}

contract HalfWeight is IPluralWeight {
    uint256 public w = 0.5e18;

    function set(uint256 w_) external {
        w = w_;
    }

    function weightOf(uint256, uint256) external view returns (uint256) {
        return w;
    }
}

contract VotingRound2Test is GovernanceBase {
    UbiGate gate;

    function setUp() public {
        _deploy(14 days, 30 days);
        gate = new UbiGate(address(tl));
    }

    function _voteSide(uint256 id, uint256 secret, bool against) internal {
        uint256 nf = ph.nullifierOf(secret, id);
        cv.castVote(id, nf, abi.encode(secret), against);
    }

    function _run(address target, bytes memory data, uint256 nFor, uint256 nAgainst)
        internal
        returns (uint256 id, address[] memory ts, uint256[] memory vs, bytes[] memory ds)
    {
        (ts, vs, ds) = _one(target, data);
        id = cv.propose(ts, vs, ds, 0);
        for (uint256 i = 1; i <= nFor; ++i) {
            _voteSide(id, i, false);
        }
        for (uint256 i = nFor + 1; i <= nFor + nAgainst; ++i) {
            _voteSide(id, i, true);
        }
        vm.warp(block.timestamp + 120 days); // ~steady state
    }

    function test_decayFactorAccuracy() public view {
        assertApproxEqRel(cv.decayFactor(7 days, 7 days), 0.5e18, 1e9);
        assertApproxEqRel(cv.decayFactor(3.5 days, 7 days), 707106781186547524, 1e9);
        assertApproxEqRel(cv.decayFactor(21 days, 7 days), 0.125e18, 1e9);
        assertEq(cv.decayFactor(0, 7 days), 1e18);
        assertEq(cv.decayFactor(7 days * 200, 7 days), 0);
    }

    function testFuzz_decayMonotone(uint32 a, uint32 b) public view {
        (uint256 lo, uint256 hi) = a < b ? (uint256(a), uint256(b)) : (uint256(b), uint256(a));
        uint256 h = cv.halfLife();
        assertLe(cv.decayFactor(hi, h), cv.decayFactor(lo, h));
        assertLe(cv.decayFactor(lo, h), 1e18);
    }

    function test_halfLifeFromRegistryWithinBounds() public {
        bytes32 k = reg.VOTE_HALF_LIFE();
        vm.startPrank(address(tl));
        vm.expectRevert(abi.encodeWithSelector(ParameterRegistry.OutOfBounds.selector, k, 2 days));
        reg.setParameter(k, 2 days);
        reg.setParameter(k, 10 days);
        vm.stopPrank();
        assertEq(cv.halfLife(), 10 days);
        (address[] memory ts, uint256[] memory vs, bytes[] memory ds) = _one(address(reg), hex"00");
        uint256 id = cv.propose(ts, vs, ds, 0);
        for (uint256 i = 1; i <= N; ++i) {
            _voteSide(id, i, false);
        }
        vm.warp(block.timestamp + 10 days);
        assertApproxEqRel(_for(id), 5e18, 1e12);
    }

    function test_simpleMajorityWithAgainst() public {
        (uint256 id, address[] memory ts, uint256[] memory vs, bytes[] memory ds) =
            _run(address(reg), abi.encodeCall(ParameterRegistry.setParameter, (reg.DEMURRAGE_RATE(), 0.0006e18)), 6, 4);
        (, bool constitutional,) = _flags(id);
        assertFalse(constitutional);
        cv.schedule(id, ts, vs, ds, 0);
    }

    function test_againstCanBlockSimpleMajority() public {
        (uint256 id, address[] memory ts, uint256[] memory vs, bytes[] memory ds) =
            _run(address(reg), abi.encodeCall(ParameterRegistry.setParameter, (reg.DEMURRAGE_RATE(), 0.0006e18)), 5, 5);
        vm.expectRevert(); // MajorityNotMet: 50% is not > 50%
        cv.schedule(id, ts, vs, ds, 0);
    }

    function test_ubiGateNeedsSupermajority() public {
        bytes memory data = abi.encodeCall(UbiGate.setEnabled, (true));
        assertEq(tl.requiredDelay(address(gate), data), 30 days);
        (uint256 id, address[] memory ts, uint256[] memory vs, bytes[] memory ds) = _run(address(gate), data, 6, 4);
        (, bool constitutional,) = _flags(id);
        assertTrue(constitutional);
        vm.expectRevert(); // 60% <= 66%
        cv.schedule(id, ts, vs, ds, 0);
    }

    function test_ubiGatePassesAt70PctAfter30Days() public {
        bytes memory data = abi.encodeCall(UbiGate.setEnabled, (true));
        (uint256 id, address[] memory ts, uint256[] memory vs, bytes[] memory ds) = _run(address(gate), data, 7, 3);
        cv.schedule(id, ts, vs, ds, 0);
        bytes32 salt = keccak256(abi.encode(id, bytes32(0)));
        vm.warp(block.timestamp + 30 days - 1);
        vm.expectRevert();
        tl.executeBatch(ts, vs, ds, 0, salt);
        vm.warp(block.timestamp + 1);
        tl.executeBatch(ts, vs, ds, 0, salt);
        assertTrue(gate.enabled());
    }

    function test_ubiGateOnlyTimelock() public {
        vm.expectRevert(UbiGate.NotTimelock.selector);
        gate.setEnabled(true);
    }

    function test_againstWithdraw() public {
        (address[] memory ts, uint256[] memory vs, bytes[] memory ds) = _one(address(reg), hex"00");
        uint256 id = cv.propose(ts, vs, ds, 0);
        _voteSide(id, 1, true);
        vm.warp(block.timestamp + 60 days);
        (, uint256 ya) = cv.convictionOf(id);
        assertGt(ya, 0.99e18);
        cv.withdrawVote(id, ph.nullifierOf(1, id), abi.encode(uint256(1)));
        vm.warp(block.timestamp + 7 days);
        (, uint256 ya2) = cv.convictionOf(id);
        assertApproxEqRel(ya2, ya / 2, 1e12);
    }

    function test_pluralWeightHook() public {
        HalfWeight hw = new HalfWeight();
        ConvictionVoting cv2 = new ConvictionVoting(
            ph, ITimelockScheduler(address(tl)), IParamReader(address(reg)), IPluralWeight(address(hw)), 0.5e18
        );
        (address[] memory ts, uint256[] memory vs, bytes[] memory ds) = _one(address(reg), hex"00");
        uint256 id = cv2.propose(ts, vs, ds, 0);
        cv2.castVote(id, ph.nullifierOf(1, id), abi.encode(uint256(1)), false);
        hw.set(5e18); // over-weight is clamped to 1
        cv2.castVote(id, ph.nullifierOf(2, id), abi.encode(uint256(2)), false);
        vm.warp(block.timestamp + 200 days);
        (uint256 yf,) = cv2.convictionOf(id);
        assertApproxEqRel(yf, 1.5e18, 1e12);
        (,, uint256 w) = cv2.votes(id, ph.nullifierOf(2, id));
        assertEq(w, 1e18);
    }

    function test_defaultWeightIsOne() public {
        (address[] memory ts, uint256[] memory vs, bytes[] memory ds) = _one(address(reg), hex"00");
        uint256 id = cv.propose(ts, vs, ds, 0);
        _voteSide(id, 1, false);
        (,, uint256 w) = cv.votes(id, ph.nullifierOf(1, id));
        assertEq(w, 1e18);
    }

    function _flags(uint256 id) internal view returns (bool scheduled, bool constitutional, bool exists) {
        (,,,,,, constitutional, scheduled, exists) = cv.proposals(id);
    }
}

contract EngineFixture is Test {
    CirculatoryEngine engine;
    GovernanceTimelock tl;
    address owner = address(0x0E);
    address proposer = address(0x9);
    address guardian = address(0x6A);
    address settler = address(0x5E);

    function _setup() internal {
        CirculatoryEngine impl = new CirculatoryEngine();
        engine = CirculatoryEngine(
            payable(address(
                    new ERC1967Proxy(
                        address(impl),
                        abi.encodeCall(CirculatoryEngine.initialize, (owner, new AcceptAll(), 5e6 ether, 0, 0, 0))
                    )
                ))
        );
        address[] memory p = new address[](1);
        p[0] = proposer;
        address[] memory e = new address[](1);
        tl = new GovernanceTimelock(14 days, 30 days, p, e, address(this));
        tl.setGuardian(guardian, 90 days);
        tl.setPauseTarget(address(engine), true);
        tl.renounceRole(tl.DEFAULT_ADMIN_ROLE(), address(this));
        new HandoffEngineOwnership().handoff(engine, tl, settler, owner);
    }

    function _viaTimelock(address target, bytes memory data, uint256 delay) internal {
        vm.prank(proposer);
        tl.schedule(target, 0, data, 0, keccak256(data), delay);
        vm.warp(block.timestamp + delay);
        tl.execute(target, 0, data, 0, keccak256(data));
    }
}

contract GuardianTest is EngineFixture {
    function setUp() public {
        _setup();
    }

    function test_guardianCanPause() public {
        vm.prank(guardian);
        tl.guardianPause(address(engine));
        assertTrue(engine.paused());
    }

    function test_guardianCannotUnpauseUpgradeOrMoveFunds() public {
        vm.prank(guardian);
        tl.guardianPause(address(engine));
        vm.startPrank(guardian);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, guardian));
        engine.unpause();
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, guardian));
        engine.redistribute(guardian, 0);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, guardian));
        engine.upgradeToAndCall(address(1), "");
        vm.expectRevert(); // no proposer role
        tl.schedule(address(engine), 0, abi.encodeWithSignature("unpause()"), 0, 0, 30 days);
        vm.expectRevert(GovernanceTimelock.NotAdmin.selector);
        tl.setPauseTarget(address(0xDEAD), true);
        vm.expectRevert(GovernanceTimelock.NotAdmin.selector);
        tl.setGuardian(guardian, 180 days);
        vm.stopPrank();
        // unpause only via a full 30-day proposal
        _viaTimelock(address(engine), abi.encodeWithSignature("unpause()"), 30 days);
        assertFalse(engine.paused());
    }

    function test_onlyGuardianAndOnlyTargets() public {
        vm.expectRevert(GovernanceTimelock.NotGuardian.selector);
        tl.guardianPause(address(engine));
        vm.prank(guardian);
        vm.expectRevert(abi.encodeWithSelector(GovernanceTimelock.NotPauseTarget.selector, address(0xBEEF)));
        tl.guardianPause(address(0xBEEF));
    }

    function test_guardianSunsets() public {
        assertTrue(tl.guardianActive());
        vm.warp(block.timestamp + 90 days);
        assertFalse(tl.guardianActive());
        vm.prank(guardian);
        vm.expectRevert(GovernanceTimelock.GuardianExpired.selector);
        tl.guardianPause(address(engine));
    }

    function test_revocableByTimelock30d() public {
        bytes memory data = abi.encodeCall(GovernanceTimelock.revokeGuardian, ());
        assertEq(tl.requiredDelay(address(tl), data), 30 days);
        _viaTimelock(address(tl), data, 30 days);
        assertEq(tl.guardian(), address(0));
        vm.prank(guardian);
        vm.expectRevert(GovernanceTimelock.NotGuardian.selector);
        tl.guardianPause(address(engine));
    }

    function test_guardianCanSelfRevoke() public {
        vm.prank(guardian);
        tl.revokeGuardian();
        assertFalse(tl.guardianActive());
    }

    function test_sunsetCapped() public {
        address[] memory none = new address[](0);
        GovernanceTimelock t = new GovernanceTimelock(14 days, 30 days, none, none, address(this));
        vm.expectRevert(abi.encodeWithSelector(GovernanceTimelock.SunsetTooLong.selector, 181 days));
        t.setGuardian(guardian, 181 days);
    }
}

contract FinalizeTest is EngineFixture {
    FinalizeEngine fin;

    function setUp() public {
        _setup();
        fin = new FinalizeEngine();
    }

    function _revokeGuardian() internal {
        _viaTimelock(address(tl), abi.encodeCall(GovernanceTimelock.revokeGuardian, ()), 30 days);
    }

    function test_finalizeMakesEngineImmutable() public {
        _revokeGuardian();
        (address target,, bytes memory data) = fin.build(engine, tl);
        assertEq(tl.requiredDelay(target, data), 30 days);
        vm.prank(proposer);
        vm.expectRevert(abi.encodeWithSelector(GovernanceTimelock.DelayTooShort.selector, 14 days, 30 days));
        tl.schedule(target, 0, data, 0, 0, 14 days);
        _viaTimelock(target, data, 30 days);
        assertEq(engine.owner(), address(0));

        address newImpl = address(new CirculatoryEngine());
        bytes memory up = abi.encodeWithSignature("upgradeToAndCall(address,bytes)", newImpl, "");
        vm.prank(proposer);
        tl.schedule(address(engine), 0, up, 0, keccak256("u"), 30 days);
        vm.warp(block.timestamp + 30 days);
        vm.expectRevert(); // _authorizeUpgrade: onlyOwner, owner == 0 => permanently disabled
        tl.execute(address(engine), 0, up, 0, keccak256("u"));
        vm.prank(address(0));
        vm.expectRevert(); // owner()==0 check passes only for msg.sender==0 (unsignable); the call still fails
        engine.upgradeToAndCall(address(1), "");
        assertEq(engine.settler(), settler); // settlement survives
    }

    function test_gateRejectsActiveGuardian() public {
        vm.expectRevert(FinalizeEngine.GuardianStillActive.selector);
        fin.build(engine, tl);
    }

    function test_gateRejectsPaused() public {
        vm.prank(guardian);
        tl.guardianPause(address(engine));
        _revokeGuardian();
        vm.expectRevert(FinalizeEngine.EnginePaused.selector);
        fin.build(engine, tl);
    }

    function test_gateRejectsTimelockSettler() public {
        _viaTimelock(address(engine), abi.encodeCall(CirculatoryEngine.setSettler, (address(tl))), 30 days);
        _revokeGuardian();
        vm.expectRevert(abi.encodeWithSelector(FinalizeEngine.BadSettler.selector, address(tl)));
        fin.build(engine, tl);
    }

    function test_gateRejectsNotTimelockOwned() public {
        CirculatoryEngine impl = new CirculatoryEngine();
        CirculatoryEngine e2 = CirculatoryEngine(
            payable(address(
                    new ERC1967Proxy(
                        address(impl),
                        abi.encodeCall(CirculatoryEngine.initialize, (owner, new AcceptAll(), 1, 0, 0, 0))
                    )
                ))
        );
        vm.expectRevert(abi.encodeWithSelector(FinalizeEngine.NotTimelockOwned.selector, owner));
        fin.build(e2, tl);
    }
}

contract BountyRound2Test is Test {
    BountyModule b;
    address constant TL = address(0x71);
    address constant VAULT = address(0x7A);
    address rep = address(0xBEE);
    uint256 pk = 0xA11CE;
    address offender;
    bytes32 oid;

    function setUp() public {
        offender = vm.addr(pk);
        oid = bytes32(uint256(uint160(offender)));
        b = new BountyModule(TL, VAULT, 0.1 ether, 0.5 ether, 0.5e18, 7 days);
        vm.deal(offender, 10 ether);
        vm.deal(rep, 1 ether);
        vm.deal(address(this), 10 ether);
        vm.prank(offender);
        b.bond{value: 2 ether}(oid);
    }

    function test_unbondChallengeWindow() public {
        vm.startPrank(offender);
        vm.expectRevert(BountyModule.NotUnbonding.selector);
        b.withdrawBond(oid);
        b.requestUnbond(oid);
        vm.expectRevert(BountyModule.Unbonding.selector);
        b.bond{value: 1}(oid);
        vm.expectRevert(abi.encodeWithSelector(BountyModule.ChallengeWindowOpen.selector, block.timestamp + 7 days));
        b.withdrawBond(oid);
        vm.warp(block.timestamp + 7 days);
        b.withdrawBond(oid);
        b.withdraw();
        vm.stopPrank();
        assertEq(offender.balance, 10 ether);
    }

    function test_onlyOwnerUnbonds() public {
        vm.expectRevert(BountyModule.NotBondOwner.selector);
        b.requestUnbond(oid);
    }

    function test_reportDuringWindowSlashes() public {
        MockOk ok = new MockOk();
        vm.prank(TL);
        b.setVerifier(BountyModule.Offence.InvalidProof, ok);
        vm.prank(offender);
        b.requestUnbond(oid);
        vm.warp(block.timestamp + 6 days);
        vm.prank(rep);
        assertTrue(b.report{value: 0.1 ether}(BountyModule.Offence.InvalidProof, oid, "x"));
        vm.warp(block.timestamp + 2 days);
        vm.prank(offender);
        vm.expectRevert(BountyModule.NotUnbonding.selector);
        b.withdrawBond(oid);
    }

    function test_tauFromCappedPool() public {
        MockOk ok = new MockOk();
        vm.startPrank(TL);
        b.setVerifier(BountyModule.Offence.InvalidProof, ok);
        b.setRewardParams(0.5e18, 0.3 ether);
        vm.stopPrank();
        b.fundPool{value: 0.2 ether}(); // pool smaller than tau
        (uint256 fb, uint256 fp) = b.rewardSplit(2 ether);
        assertEq(fb, 1 ether);
        assertEq(fp, 0.2 ether);
        vm.prank(rep);
        b.report{value: 0.1 ether}(BountyModule.Offence.InvalidProof, oid, "x");
        assertEq(b.withdrawable(rep), 1.3 ether);
        assertEq(b.withdrawable(VAULT), 1 ether); // b_o - sigma*b_o: offender bond never pays tau
        assertEq(b.pool(), 0);
    }

    function testFuzz_rewardNeverExceedsBond(uint96 bo, uint64 sigma, uint64 tau, uint96 poolAmt) public {
        sigma = uint64(bound(sigma, 1, 1e18 - 1));
        BountyModule m = new BountyModule(TL, VAULT, 1, type(uint64).max, sigma, 1);
        vm.prank(TL);
        m.setRewardParams(sigma, tau);
        vm.deal(address(this), poolAmt);
        m.fundPool{value: poolAmt}();
        (uint256 fb, uint256 fp) = m.rewardSplit(bo);
        assertLe(fb + fp, bo);
        assertLe(fp, tau);
        assertLe(fp, poolAmt);
    }

    // ---------------- DoubleDepositVerifier
    function _auth(DoubleDepositVerifier v, uint256 key, uint256 note, uint256 era, address to, uint256 amt)
        internal
        view
        returns (DoubleDepositVerifier.Auth memory a)
    {
        bytes32 d = MessageHashUtils.toEthSignedMessageHash(v.depositDigest(note, era, to, amt));
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(key, d);
        a = DoubleDepositVerifier.Auth(to, amt, abi.encodePacked(r, s, vv));
    }

    function test_doubleDepositVerifier() public {
        DoubleDepositVerifier v = new DoubleDepositVerifier(address(0xD3));
        DoubleDepositVerifier.Auth memory a = _auth(v, pk, 7, 3, address(1), 100);
        DoubleDepositVerifier.Auth memory c = _auth(v, pk, 7, 3, address(2), 100);
        assertTrue(v.verify(oid, abi.encode(uint256(7), uint256(3), a, c)));
        assertFalse(v.verify(oid, abi.encode(uint256(7), uint256(3), a, a))); // same authorization
        assertFalse(v.verify(oid, abi.encode(uint256(7), uint256(4), a, c))); // other era: sigs don't match
        DoubleDepositVerifier.Auth memory x = _auth(v, 0xB0B, 7, 3, address(2), 100);
        assertFalse(v.verify(oid, abi.encode(uint256(7), uint256(3), a, x))); // different signer
        assertFalse(v.verify(bytes32(type(uint256).max), abi.encode(uint256(7), uint256(3), a, c))); // bad id

        vm.prank(TL);
        b.setVerifier(BountyModule.Offence.DoubleDeposit, v);
        vm.prank(rep);
        assertTrue(
            b.report{value: 0.1 ether}(
                BountyModule.Offence.DoubleDeposit, oid, abi.encode(uint256(7), uint256(3), a, c)
            )
        );
    }

    // ---------------- InvalidProofVerifier
    function _proofEvidence(InvalidProofVerifier v, uint256 key) internal view returns (bytes memory) {
        uint256[2] memory pA = [uint256(1), 2];
        uint256[2][2] memory pB = [[uint256(3), 4], [uint256(5), 6]];
        uint256[2] memory pC = [uint256(7), 8];
        uint256[4] memory ps = [uint256(9), 10, 11, 12];
        bytes32 d = MessageHashUtils.toEthSignedMessageHash(v.submissionDigest(pA, pB, pC, ps));
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(key, d);
        return abi.encode(pA, pB, pC, ps, abi.encodePacked(r, s, vv));
    }

    function test_invalidProofVerifier() public {
        InvalidProofVerifier rej = new InvalidProofVerifier(new RejectAll());
        InvalidProofVerifier acc = new InvalidProofVerifier(new AcceptAll());
        InvalidProofVerifier rev = new InvalidProofVerifier(new RevertAll());
        assertTrue(rej.verify(oid, _proofEvidence(rej, pk)));
        assertFalse(acc.verify(oid, _proofEvidence(acc, pk))); // proof is valid -> no offence
        assertFalse(rev.verify(oid, _proofEvidence(rev, pk))); // verifier revert -> fail closed
        assertFalse(rej.verify(oid, _proofEvidence(rej, 0xB0B))); // not signed by offender

        vm.prank(TL);
        b.setVerifier(BountyModule.Offence.InvalidProof, rej);
        vm.prank(rep);
        assertTrue(b.report{value: 0.1 ether}(BountyModule.Offence.InvalidProof, oid, _proofEvidence(rej, pk)));
    }

    function test_todoTypesFailClosed() public {
        vm.startPrank(rep);
        vm.expectRevert(BountyModule.NoVerifier.selector);
        b.report{value: 0.1 ether}(BountyModule.Offence.BribeOffer, oid, "");
        vm.expectRevert(BountyModule.NoVerifier.selector);
        b.report{value: 0.1 ether}(BountyModule.Offence.FakeIdentityVouch, oid, "");
    }
}

contract MockOk is IEvidenceVerifier {
    function verify(bytes32, bytes calldata) external pure returns (bool) {
        return true;
    }
}
