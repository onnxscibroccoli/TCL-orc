// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import {PausableUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";
import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {CirculatoryEngine, IStateVerifier} from "../../contracts/CirculatoryEngine.sol";
import {AcceptAllVerifier, Base} from "./CirculatoryEngine.t.sol";

contract RejectAllVerifier is IStateVerifier {
    function verifyProof(uint256[2] calldata, uint256[2][2] calldata, uint256[2] calldata, uint256[4] calldata)
        external
        pure
        returns (bool)
    {
        return false;
    }
}

/// Attacker that re-enters every mutating entry point from its receive hook.
contract MultiReenterer {
    CirculatoryEngine public engine;
    uint8 public mode;
    bool public reentered;

    constructor(CirculatoryEngine e) {
        engine = e;
    }

    function fund() external payable {
        engine.deposit{value: msg.value}();
    }

    function go(uint8 m, uint256 amount) external {
        mode = m;
        engine.withdraw(amount);
    }

    receive() external payable {
        if (mode == 1) {
            mode = 0;
            engine.deposit{value: msg.value}();
        } else if (mode == 2) {
            mode = 0;
            engine.withdraw(1);
        } else if (mode == 3) {
            mode = 0;
            engine.settle(address(this)); // not nonReentrant: must be harmless
            reentered = true;
        }
    }
}

contract NotUUPS {
    uint256 public x;
}

