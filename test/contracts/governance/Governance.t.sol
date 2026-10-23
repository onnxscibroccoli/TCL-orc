// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";
import {CirculatoryEngine, IStateVerifier} from "../../../contracts/CirculatoryEngine.sol";
import {ParameterRegistry} from "../../../contracts/governance/ParameterRegistry.sol";
import {GovernanceTimelock} from "../../../contracts/governance/GovernanceTimelock.sol";
import {ConvictionVoting, ITimelockScheduler} from "../../../contracts/governance/ConvictionVoting.sol";
import {TieredPersonhoodLimits} from "../../../contracts/governance/TieredPersonhoodLimits.sol";
import {BountyModule, IEvidenceVerifier} from "../../../contracts/governance/BountyModule.sol";
import {HandoffEngineOwnership} from "../../../script/HandoffEngineOwnership.s.sol";
import {MockSemaphorePersonhood, MockEvidenceVerifier} from "./Mocks.sol";

contract AcceptAll is IStateVerifier {
    function verifyProof(uint256[2] calldata, uint256[2][2] calldata, uint256[2] calldata, uint256[4] calldata)
        external
        pure
        returns (bool)
    {
        return true;
    }
}

contract GovernanceBase is Test {
    GovernanceTimelock tl;
    ParameterRegistry reg;
    ConvictionVoting cv;
    MockSemaphorePersonhood ph;
    uint256 constant N = 10;

    function _deploy(uint256 pd, uint256 ud) internal {
        address[] memory none = new address[](0);
        address[] memory anyone = new address[](1);
        anyone[0] = address(0);
        tl = new GovernanceTimelock(pd, ud, none, anyone, address(this));
        reg = new ParameterRegistry(address(tl), 14 days);
        ph = new MockSemaphorePersonhood();
        cv = new ConvictionVoting(ph, ITimelockScheduler(address(tl)), 0.5e18);
        tl.grantRole(tl.PROPOSER_ROLE(), address(cv));
        tl.grantRole(tl.CANCELLER_ROLE(), address(cv));
        tl.setRegistry(address(reg));
        tl.renounceRole(tl.DEFAULT_ADMIN_ROLE(), address(this));
        for (uint256 i = 1; i <= N; ++i) {
            ph.addMember(i);
        }
    }

    function _one(address t, bytes memory d)
        internal
        pure
        returns (address[] memory ts, uint256[] memory vs, bytes[] memory ds)
    {
        ts = new address[](1);
        vs = new uint256[](1);
        ds = new bytes[](1);
        ts[0] = t;
        ds[0] = d;
    }

    function _vote(uint256 id, uint256 secret) internal {
        cv.castVote(id, ph.nullifierOf(secret, id), abi.encode(secret));
    }
}

