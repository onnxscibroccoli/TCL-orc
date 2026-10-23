// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {Test, stdJson} from "forge-std/Test.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import {PausableUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";
import {CirculatoryEngine, IStateVerifier} from "../../contracts/CirculatoryEngine.sol";
import {StateVerifier} from "../../contracts/verifiers/StateVerifier.sol";

contract AcceptAllVerifier is IStateVerifier {
    function verifyProof(uint256[2] calldata, uint256[2][2] calldata, uint256[2] calldata, uint256[4] calldata)
        external
        pure
        returns (bool)
    {
        return true;
    }
}

contract Reenterer {
    CirculatoryEngine public engine;
    bool public attack;

    constructor(CirculatoryEngine e) {
        engine = e;
    }

    function fund() external payable {
        engine.deposit{value: msg.value}();
    }

    function steal(uint256 amount) external {
        attack = true;
        engine.withdraw(amount);
    }

    receive() external payable {
        if (attack) {
            attack = false;
            engine.withdraw(msg.value); // must hit ReentrancyGuard
        }
    }
}

abstract contract Base is Test {
    uint256 internal constant THRESHOLD = 10 ether;
    uint256 internal constant RATE = 1e12; // 1e-6 of surplus per second
    address internal owner = makeAddr("owner");
    address internal alice = makeAddr("alice");
    address internal bob = makeAddr("bob");
    CirculatoryEngine internal engine;

    function _deploy(IStateVerifier v, uint256 root, uint64 t0) internal returns (CirculatoryEngine) {
        CirculatoryEngine impl = new CirculatoryEngine();
        bytes memory init = abi.encodeCall(CirculatoryEngine.initialize, (owner, v, THRESHOLD, RATE, root, t0));
        return CirculatoryEngine(address(new ERC1967Proxy(address(impl), init)));
    }
}