contract SecurityTest is Base {
    uint256[2] internal a;
    uint256[2][2] internal b;
    uint256[2] internal c;

    function setUp() public {
        vm.warp(1_000_000);
        engine = _deploy(new AcceptAllVerifier(), 1, 1);
        vm.deal(alice, 1_000 ether);
        vm.deal(bob, 1_000 ether);
    }

    // ------------------------------------------------------------ upgrades
    function test_UpgradeByNonOwnerReverts() public {
        CirculatoryEngine impl2 = new CirculatoryEngine();
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, alice));
        engine.upgradeToAndCall(address(impl2), "");
    }

    function test_UpgradeToNonUUPSReverts() public {
        NotUUPS bad = new NotUUPS();
        vm.prank(owner);
        vm.expectRevert();
        engine.upgradeToAndCall(address(bad), "");
    }

    function test_ImplementationCannotBeInitialized() public {
        CirculatoryEngine impl = new CirculatoryEngine();
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        impl.initialize(alice, IStateVerifier(address(1)), 0, 0, 0, 0);
    }

    function test_ImplementationUpgradeDirectReverts() public {
        CirculatoryEngine impl = new CirculatoryEngine();
        CirculatoryEngine impl2 = new CirculatoryEngine();
        vm.expectRevert(UUPSUpgradeable.UUPSUnauthorizedCallContext.selector);
        impl.upgradeToAndCall(address(impl2), "");
    }

    function test_UpgradePreservesAllState() public {
        vm.prank(alice);
        engine.deposit{value: 30 ether}();
        vm.warp(block.timestamp + 1 days);
        engine.settle(alice);
        uint256 tb = engine.totalBalances();
        uint256 v = engine.vault();
        uint256 root = engine.stateRoot();
        vm.prank(owner);
        engine.setSettler(bob);
        CirculatoryEngine impl2 = new CirculatoryEngine();
        vm.prank(owner);
        engine.upgradeToAndCall(address(impl2), "");
        assertEq(engine.totalBalances(), tb);
        assertEq(engine.vault(), v);
        assertEq(engine.stateRoot(), root);
        assertEq(engine.settler(), bob);
        assertEq(engine.owner(), owner);
        assertEq(engine.threshold(), THRESHOLD);
        assertEq(engine.ratePerSecond(), RATE);
    }

    function test_InitializeRejectsBadParams() public {
        CirculatoryEngine impl = new CirculatoryEngine();
        uint256 field = engine.SNARK_FIELD();
        vm.expectRevert(CirculatoryEngine.ZeroAddress.selector);
        new ERC1967Proxy(
            address(impl), abi.encodeCall(CirculatoryEngine.initialize, (owner, IStateVerifier(address(0)), 0, 0, 0, 0))
        );
        vm.expectRevert(CirculatoryEngine.RateTooHigh.selector);
        new ERC1967Proxy(
            address(impl),
            abi.encodeCall(CirculatoryEngine.initialize, (owner, IStateVerifier(address(1)), 0, 1e18 + 1, 0, 0))
        );
        vm.expectRevert(abi.encodeWithSelector(CirculatoryEngine.SignalOutOfField.selector, 2));
        new ERC1967Proxy(
            address(impl),
            abi.encodeCall(CirculatoryEngine.initialize, (owner, IStateVerifier(address(1)), 0, 0, field, 0))
        );
        vm.expectRevert(CirculatoryEngine.ZeroAddress.selector);
        new ERC1967Proxy(
            address(impl),
            abi.encodeCall(CirculatoryEngine.initialize, (address(0), IStateVerifier(address(1)), 0, 0, 0, 0))
        );
    }

    // --------------------------------------------------------------- pause
    function test_PauseBlocksEveryUserPath() public {
        vm.prank(alice);
        engine.deposit{value: 20 ether}();
        vm.prank(owner);
        engine.pause();
        vm.startPrank(alice);
        vm.expectRevert(PausableUpgradeable.EnforcedPause.selector);
        engine.withdraw(1 ether);
        vm.expectRevert(PausableUpgradeable.EnforcedPause.selector);
        engine.settle(alice);
        vm.expectRevert(PausableUpgradeable.EnforcedPause.selector);
        engine.deposit{value: 1 ether}();
        vm.stopPrank();
        vm.prank(owner);
        vm.expectRevert(PausableUpgradeable.EnforcedPause.selector);
        engine.settleEpoch(a, b, c, [uint256(2), 0, 1, block.timestamp]);
    }

    function test_PauseUnpauseOnlyOwner() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, alice));
        engine.pause();
        vm.prank(owner);
        engine.pause();
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, alice));
        engine.unpause();
    }

    function test_PausedDecayStillAccruesButFundsIntact() public {
        vm.prank(alice);
        engine.deposit{value: 20 ether}();
        vm.prank(owner);
        engine.pause();
        vm.warp(block.timestamp + 1 days);
        vm.prank(owner);
        engine.unpause();
        uint256 bal = engine.balanceOf(alice);
        vm.prank(alice);
        engine.withdraw(bal);
        assertEq(address(engine).balance, engine.vault());
        assertEq(engine.totalBalances(), 0);
    }

    // ---------------------------------------------------------- reentrancy
    function test_ReenterDepositFromWithdrawBlocked() public {
        MultiReenterer r = new MultiReenterer(engine);
        r.fund{value: 2 ether}();
        vm.expectRevert(CirculatoryEngine.TransferFailed.selector);
        r.go(1, 1 ether);
        assertEq(engine.balanceOf(address(r)), 2 ether);
    }

    function test_ReenterWithdrawBlocked() public {
        MultiReenterer r = new MultiReenterer(engine);
        r.fund{value: 2 ether}();
        vm.expectRevert(CirculatoryEngine.TransferFailed.selector);
        r.go(2, 1 ether);
        assertEq(address(engine).balance, 2 ether);
    }

    function test_ReenterSettleHarmless() public {
        MultiReenterer r = new MultiReenterer(engine);
        r.fund{value: 2 ether}();
        r.go(3, 1 ether);
        assertTrue(r.reentered());
        assertEq(engine.balanceOf(address(r)), 1 ether);
        assertEq(address(engine).balance, engine.totalBalances() + engine.vault());
    }

    // ---------------------------------------------------------- settlement
    function test_RejectingVerifierFailsClosed() public {
        CirculatoryEngine e = _deploy(new RejectAllVerifier(), 1, 1);
        vm.prank(owner);
        vm.expectRevert(CirculatoryEngine.InvalidProof.selector);
        e.settleEpoch(a, b, c, [uint256(2), 0, 1, block.timestamp]);
        assertEq(e.stateRoot(), 1);
        assertEq(e.epoch(), 0);
    }

    function test_SettleEpochNonSettlerReverts() public {
        vm.prank(alice);
        vm.expectRevert(CirculatoryEngine.NotSettler.selector);
        engine.settleEpoch(a, b, c, [uint256(2), 0, 1, block.timestamp]);
    }

    function testFuzz_SettleEpochOutOfFieldRejected(uint8 idx, uint256 extra) public {
        uint256 i = idx % 4;
        uint256[4] memory s = [uint256(2), 0, 1, block.timestamp];
        s[i] = engine.SNARK_FIELD() + bound(extra, 0, type(uint256).max - engine.SNARK_FIELD());
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(CirculatoryEngine.SignalOutOfField.selector, i));
        engine.settleEpoch(a, b, c, s);
    }

    // ------------------------------------------------------ fuzzed accounting
    function testFuzz_DecayNeverExceedsSurplus(uint96 amt, uint32 dt) public {
        uint256 amount = bound(amt, 1, 1e27);
        vm.deal(alice, amount);
        vm.prank(alice);
        engine.deposit{value: amount}();
        vm.warp(block.timestamp + dt);
        uint256 fee = engine.pendingFee(alice);
        if (amount <= THRESHOLD) assertEq(fee, 0);
        else assertLe(fee, amount - THRESHOLD);
        assertGe(engine.balanceOf(alice), amount > THRESHOLD ? THRESHOLD : amount);
    }

    function testFuzz_RedistributeConserves(uint96 amt, uint32 dt, uint96 take) public {
        uint256 amount = bound(amt, THRESHOLD + 1, 1e27);
        vm.deal(alice, amount);
        vm.prank(alice);
        engine.deposit{value: amount}();
        vm.warp(block.timestamp + bound(dt, 1, 365 days));
        engine.settle(alice);
        uint256 v = engine.vault();
        vm.assume(v > 0);
        vm.prank(owner);
        engine.redistribute(bob, bound(take, 1, v));
        assertEq(address(engine).balance, engine.totalBalances() + engine.vault());
    }

    function test_RedistributeOverVaultReverts() public {
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(CirculatoryEngine.InsufficientVault.selector, 0, 1));
        engine.redistribute(bob, 1);
    }
}