contract ParameterRegistryTest is GovernanceBase {
    bytes32 R;

    function setUp() public {
        _deploy(14 days, 30 days);
        R = reg.DEMURRAGE_RATE();
    }

    function test_defaults() public view {
        assertEq(reg.get(R), 0.0005e18);
        assertEq(reg.get(reg.HOARD_MULTIPLIER()), 9);
        assertEq(reg.get(reg.MINT_REWARD()), 0.002e18);
        assertEq(reg.keys().length, 11);
    }

    function test_onlyTimelock() public {
        vm.expectRevert(ParameterRegistry.NotTimelock.selector);
        reg.setParameter(R, 0.0006e18);
    }

    function test_unknownKeyReverts() public {
        vm.prank(address(tl));
        vm.expectRevert(abi.encodeWithSelector(ParameterRegistry.UnknownParameter.selector, bytes32("x")));
        reg.setParameter(bytes32("x"), 1);
    }

    function test_bounds() public {
        vm.startPrank(address(tl));
        vm.expectRevert(abi.encodeWithSelector(ParameterRegistry.OutOfBounds.selector, R, 0.0021e18));
        reg.setParameter(R, 0.0021e18);
        vm.expectRevert(abi.encodeWithSelector(ParameterRegistry.OutOfBounds.selector, R, 0.00009e18));
        reg.setParameter(R, 0.00009e18);
    }

    function test_relativeStep() public {
        vm.startPrank(address(tl));
        vm.expectRevert(abi.encodeWithSelector(ParameterRegistry.StepTooLarge.selector, R, 0.0005e18, 0.000626e18));
        reg.setParameter(R, 0.000626e18); // > 25%
        reg.setParameter(R, 0.000625e18); // exactly 25%
        assertEq(reg.get(R), 0.000625e18);
    }

    function test_absoluteStep() public {
        bytes32 k = reg.HOARD_MULTIPLIER();
        vm.startPrank(address(tl));
        vm.expectRevert(abi.encodeWithSelector(ParameterRegistry.StepTooLarge.selector, k, 9, 12));
        reg.setParameter(k, 12);
        reg.setParameter(k, 11);
    }

    function test_oncePerCycle() public {
        vm.startPrank(address(tl));
        reg.setParameter(R, 0.0006e18);
        vm.expectRevert(abi.encodeWithSelector(ParameterRegistry.AlreadyChangedThisCycle.selector, R));
        reg.setParameter(R, 0.0007e18);
        vm.warp(block.timestamp + 14 days);
        reg.setParameter(R, 0.0007e18);
    }

    function testFuzz_acceptedValuesStayInBounds(uint256 keyIdx, uint256 v) public {
        bytes32[] memory ks = reg.keys();
        bytes32 k = ks[keyIdx % ks.length];
        ParameterRegistry.Param memory p = reg.getParam(k);
        vm.prank(address(tl));
        try reg.setParameter(k, v) {
            assertGe(v, p.min);
            assertLe(v, p.max);
            uint256 d = v > p.value ? v - p.value : p.value - v;
            assertLe(d, p.maxStepAbs > 0 ? p.maxStepAbs : p.value * p.maxStepBps / 10_000);
        } catch {
            assertEq(reg.get(k), p.value);
        }
    }
}

contract TimelockTest is GovernanceBase {
    function setUp() public {
        _deploy(14 days, 30 days);
    }

    function test_classification() public view {
        bytes memory setP = abi.encodeCall(ParameterRegistry.setParameter, (reg.DEMURRAGE_RATE(), 1));
        assertEq(tl.requiredDelay(address(reg), setP), 14 days);
        assertEq(tl.requiredDelay(address(0xBEEF), setP), 30 days); // wrong target
        assertEq(tl.requiredDelay(address(reg), hex"deadbeef"), 30 days); // other selector
        assertEq(tl.requiredDelay(address(tl), abi.encodeCall(TimelockController.updateDelay, (1))), 30 days);
    }

    function test_scheduleDelaysEnforced() public {
        bytes memory setP = abi.encodeCall(ParameterRegistry.setParameter, (reg.DEMURRAGE_RATE(), 0.0006e18));
        vm.startPrank(address(cv));
        vm.expectRevert(abi.encodeWithSelector(GovernanceTimelock.DelayTooShort.selector, 14 days - 1, 14 days));
        tl.schedule(address(reg), 0, setP, 0, 0, 14 days - 1);
        tl.schedule(address(reg), 0, setP, 0, 0, 14 days);
        bytes memory up = abi.encodeWithSignature("upgradeToAndCall(address,bytes)", address(1), "");
        vm.expectRevert(abi.encodeWithSelector(GovernanceTimelock.DelayTooShort.selector, 14 days, 30 days));
        tl.schedule(address(0xBEEF), 0, up, 0, 0, 14 days);
        tl.schedule(address(0xBEEF), 0, up, 0, 0, 30 days);
        vm.stopPrank();

        vm.warp(block.timestamp + 14 days - 1);
        vm.expectRevert();
        tl.execute(address(reg), 0, setP, 0, 0);
        vm.warp(block.timestamp + 1);
        tl.execute(address(reg), 0, setP, 0, 0);
        assertEq(reg.get(reg.DEMURRAGE_RATE()), 0.0006e18);
    }

    function test_batchUsesStrictestDelay() public {
        (address[] memory ts, uint256[] memory vs, bytes[] memory ds) =
            _one(address(reg), abi.encodeCall(ParameterRegistry.setParameter, (reg.DEMURRAGE_RATE(), 0.0006e18)));
        address[] memory t2 = new address[](2);
        uint256[] memory v2 = new uint256[](2);
        bytes[] memory d2 = new bytes[](2);
        t2[0] = ts[0];
        d2[0] = ds[0];
        t2[1] = address(0xBEEF);
        d2[1] = hex"01";
        vs;
        vm.prank(address(cv));
        vm.expectRevert(abi.encodeWithSelector(GovernanceTimelock.DelayTooShort.selector, 14 days, 30 days));
        tl.scheduleBatch(t2, v2, d2, 0, 0, 14 days);
    }

    function test_onlyProposerCanSchedule() public {
        vm.expectRevert();
        tl.schedule(address(reg), 0, "", 0, 0, 30 days);
    }

    function test_badDelaysRejected() public {
        address[] memory none = new address[](0);
        vm.expectRevert(GovernanceTimelock.BadDelays.selector);
        new GovernanceTimelock(2 days, 1 days, none, none, address(0));
    }

    function test_lowerDelaysConfigurableForTests() public {
        address[] memory none = new address[](0);
        GovernanceTimelock t = new GovernanceTimelock(1 hours, 2 hours, none, none, address(0));
        assertEq(t.paramDelay(), 1 hours);
        assertEq(t.upgradeDelay(), 2 hours);
    }
}