contract CirculatoryEngineTest is Base {
    using stdJson for string;

    function setUp() public {
        vm.warp(1_000_000);
        engine = _deploy(new AcceptAllVerifier(), 1, 1);
        vm.deal(alice, 1_000 ether);
        vm.deal(bob, 1_000 ether);
    }

    function test_Deposit() public {
        vm.prank(alice);
        engine.deposit{value: 5 ether}();
        assertEq(engine.balanceOf(alice), 5 ether);
        assertEq(engine.totalBalances(), 5 ether);
        assertEq(address(engine).balance, 5 ether);
    }

    function test_DepositZeroReverts() public {
        vm.prank(alice);
        vm.expectRevert(CirculatoryEngine.ZeroAmount.selector);
        engine.deposit{value: 0}();
    }

    function test_SubThresholdNoDecay() public {
        vm.prank(alice);
        engine.deposit{value: THRESHOLD}();
        vm.warp(block.timestamp + 365 days);
        assertEq(engine.balanceOf(alice), THRESHOLD);
    }

    function test_LazyDecayAndSettle() public {
        vm.prank(alice);
        engine.deposit{value: 110 ether}(); // surplus 100 ether
        vm.warp(block.timestamp + 1000);
        uint256 expectedFee = (100 ether * RATE * 1000) / 1e18; // 0.1 ether
        assertEq(engine.pendingFee(alice), expectedFee);
        assertEq(engine.balanceOf(alice), 110 ether - expectedFee);
        (uint256 stored,) = engine.storedBalance(alice);
        assertEq(stored, 110 ether, "lazy: storage untouched until settle");
        engine.settle(alice);
        assertEq(engine.vault(), expectedFee);
        assertEq(engine.totalBalances() + engine.vault(), address(engine).balance);
    }

    function test_DecayEpochsCompose() public {
        vm.prank(alice);
        engine.deposit{value: 110 ether}();
        uint256 bal = 110 ether;
        for (uint256 i = 0; i < 5; ++i) {
            vm.warp(block.timestamp + 1 days);
            uint256 fee = ((bal - THRESHOLD) * RATE * 1 days) / 1e18;
            engine.settle(alice);
            bal -= fee;
            assertEq(engine.balanceOf(alice), bal);
        }
        assertEq(engine.totalBalances() + engine.vault(), address(engine).balance);
    }

    function test_DecayCappedAtSurplus() public {
        vm.prank(alice);
        engine.deposit{value: 20 ether}();
        vm.warp(block.timestamp + 10_000 days);
        assertEq(engine.balanceOf(alice), THRESHOLD);
    }

    function test_WithdrawAfterDecay() public {
        vm.startPrank(alice);
        engine.deposit{value: 110 ether}();
        vm.warp(block.timestamp + 1000);
        uint256 avail = engine.balanceOf(alice);
        vm.expectRevert(abi.encodeWithSelector(CirculatoryEngine.InsufficientBalance.selector, avail, 110 ether));
        engine.withdraw(110 ether);
        uint256 before = alice.balance;
        engine.withdraw(avail);
        vm.stopPrank();
        assertEq(alice.balance - before, avail);
        assertEq(address(engine).balance, engine.vault());
    }

    function test_RedistributeOnlyOwner() public {
        vm.prank(alice);
        engine.deposit{value: 110 ether}();
        vm.warp(block.timestamp + 1000);
        engine.settle(alice);
        uint256 v = engine.vault();
        vm.prank(bob);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, bob));
        engine.redistribute(bob, v);
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(CirculatoryEngine.InsufficientVault.selector, v, v + 1));
        engine.redistribute(bob, v + 1);
        vm.prank(owner);
        engine.redistribute(bob, v);
        assertEq(engine.balanceOf(bob), v);
        assertEq(engine.vault(), 0);
    }

    function test_AccessControl() public {
        CirculatoryEngine impl2 = new CirculatoryEngine();
        vm.startPrank(alice);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, alice));
        engine.pause();
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, alice));
        engine.setSettler(alice);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, alice));
        engine.upgradeToAndCall(address(impl2), "");
        uint256[2] memory a;
        uint256[2][2] memory b;
        uint256[4] memory p;
        vm.expectRevert(CirculatoryEngine.NotSettler.selector);
        engine.settleEpoch(a, b, a, p);
        vm.stopPrank();
    }

    function test_InitializeOnce() public {
        vm.expectRevert();
        engine.initialize(alice, IStateVerifier(address(1)), 0, 0, 0, 0);
    }

    function test_PauseBlocksActions() public {
        vm.prank(owner);
        engine.pause();
        vm.prank(alice);
        vm.expectRevert(PausableUpgradeable.EnforcedPause.selector);
        engine.deposit{value: 1 ether}();
        vm.prank(owner);
        engine.unpause();
        vm.prank(alice);
        engine.deposit{value: 1 ether}();
    }

    function test_UpgradeByOwnerPreservesState() public {
        vm.prank(alice);
        engine.deposit{value: 3 ether}();
        CirculatoryEngine impl2 = new CirculatoryEngine();
        vm.prank(owner);
        engine.upgradeToAndCall(address(impl2), "");
        assertEq(engine.balanceOf(alice), 3 ether);
    }

    function test_ReentrancyBlocked() public {
        Reenterer r = new Reenterer(engine);
        r.fund{value: 2 ether}();
        vm.prank(bob);
        engine.deposit{value: 5 ether}();
        vm.expectRevert(CirculatoryEngine.TransferFailed.selector);
        r.steal(1 ether);
        assertEq(engine.balanceOf(address(r)), 2 ether);
        assertEq(address(engine).balance, 7 ether);
    }

    function test_SettleEpochChecks() public {
        uint256[2] memory a;
        uint256[2][2] memory b;
        uint256 field = engine.SNARK_FIELD();
        vm.startPrank(owner);
        vm.expectRevert(abi.encodeWithSelector(CirculatoryEngine.StaleRoot.selector, 1, 2));
        engine.settleEpoch(a, b, a, [uint256(5), 0, 2, block.timestamp]);
        vm.expectRevert(abi.encodeWithSelector(CirculatoryEngine.InvalidEpochTime.selector, 1, block.timestamp + 1));
        engine.settleEpoch(a, b, a, [uint256(5), 0, 1, block.timestamp + 1]);
        vm.expectRevert(abi.encodeWithSelector(CirculatoryEngine.SignalOutOfField.selector, 0));
        engine.settleEpoch(a, b, a, [field, 0, 1, block.timestamp]);
        engine.settleEpoch(a, b, a, [uint256(5), 7, 1, block.timestamp]);
        vm.stopPrank();
        assertEq(engine.stateRoot(), 5);
        assertEq(engine.epoch(), 1);
        assertEq(engine.offchainFeesSettled(), 7);
    }
}