/// Handler covering deposit / withdraw / decay / settle / redistribute / settleEpoch.
contract FullHandler is Test {
    CirculatoryEngine internal engine;
    address internal owner;
    address[] internal actors;
    uint256 public ghostDeposited;
    uint256 public ghostWithdrawn;
    uint256 public ghostFeesSeen;
    uint256 public ghostRedistributed;
    uint256 public lastRoot = 1;

    constructor(CirculatoryEngine e, address o) {
        engine = e;
        owner = o;
        for (uint256 i = 0; i < 5; ++i) {
            address x = makeAddr(string(abi.encodePacked("full", vm.toString(i))));
            actors.push(x);
            vm.deal(x, 1e30);
        }
    }

    function actorList() external view returns (address[] memory) {
        return actors;
    }

    function deposit(uint256 who, uint256 amount) external {
        amount = bound(amount, 1, 1e24);
        vm.prank(actors[who % 5]);
        engine.deposit{value: amount}();
        ghostDeposited += amount;
    }

    function withdraw(uint256 who, uint256 amount) external {
        address x = actors[who % 5];
        uint256 bal = engine.balanceOf(x);
        if (bal == 0) return;
        amount = bound(amount, 1, bal);
        vm.prank(x);
        engine.withdraw(amount);
        ghostWithdrawn += amount;
    }

    function warp(uint256 dt) external {
        vm.warp(block.timestamp + bound(dt, 1, 30 days));
    }

    function settle(uint256 who) external {
        engine.settle(actors[who % 5]);
    }

    function redistribute(uint256 who, uint256 amount) external {
        uint256 v = engine.vault();
        if (v == 0) return;
        amount = bound(amount, 1, v);
        vm.prank(owner);
        engine.redistribute(actors[who % 5], amount);
        ghostRedistributed += amount;
    }

    function settleEpoch(uint256 newRoot, uint256 fee) external {
        newRoot = bound(newRoot, 0, engine.SNARK_FIELD() - 1);
        fee = bound(fee, 0, 1e24);
        uint256 t = block.timestamp;
        if (t <= engine.lastSettledTime()) return;
        uint256[2] memory a;
        uint256[2][2] memory b;
        uint256[2] memory c;
        vm.prank(owner);
        engine.settleEpoch(a, b, c, [newRoot, fee, engine.stateRoot(), t]);
        lastRoot = newRoot;
    }

    function pauseToggle() external {
        vm.startPrank(owner);
        if (engine.paused()) engine.unpause();
        else engine.pause();
        vm.stopPrank();
    }
}

contract FullInvariantTest is Base {
    FullHandler internal h;

    function setUp() public {
        vm.warp(1_000_000);
        engine = _deploy(new AcceptAllVerifier(), 1, 1);
        h = new FullHandler(engine, owner);
        targetContract(address(h));
    }

    /// forge-config: default.invariant.runs = 512
    /// forge-config: default.invariant.depth = 128
    function invariant_EthConservation() public view {
        assertEq(address(engine).balance, engine.totalBalances() + engine.vault());
    }

    /// forge-config: default.invariant.runs = 512
    /// forge-config: default.invariant.depth = 128
    function invariant_GhostConservation() public view {
        // Everything deposited is either withdrawn or still held (balances + vault).
        assertEq(h.ghostDeposited(), h.ghostWithdrawn() + engine.totalBalances() + engine.vault());
    }

    /// forge-config: default.invariant.runs = 512
    /// forge-config: default.invariant.depth = 128
    function invariant_SumOfBalances() public view {
        address[] memory xs = h.actorList();
        uint256 stored;
        for (uint256 i = 0; i < xs.length; ++i) {
            (uint256 s,) = engine.storedBalance(xs[i]);
            stored += s;
            assertLe(engine.pendingFee(xs[i]), s);
        }
        assertEq(stored, engine.totalBalances());
    }

    /// forge-config: default.invariant.runs = 512
    /// forge-config: default.invariant.depth = 128
    function invariant_RootTracksLastSettle() public view {
        assertEq(engine.stateRoot(), h.lastRoot());
        assertLe(engine.lastSettledTime(), block.timestamp);
    }
}