contract ConvictionVotingTest is GovernanceBase {
    function setUp() public {
        _deploy(14 days, 30 days);
    }

    function _proposeRate(uint256 v)
        internal
        returns (uint256 id, address[] memory ts, uint256[] memory vs, bytes[] memory ds)
    {
        (ts, vs, ds) = _one(address(reg), abi.encodeCall(ParameterRegistry.setParameter, (reg.DEMURRAGE_RATE(), v)));
        id = cv.propose(ts, vs, ds, 0);
    }

    function test_alphaIsSevenDayHalfLife() public view {
        uint256 h = cv.rpow(cv.ALPHA_PER_SECOND(), 7 days);
        assertApproxEqAbs(h, 0.5e18, 1e9);
    }

    function test_flashVoteHasTinyWeight() public {
        (uint256 id, address[] memory ts, uint256[] memory vs, bytes[] memory ds) = _proposeRate(0.0006e18);
        for (uint256 i = 1; i <= N; ++i) {
            _vote(id, i);
        }
        assertEq(cv.convictionOf(id), 0); // same block: zero
        vm.warp(block.timestamp + 12); // one block
        uint256 y = cv.convictionOf(id);
        assertLt(y, 2e-4 * 1e18); // 10 persons * (1 - alpha^12) ~ 1.4e-4
        assertGt(y, 0);
        vm.expectRevert();
        cv.schedule(id, ts, vs, ds, 0);
    }

    function test_convictionBuildsAndSchedules() public {
        (uint256 id, address[] memory ts, uint256[] memory vs, bytes[] memory ds) = _proposeRate(0.0006e18);
        for (uint256 i = 1; i <= N; ++i) {
            _vote(id, i);
        }
        vm.warp(block.timestamp + 7 days);
        assertApproxEqRel(cv.convictionOf(id), 5e18, 1e12); // half of steady state after one half-life
        vm.expectRevert(); // exactly at threshold minus rounding => still below 0.5*N
        cv.schedule(id, ts, vs, ds, 0);
        vm.warp(block.timestamp + 1 days);
        cv.schedule(id, ts, vs, ds, 0);
        bytes32 salt = keccak256(abi.encode(id, bytes32(0)));
        vm.warp(block.timestamp + 14 days - 1);
        vm.expectRevert();
        tl.executeBatch(ts, vs, ds, 0, salt);
        vm.warp(block.timestamp + 1);
        tl.executeBatch(ts, vs, ds, 0, salt);
        assertEq(reg.get(reg.DEMURRAGE_RATE()), 0.0006e18);
    }

    function test_doubleVoteRejected() public {
        (uint256 id,,,) = _proposeRate(0.0006e18);
        _vote(id, 1);
        uint256 nf = ph.nullifierOf(1, id);
        vm.expectRevert(ConvictionVoting.NullifierUsed.selector);
        cv.castVote(id, nf, abi.encode(uint256(1)));
        cv.withdrawVote(id, nf, abi.encode(uint256(1)));
        vm.expectRevert(ConvictionVoting.NullifierUsed.selector);
        cv.castVote(id, nf, abi.encode(uint256(1)));
    }

    function test_invalidProofRejected() public {
        (uint256 id,,,) = _proposeRate(0.0006e18);
        uint256 nf = ph.nullifierOf(999, id);
        vm.expectRevert(ConvictionVoting.InvalidPersonhoodProof.selector);
        cv.castVote(id, nf, abi.encode(uint256(999))); // not a member
        vm.expectRevert(ConvictionVoting.InvalidPersonhoodProof.selector);
        cv.castVote(id, 12345, abi.encode(uint256(1))); // wrong nullifier
    }

    function test_withdrawDecays() public {
        (uint256 id,,,) = _proposeRate(0.0006e18);
        _vote(id, 1);
        vm.warp(block.timestamp + 70 days);
        cv.withdrawVote(id, ph.nullifierOf(1, id), abi.encode(uint256(1)));
        uint256 y0 = cv.convictionOf(id);
        vm.warp(block.timestamp + 7 days);
        assertApproxEqRel(cv.convictionOf(id), y0 / 2, 1e12);
    }

    function test_actionMismatchRejected() public {
        (uint256 id, address[] memory ts, uint256[] memory vs, bytes[] memory ds) = _proposeRate(0.0006e18);
        ds[0] = abi.encodeCall(ParameterRegistry.setParameter, (reg.DEMURRAGE_RATE(), 0.0004e18));
        vm.expectRevert(ConvictionVoting.ActionMismatch.selector);
        cv.schedule(id, ts, vs, ds, 0);
    }
}

