// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {stdJson} from "forge-std/StdJson.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {ParameterRegistry} from "../../contracts/governance/ParameterRegistry.sol";
import {NoteController, INoteDepositVerifier, IParamRegistry} from "../../contracts/physical/NoteController.sol";
import {NoteDepositVerifier} from "../../contracts/verifiers/NoteDepositVerifier.sol";

contract MockTCL is ERC20 {
    constructor() ERC20("TCL", "TCL") {}

    function mintTo(address to, uint256 a) external {
        _mint(to, a);
    }
}

contract MockVerifier is INoteDepositVerifier {
    bool public ok = true;

    function set(bool v) external {
        ok = v;
    }

    function verifyProof(uint256[2] calldata, uint256[2][2] calldata, uint256[2] calldata, uint256[6] calldata)
        external
        view
        returns (bool)
    {
        return ok;
    }
}

abstract contract Base is Test {
    uint256 constant UNIT = 1e18;
    MockTCL tcl;
    NoteController nc;
    address minter = makeAddr("minter");

    function _deploy(address verifier, address at) internal {
        tcl = new MockTCL();
        NoteController impl = new NoteController();
        bytes memory init = abi.encodeCall(
            NoteController.initialize, (address(this), IERC20(address(tcl)), INoteDepositVerifier(verifier), UNIT)
        );
        if (at == address(0)) {
            nc = NoteController(address(new ERC1967Proxy(address(impl), init)));
        } else {
            deployCodeTo("ERC1967Proxy.sol:ERC1967Proxy", abi.encode(address(impl), init), at);
            nc = NoteController(at);
        }
        tcl.mintTo(address(this), 1e30);
        tcl.approve(address(nc), type(uint256).max);
        nc.fundPool(1e24);
        tcl.mintTo(minter, 1e27);
        vm.prank(minter);
        tcl.approve(address(nc), type(uint256).max);
    }

    function _conserved() internal view {
        assertEq(tcl.balanceOf(address(nc)), nc.physicalOutstanding() + nc.pool() + nc.lockedTotal());
    }
}

/// Real Groth16 proofs (dev zkey) against the generated verifier.
contract NoteControllerProofTest is Base {
    using stdJson for string;

    string fx;
    uint256 id;
    address bob;

    function setUp() public {
        fx = vm.readFile("test/contracts/fixtures/note_deposit.json");
        address at = fx.readAddress(".controller");
        _deploy(address(new NoteDepositVerifier()), at);
        id = fx.readUint(".id");
        bob = fx.readAddress(".recipient");
        vm.prank(minter);
        nc.mint(id, 100, 0);
    }

    function _p(string memory k)
        internal
        view
        returns (uint256 nf, uint256[2] memory a, uint256[2][2] memory b, uint256[2] memory c)
    {
        uint256[] memory pub = fx.readUintArray(string.concat(".", k, ".pubSignals"));
        nf = pub[5];
        uint256[] memory aa = fx.readUintArray(string.concat(".", k, ".a"));
        uint256[] memory b0 = fx.readUintArray(string.concat(".", k, ".b[0]"));
        uint256[] memory b1 = fx.readUintArray(string.concat(".", k, ".b[1]"));
        uint256[] memory cc = fx.readUintArray(string.concat(".", k, ".c"));
        a = [aa[0], aa[1]];
        b = [[b0[0], b0[1]], [b1[0], b1[1]]];
        c = [cc[0], cc[1]];
    }

    function _dep(string memory k) internal {
        (uint256 nf, uint256[2] memory a, uint256[2][2] memory b, uint256[2] memory c) = _p(k);
        nc.deposit(id, nf, bob, a, b, c);
    }

    function test_DepositThenReplayRejected() public {
        _dep("era0");
        assertEq(tcl.balanceOf(bob), 100 * UNIT);
        (,,, NoteController.State s) = nc.notes(id);
        assertEq(uint8(s), uint8(NoteController.State.DORMANT));
        vm.expectRevert(NoteController.BadState.selector);
        _dep("era0");
        _conserved();
    }

    function test_RearmAtKPlus1AndDepositAgain() public {
        _dep("era0");
        vm.prank(minter);
        nc.rearm(id, 0);
        (, uint64 era,, NoteController.State s) = nc.notes(id);
        assertEq(era, 1);
        assertEq(uint8(s), uint8(NoteController.State.LIVE));
        // stale era-0 proof is rejected (nullifier already used)
        vm.expectRevert(NoteController.NullifierUsed.selector);
        _dep("era0");
        _dep("era1");
        assertEq(tcl.balanceOf(bob), 200 * UNIT);
        _conserved();
    }

    function test_Era1ProofRejectedAtEra0() public {
        vm.expectRevert(NoteController.InvalidProof.selector);
        _dep("era1");
    }

    function test_WrongContextRejected() public {
        vm.expectRevert(NoteController.InvalidProof.selector);
        _dep("wrongCtx");
    }

    function test_RecipientBound() public {
        (uint256 nf, uint256[2] memory a, uint256[2][2] memory b, uint256[2] memory c) = _p("era0");
        vm.expectRevert(NoteController.InvalidProof.selector);
        nc.deposit(id, nf, makeAddr("thief"), a, b, c);
    }

    function test_TamperedProofRejected() public {
        (uint256 nf, uint256[2] memory a, uint256[2][2] memory b, uint256[2] memory c) = _p("era0");
        a[0] ^= 1;
        vm.expectRevert(NoteController.InvalidProof.selector);
        nc.deposit(id, nf, bob, a, b, c);
    }
}