/// Uses the real snarkjs verifier and a real proof produced by
/// scripts/gen_zk_fixture.py from a ledger settle (src/tcl/zk/prover.py).
contract CirculatoryEngineProofTest is Base {
    using stdJson for string;

    string internal fx;
    uint256[2] internal pA;
    uint256[2][2] internal pB;
    uint256[2] internal pC;
    uint256[4] internal pub;

    event StateSettled(uint64 indexed epoch, uint256 oldRoot, uint256 newRoot, uint256 fee, uint64 newTime);

    function setUp() public {
        fx = vm.readFile("test/contracts/fixtures/state_transition.json");
        uint256[] memory a = fx.readUintArray(".calldata.a");
        uint256[] memory b0 = fx.readUintArray(".calldata.b[0]");
        uint256[] memory b1 = fx.readUintArray(".calldata.b[1]");
        uint256[] memory c = fx.readUintArray(".calldata.c");
        uint256[] memory s = fx.readUintArray(".calldata.pubSignals");
        pA = [a[0], a[1]];
        pB = [[b0[0], b0[1]], [b1[0], b1[1]]];
        pC = [c[0], c[1]];
        pub = [s[0], s[1], s[2], s[3]];
        uint64 t0 = uint64(fx.readUint(".scenario.t0"));
        vm.warp(pub[3] + 1);
        engine = _deploy(IStateVerifier(address(new StateVerifier())), pub[2], t0);
    }

    function test_RealVerifierAcceptsFixture() public {
        assertTrue(new StateVerifier().verifyProof(pA, pB, pC, pub));
    }

    function test_SettleEpochWithRealProofEmitsStateSettled() public {
        vm.expectEmit(true, false, false, true, address(engine));
        emit StateSettled(1, pub[2], pub[0], pub[1], uint64(pub[3]));
        vm.prank(owner);
        engine.settleEpoch(pA, pB, pC, pub);
        assertEq(engine.stateRoot(), pub[0]);
        assertEq(engine.offchainFeesSettled(), pub[1]);
        assertEq(engine.lastSettledTime(), pub[3]);
    }

    function test_ReplayRejected() public {
        vm.startPrank(owner);
        engine.settleEpoch(pA, pB, pC, pub);
        vm.expectRevert(abi.encodeWithSelector(CirculatoryEngine.StaleRoot.selector, pub[0], pub[2]));
        engine.settleEpoch(pA, pB, pC, pub);
        vm.stopPrank();
    }

    function test_TamperedFeeRejected() public {
        uint256[4] memory bad = pub;
        bad[1] += 1;
        vm.prank(owner);
        vm.expectRevert(CirculatoryEngine.InvalidProof.selector);
        engine.settleEpoch(pA, pB, pC, bad);
    }

    function test_TamperedProofRejected() public {
        uint256[2] memory badA = [pA[0], pA[1] + 1];
        vm.prank(owner);
        vm.expectRevert(CirculatoryEngine.InvalidProof.selector);
        engine.settleEpoch(badA, pB, pC, pub);
    }
}

contract Handler is Test {
    CirculatoryEngine internal engine;
    address internal owner;
    address[] internal actors;

    constructor(CirculatoryEngine e, address o) {
        engine = e;
        owner = o;
        for (uint256 i = 0; i < 4; ++i) {
            address a = makeAddr(string(abi.encodePacked("actor", vm.toString(i))));
            actors.push(a);
            vm.deal(a, 1e30);
        }
    }

    function actorList() external view returns (address[] memory) {
        return actors;
    }

    function deposit(uint256 who, uint256 amount) external {
        amount = bound(amount, 1, 1e24);
        vm.prank(actors[who % 4]);
        engine.deposit{value: amount}();
    }

    function withdraw(uint256 who, uint256 amount) external {
        address a = actors[who % 4];
        uint256 bal = engine.balanceOf(a);
        if (bal == 0) return;
        vm.prank(a);
        engine.withdraw(bound(amount, 1, bal));
    }

    function warp(uint256 dt) external {
        vm.warp(block.timestamp + bound(dt, 1, 30 days));
    }

    function settle(uint256 who) external {
        engine.settle(actors[who % 4]);
    }

    function redistribute(uint256 who, uint256 amount) external {
        uint256 v = engine.vault();
        if (v == 0) return;
        vm.prank(owner);
        engine.redistribute(actors[who % 4], bound(amount, 1, v));
    }
}