contract TieredLimitsTest is Test {
    TieredPersonhoodLimits t;
    address constant ATT = address(0xA77);
    address constant CON = address(0xC0);

    function _arr(uint256 a, uint256 b, uint256 c, uint256 d) internal pure returns (uint256[] memory x) {
        x = new uint256[](4);
        (x[0], x[1], x[2], x[3]) = (a, b, c, d);
    }

    function setUp() public {
        t = new TieredPersonhoodLimits(
            address(1), ATT, CON, 1 days, _arr(3e18, 6e18, 12e18, 24e18), _arr(1000, 1800, 3000, 5000)
        );
    }

    function test_rejectsIncreasingLimitPerVouch() public {
        vm.expectRevert(TieredPersonhoodLimits.BadTiers.selector);
        new TieredPersonhoodLimits(
            address(1), ATT, CON, 1 days, _arr(3e18, 6e18, 12e18, 24e18), _arr(1000, 2001, 3000, 5000)
        );
    }

    function test_unknownIsFailClosed() public {
        vm.prank(CON);
        vm.expectRevert(abi.encodeWithSelector(TieredPersonhoodLimits.LimitExceeded.selector, bytes32("p"), 1, 0));
        t.consume("p", 1);
    }

    function test_tiersAndWindow() public {
        vm.prank(ATT);
        t.attestVouches("p", 6.5e18);
        assertEq(t.tierOf("p"), 2);
        vm.startPrank(CON);
        t.consume("p", 1800);
        vm.expectRevert(abi.encodeWithSelector(TieredPersonhoodLimits.LimitExceeded.selector, bytes32("p"), 1, 0));
        t.consume("p", 1);
        vm.warp(block.timestamp + 1 days);
        t.consume("p", 1800);
    }

    function test_funneledChargesRecipient() public {
        vm.prank(ATT);
        t.attestVouches("r", 3e18);
        vm.prank(ATT);
        t.chargeFunneled("r", 900);
        assertEq(t.remaining("r"), 100);
    }

    function test_onlyRoles() public {
        vm.expectRevert(TieredPersonhoodLimits.NotAttestor.selector);
        t.attestVouches("p", 1);
        vm.expectRevert(TieredPersonhoodLimits.NotConsumer.selector);
        t.consume("p", 0);
    }
}