contract NoteControllerUnitTest is Base {
    MockVerifier mv;
    uint256[2] a;
    uint256[2][2] b;
    uint256[2] c;

    function setUp() public {
        mv = new MockVerifier();
        _deploy(address(mv), address(0));
    }

    function _mint(uint256 id, uint32 d) internal returns (uint256 cost, uint256 reward) {
        vm.prank(minter);
        return nc.mint(id, d, type(uint256).max);
    }

    function test_DoubleMintRejected() public {
        _mint(1, 100);
        vm.expectRevert(NoteController.BadState.selector);
        _mint(1, 100);
    }

    function test_DoubleDepositSameNullifierRejected() public {
        _mint(1, 100);
        _mint(2, 100);
        nc.deposit(1, 77, address(0xB0B), a, b, c);
        vm.expectRevert(NoteController.NullifierUsed.selector);
        nc.deposit(2, 77, address(0xB0B), a, b, c);
    }

    function test_RearmRequiresDormant() public {
        _mint(1, 100);
        vm.expectRevert(NoteController.BadState.selector);
        vm.prank(minter);
        nc.rearm(1, 0);
        vm.expectRevert(NoteController.BadState.selector);
        nc.rearm(99, 0);
    }

    function test_FailClosedVerifier() public {
        _mint(1, 100);
        mv.set(false);
        vm.expectRevert(NoteController.InvalidProof.selector);
        nc.deposit(1, 5, address(0xB0B), a, b, c);
    }

    function test_Denominations() public {
        uint32[7] memory ok = [uint32(5), 10, 20, 50, 100, 200, 500];
        for (uint256 i; i < 7; i++) {
            assertTrue(nc.denomAllowed(ok[i]));
        }
        vm.expectRevert(NoteController.BadDenomination.selector);
        _mint(1, 7);
        vm.expectRevert(NoteController.BadDenomination.selector);
        _mint(1, 1000);
        vm.expectRevert(NoteController.BadDenomination.selector);
        nc.setDenomination(501, true, 0);
        vm.expectRevert(NoteController.BadDenomination.selector);
        nc.setDenomination(0, true, 0);
        nc.setDenomination(100, false, 0);
        vm.expectRevert(NoteController.BadDenomination.selector);
        _mint(1, 100);
    }

    function test_RewardAndCostBounds() public {
        (uint256 cost, uint256 reward) = _mint(1, 500);
        assertEq(reward, (500 * UNIT * 100) / 10_000); // 1%
        assertEq(cost, 3 * UNIT); // c_abs binds for large notes
        vm.expectRevert(NoteController.OutOfBounds.selector);
        nc.setParams(101, 1 days, 1, 1, 1 days, 1 days);
        vm.expectRevert(NoteController.OutOfBounds.selector);
        nc.setDenomination(100, true, 50 * UNIT + 1); // > 50% of V
    }

    function test_EpochBudgetFallsBackToOneToOne() public {
        nc.setCostPolicy(4000, 3 * UNIT, UNIT / 4, 0, 7 days); // no small-note reserve here
        nc.setParams(100, 1 days, 8 * UNIT, 5_000_000 * UNIT, 30 days, 30 days);
        (uint256 c1, uint256 r1) = _mint(1, 500); // 3 + 5 = 8 fits
        assertGt(c1 + r1, 0);
        (uint256 c2, uint256 r2) = _mint(2, 500);
        assertEq(c2 + r2, 0);
        vm.warp(block.timestamp + 1 days);
        (uint256 c3, uint256 r3) = _mint(3, 500);
        assertGt(c3 + r3, 0);
        _conserved();
    }

    function test_EmptyPoolOneToOne() public {
        // drain: new controller without pool
        NoteController impl = new NoteController();
        NoteController n2 = NoteController(
            address(
                new ERC1967Proxy(
                    address(impl),
                    abi.encodeCall(
                        NoteController.initialize,
                        (address(this), IERC20(address(tcl)), INoteDepositVerifier(address(mv)), UNIT)
                    )
                )
            )
        );
        tcl.approve(address(n2), type(uint256).max);
        (uint256 cc, uint256 rr) = n2.mint(1, 100, 1e30);
        assertEq(cc + rr, 0);
    }

    function test_ReentryGradualUnlock() public {
        nc.setParams(100, 1 days, 1e30, 1_000 * UNIT, 30 days, 30 days);
        address bob = address(0xB0B);
        for (uint256 i = 1; i <= 15; i++) {
            _mint(i, 100);
        }
        for (uint256 i = 1; i <= 15; i++) {
            nc.deposit(i, 1000 + i, bob, a, b, c);
        }
        assertEq(tcl.balanceOf(bob), 1_000 * UNIT); // Theta immediately
        assertEq(nc.locked(bob), 500 * UNIT);
        vm.warp(block.timestamp + 15 days);
        assertEq(nc.unlocked(bob), 250 * UNIT); // U = Theta + E * 1/2
        vm.prank(bob);
        nc.release();
        assertEq(tcl.balanceOf(bob), 1_250 * UNIT);
        vm.warp(block.timestamp + 30 days);
        vm.prank(bob);
        nc.release();
        assertEq(tcl.balanceOf(bob), 1_500 * UNIT);
        assertEq(nc.lockedTotal(), 0);
        _conserved();
    }

    function test_PauseBlocks() public {
        nc.pause();
        vm.expectRevert();
        _mint(1, 100);
    }

    function test_OnlyRolesCanSetParams() public {
        vm.prank(minter);
        vm.expectRevert();
        nc.setParams(0, 1 days, 0, 1, 1 days, 1 days);
        address impl2 = address(new NoteController());
        vm.prank(minter);
        vm.expectRevert();
        nc.upgradeToAndCall(impl2, "");
    }

    function test_ImplementationLocked() public {
        NoteController impl = new NoteController();
        vm.expectRevert();
        impl.initialize(address(this), IERC20(address(tcl)), INoteDepositVerifier(address(mv)), UNIT);
    }

    function testFuzz_MintDepositConservation(uint8 n, uint256 seed) public {
        nc.setParams(100, 1 days, 1e30, 300 * UNIT, 30 days, 30 days);
        uint32[7] memory ds = [uint32(5), 10, 20, 50, 100, 200, 500];
        uint256 supply = tcl.totalSupply();
        for (uint256 i = 1; i <= uint256(n) % 40 + 1; i++) {
            uint32 d = ds[uint256(keccak256(abi.encode(seed, i))) % 7];
            _mint(i, d);
            if (uint256(keccak256(abi.encode(seed, i, 1))) % 2 == 0) {
                nc.deposit(i, i, address(uint160(0xB0B + (i % 3))), a, b, c);
                vm.warp(block.timestamp + (seed % 5 days));
            }
            _conserved();
        }
        assertEq(tcl.totalSupply(), supply); // no issuance
    }
}