contract CirculatoryEngineInvariantTest is Base {
    Handler internal handler;

    function setUp() public {
        vm.warp(1_000_000);
        engine = _deploy(new AcceptAllVerifier(), 1, 1);
        handler = new Handler(engine, owner);
        targetContract(address(handler));
    }

    /// ETH held == settled balances + vault, across any sequence of decay epochs.
    function invariant_Conservation() public view {
        assertEq(address(engine).balance, engine.totalBalances() + engine.vault());
    }

    /// Sum of stored balances equals totalBalances; lazy balances + pending fees match too.
    function invariant_LazyConservation() public view {
        address[] memory a = handler.actorList();
        uint256 stored;
        uint256 lazy;
        uint256 pending;
        for (uint256 i = 0; i < a.length; ++i) {
            (uint256 s,) = engine.storedBalance(a[i]);
            stored += s;
            lazy += engine.balanceOf(a[i]);
            pending += engine.pendingFee(a[i]);
        }
        assertEq(stored, engine.totalBalances());
        assertEq(lazy + pending, stored);
    }
}

/// Chained real proofs (scripts/gen_zk_fixture.py CHAIN): consecutive settles on one
/// ledger; each proof's newRoot is the full post-settle root (account + vault leaf).
contract CirculatoryEngineChainTest is Base {
    using stdJson for string;

    string internal fx;
    uint256 internal n;

    function _step(uint256 i)
        internal
        view
        returns (uint256[2] memory pA, uint256[2][2] memory pB, uint256[2] memory pC, uint256[4] memory pub)
    {
        string memory k = string.concat(".steps[", vm.toString(i), "].calldata");
        uint256[] memory a = fx.readUintArray(string.concat(k, ".a"));
        uint256[] memory b0 = fx.readUintArray(string.concat(k, ".b[0]"));
        uint256[] memory b1 = fx.readUintArray(string.concat(k, ".b[1]"));
        uint256[] memory c = fx.readUintArray(string.concat(k, ".c"));
        uint256[] memory s = fx.readUintArray(string.concat(k, ".pubSignals"));
        pA = [a[0], a[1]];
        pB = [[b0[0], b0[1]], [b1[0], b1[1]]];
        pC = [c[0], c[1]];
        pub = [s[0], s[1], s[2], s[3]];
    }

    function setUp() public {
        fx = vm.readFile("test/contracts/fixtures/state_transition_chain.json");
        n = fx.readUint(".count");
        (,,, uint256[4] memory first) = _step(0);
        (,,, uint256[4] memory last) = _step(n - 1);
        vm.warp(last[3] + 1);
        engine = _deploy(IStateVerifier(address(new StateVerifier())), first[2], uint64(fx.readUint(".t0")));
    }

    function test_ConsecutiveSettlementsTrackLedgerRoot() public {
        assertGe(n, 3);
        uint256 total;
        vm.startPrank(owner);
        for (uint256 i = 0; i < n; ++i) {
            (uint256[2] memory pA, uint256[2][2] memory pB, uint256[2] memory pC, uint256[4] memory pub) = _step(i);
            assertEq(engine.stateRoot(), pub[2]);
            engine.settleEpoch(pA, pB, pC, pub);
            uint256 ledgerRoot = fx.readUint(string.concat(".steps[", vm.toString(i), "].ledgerRoot"));
            assertEq(engine.stateRoot(), ledgerRoot);
            assertEq(engine.epoch(), i + 1);
            total += pub[1];
            assertEq(engine.offchainFeesSettled(), total);
            assertEq(total, fx.readUint(string.concat(".steps[", vm.toString(i), "].vault")));
        }
        vm.stopPrank();
    }

    function test_OutOfOrderStepRejected() public {
        (uint256[2] memory pA, uint256[2][2] memory pB, uint256[2] memory pC, uint256[4] memory pub) = _step(1);
        uint256 root = engine.stateRoot();
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(CirculatoryEngine.StaleRoot.selector, root, pub[2]));
        engine.settleEpoch(pA, pB, pC, pub);
    }
}