contract BountyTest is Test {
    BountyModule b;
    MockEvidenceVerifier v;
    address constant TL = address(0x71);
    address constant VAULT = address(0x7A);
    address rep = address(0xBEE);
    bytes32 constant OID = keccak256("prover-1");

    function setUp() public {
        b = new BountyModule(TL, VAULT, 0.1 ether, 0.5 ether, 0.5e18);
        v = new MockEvidenceVerifier();
        vm.prank(TL);
        b.setVerifier(BountyModule.Offence.InvalidProof, v);
        vm.deal(address(this), 10 ether);
        vm.deal(rep, 1 ether);
        b.bond{value: 2 ether}(OID);
    }

    function test_validReportPaysCappedReward() public {
        vm.prank(rep);
        assertTrue(b.report{value: 0.1 ether}(BountyModule.Offence.InvalidProof, OID, abi.encode("valid", OID)));
        assertEq(b.withdrawable(rep), 1 ether + 0.1 ether);
        assertEq(b.withdrawable(VAULT), 1 ether);
        assertEq(b.bonds(OID), 0);
        vm.prank(rep);
        b.withdraw();
        assertEq(rep.balance, 2 ether);
    }

    function test_falseReportForfeitsBond() public {
        vm.prank(rep);
        assertFalse(b.report{value: 0.1 ether}(BountyModule.Offence.InvalidProof, OID, "junk"));
        assertEq(b.withdrawable(VAULT), 0.1 ether);
        assertEq(b.bonds(OID), 2 ether);
    }

    function test_unregisteredEvidenceTypeFailsClosed() public {
        vm.prank(rep);
        vm.expectRevert(BountyModule.NoVerifier.selector);
        b.report{value: 0.1 ether}(BountyModule.Offence.BribeOffer, OID, "");
    }

    function test_wrongReporterBond() public {
        vm.prank(rep);
        vm.expectRevert(BountyModule.WrongReporterBond.selector);
        b.report{value: 0.05 ether}(BountyModule.Offence.InvalidProof, OID, abi.encode("valid", OID));
    }

    function test_onlyTimelockConfigures() public {
        vm.expectRevert(BountyModule.NotTimelock.selector);
        b.setRewardParams(0.4e18, 0);
        vm.prank(TL);
        vm.expectRevert(BountyModule.BadParams.selector);
        b.setRewardParams(0.4e18, 0.6 ether);
    }

    /// Self-report (offender == reporter) never profits: R <= b_o.
    function testFuzz_selfReportNetNonPositive(uint96 bo, uint64 sigma, uint64 tau) public {
        sigma = uint64(bound(sigma, 1, 1e18 - 1));
        BountyModule m = new BountyModule(TL, VAULT, 1, type(uint64).max, sigma);
        vm.prank(TL);
        m.setRewardParams(sigma, tau);
        assertLe(m.rewardFor(bo), bo);
    }
}

contract HandoffTest is Test {
    CirculatoryEngine engine;
    GovernanceTimelock tl;
    HandoffEngineOwnership script;
    address owner = address(0x0E);
    address proposer = address(0x9);

    function setUp() public {
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
        tl = new GovernanceTimelock(14 days, 30 days, p, e, address(0));
        script = new HandoffEngineOwnership();
    }

    function test_handoffAndUpgradeNeeds30Days() public {
        script.handoff(engine, tl, address(0x5E), owner);
        assertEq(engine.owner(), address(tl));
        assertEq(engine.settler(), address(0x5E));

        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, owner));
        engine.pause();

        address newImpl = address(new CirculatoryEngine());
        bytes memory up = abi.encodeWithSignature("upgradeToAndCall(address,bytes)", newImpl, "");
        vm.startPrank(proposer);
        vm.expectRevert(abi.encodeWithSelector(GovernanceTimelock.DelayTooShort.selector, 14 days, 30 days));
        tl.schedule(address(engine), 0, up, 0, 0, 14 days);
        tl.schedule(address(engine), 0, up, 0, 0, 30 days);
        vm.stopPrank();
        vm.warp(block.timestamp + 30 days);
        tl.execute(address(engine), 0, up, 0, 0);
        bytes32 slot = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
        assertEq(address(uint160(uint256(vm.load(address(engine), slot)))), newImpl);
    }

    function test_handoffRejectsShortTimelock() public {
        address[] memory none = new address[](0);
        GovernanceTimelock shortTl = new GovernanceTimelock(1 days, 2 days, none, none, address(0));
        vm.expectRevert(abi.encodeWithSelector(HandoffEngineOwnership.TimelockDelayTooShort.selector, 1 days, 2 days));
        script.handoff(engine, shortTl, address(0), owner);
        assertEq(engine.owner(), owner);
    }

    function test_handoffRejectsNonOwner() public {
        vm.expectRevert(abi.encodeWithSelector(HandoffEngineOwnership.CallerNotOwner.selector, address(this), owner));
        script.handoff(engine, tl, address(0), address(this));
    }

    function test_handoffRejectsEOA() public {
        vm.expectRevert(HandoffEngineOwnership.TimelockHasNoCode.selector);
        script.handoff(engine, GovernanceTimelock(payable(address(0x1234))), address(0), owner);
    }
}