/// docs/11 §7.2 cost-credit policy, read from the real ParameterRegistry defaults.
contract NoteControllerCostPolicyTest is Base {
    MockVerifier mv;
    ParameterRegistry reg;
    uint256[2] a;
    uint256[2][2] b;
    uint256[2] c;

    function setUp() public {
        mv = new MockVerifier();
        _deploy(address(mv), address(0));
        reg = new ParameterRegistry(address(this), 7 days);
        nc.setRegistry(IParamRegistry(address(reg)));
        nc.setParams(100, 1 days, 1e30, 5_000_000 * UNIT, 30 days, 30 days);
    }

    function _mint(uint256 id, uint32 d, uint256 attested) internal returns (uint256 cost, uint256 reward) {
        vm.prank(minter);
        return nc.mint(id, d, attested);
    }

    /// Spec table (docs/11 §7.2): gamma 40%, c_abs 3, rho 0.2%, b_small 0.25 on 5/10/20.
    function test_DefaultScheduleAllDenominations() public {
        uint32[7] memory ds = [uint32(5), 10, 20, 50, 100, 200, 500];
        uint256[7] memory wantC = [uint256(2e18), 3e18, 3e18, 3e18, 3e18, 3e18, 3e18];
        uint256[7] memory wantR = [uint256(0.26e18), 0.27e18, 0.29e18, 0.1e18, 0.2e18, 0.4e18, 1e18];
        for (uint256 i; i < 7; i++) {
            (uint256 cc, uint256 rr) = _mint(i + 1, ds[i], type(uint256).max);
            assertEq(cc, wantC[i], "cost");
            assertEq(rr, wantR[i], "reward");
            assertLe(cc + rr, (uint256(ds[i]) * UNIT * 6000) / 10_000);
        }
        _conserved();
    }

    function test_HonestCostIsPaidNotCap() public {
        (uint256 cc, uint256 rr) = _mint(1, 5, 1.5e18);
        assertEq(cc, 1.5e18);
        assertEq(rr, 0.26e18); // minter profit = reward when real cost <= cap
    }

    function test_RearmNoCostRewardAfterCirculation() public {
        _mint(1, 100, type(uint256).max);
        nc.deposit(1, 11, address(0xB0B), a, b, c);
        vm.prank(minter);
        (uint256 c1, uint256 r1) = nc.rearm(1, type(uint256).max);
        assertEq(c1, 0);
        assertEq(r1, 0); // too soon: < 7 days since era start
        nc.deposit(1, 12, address(0xB0B), a, b, c);
        vm.warp(block.timestamp + 7 days);
        vm.prank(minter);
        (uint256 c2, uint256 r2) = nc.rearm(1, type(uint256).max);
        assertEq(c2, 0);
        assertEq(r2, 0.2e18); // rho * V, no small bonus on re-arm
        _conserved();
    }

    function test_SmallNoteReserveSurvivesLargeMints() public {
        nc.setParams(100, 1 days, 10 * UNIT, 5_000_000 * UNIT, 30 days, 30 days); // budget 10, 50% reserved
        (uint256 c1, uint256 r1) = _mint(1, 500, type(uint256).max); // 4 <= 5 large budget
        assertEq(c1 + r1, 4e18);
        (uint256 c2, uint256 r2) = _mint(2, 500, type(uint256).max); // 8 > 5: 1:1
        assertEq(c2 + r2, 0);
        (uint256 c3, uint256 r3) = _mint(3, 5, type(uint256).max); // small notes still credited
        assertEq(c3 + r3, 2.26e18);
        _conserved();
    }

    function test_RegistryFailureFailsClosedToOneToOne() public {
        nc.setRegistry(IParamRegistry(address(0xdead))); // no code: every read reverts
        (uint256 cc, uint256 rr) = _mint(1, 5, type(uint256).max);
        assertEq(cc + rr, 0);
        _conserved();
    }

    function test_GovernanceMovesPolicyWithinBounds() public {
        vm.warp(block.timestamp + 7 days);
        reg.setParameter(reg.SMALL_BONUS(), 0.5e18);
        (, uint256 rr) = _mint(1, 5, type(uint256).max);
        assertEq(rr, 0.51e18);
        bytes32 k = reg.COST_CAP();
        vm.warp(block.timestamp + 7 days);
        vm.expectRevert();
        reg.setParameter(k, 0.55e18); // above hard 50%
    }

    function test_LocalPolicyBounds() public {
        vm.expectRevert(NoteController.OutOfBounds.selector);
        nc.setCostPolicy(5001, 0, 0, 0, 7 days);
        vm.expectRevert(NoteController.OutOfBounds.selector);
        nc.setCostPolicy(0, 10 * UNIT + 1, 0, 0, 7 days);
        vm.expectRevert(NoteController.OutOfBounds.selector);
        nc.setCostPolicy(0, 0, UNIT + 1, 0, 7 days);
        vm.expectRevert(NoteController.OutOfBounds.selector);
        nc.setCostPolicy(0, 0, 0, 8001, 7 days);
        vm.expectRevert(NoteController.OutOfBounds.selector);
        nc.setCostPolicy(0, 0, 0, 0, 91 days);
        vm.prank(minter);
        vm.expectRevert();
        nc.setRegistry(IParamRegistry(address(0)));
    }

    /// Any policy inside the hard bounds: credit <= 60% of V, cost <= attested, supply unchanged.
    function testFuzz_CreditBoundedAndConserved(
        uint16 cb,
        uint256 ca,
        uint256 bo,
        uint16 rb,
        uint8 di,
        uint256 attested
    ) public {
        nc.setRegistry(IParamRegistry(address(0)));
        nc.setCostPolicy(
            uint16(bound(cb, 0, 5000)), bound(ca, 0, 10 * UNIT), bound(bo, 0, UNIT), uint16(bound(rb, 0, 8000)), 7 days
        );
        uint32[7] memory ds = [uint32(5), 10, 20, 50, 100, 200, 500];
        uint32 d = ds[di % 7];
        uint256 supply = tcl.totalSupply();
        (uint256 cc, uint256 rr) = _mint(1, d, attested);
        assertLe(cc, attested);
        assertLe(cc + rr, (uint256(d) * UNIT * 6000) / 10_000);
        _conserved();
        assertEq(tcl.totalSupply(), supply);
    }
}

contract Handler is Test {
    NoteController nc;
    MockTCL tcl;
    address minter;
    uint256 next = 1;
    uint256[] live;
    uint256[2] a;
    uint256[2][2] b;
    uint256[2] c;

    constructor(NoteController nc_, MockTCL tcl_, address minter_) {
        nc = nc_;
        tcl = tcl_;
        minter = minter_;
    }

    function mint(uint8 di, uint256 cost) external {
        uint32[7] memory ds = [uint32(5), 10, 20, 50, 100, 200, 500];
        vm.prank(minter);
        nc.mint(next, ds[di % 7], cost);
        live.push(next++);
    }

    function deposit(uint256 i, uint8 who) external {
        if (live.length == 0) return;
        uint256 id = live[i % live.length];
        (,,, NoteController.State s) = nc.notes(id);
        if (s != NoteController.State.LIVE) return;
        nc.deposit(
            id, uint256(keccak256(abi.encode(id, next++))) % 1e70 + 1, address(uint160(0x1000 + who % 4)), a, b, c
        );
    }

    function rearm(uint256 i) external {
        if (live.length == 0) return;
        vm.prank(minter);
        try nc.rearm(live[i % live.length], 0) {} catch {}
    }

    function warp(uint32 dt) external {
        vm.warp(block.timestamp + (dt % 10 days));
    }

    function release(uint8 who) external {
        vm.prank(address(uint160(0x1000 + who % 4)));
        nc.release();
    }
}

contract NoteControllerInvariantTest is Base {
    Handler h;
    uint256 supply;

    function setUp() public {
        _deploy(address(new MockVerifier()), address(0));
        nc.setParams(100, 1 days, 1e30, 300 * UNIT, 30 days, 30 days);
        h = new Handler(nc, tcl, minter);
        supply = tcl.totalSupply();
        targetContract(address(h));
    }

    function invariant_Conservation() public view {
        _conserved();
        assertEq(tcl.totalSupply(), supply);
    }
}
