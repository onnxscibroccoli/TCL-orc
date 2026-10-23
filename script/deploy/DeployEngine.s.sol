// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {CirculatoryEngine, IStateVerifier} from "../../contracts/CirculatoryEngine.sol";
import {StateVerifier} from "../../contracts/verifiers/StateVerifier.sol";

/// @notice Deploy StateVerifier + CirculatoryEngine (UUPS impl) + ERC1967 proxy.
/// @dev Env-driven; no secrets in the repo. The signer comes from forge flags
///      (`--account <keystore>` / `--ledger` / `--private-key $PK`), never from this file.
///      Fail-closed on non-dev chains: EXPECTED_CHAIN_ID must match, a final
///      owner contract (timelock/multisig) is required, and the verifier's trusted
///      setup must be attested via VERIFIER_CEREMONY_ATTESTED=true.
///
///   Required env:  GENESIS_ROOT (uint, < BN254 field)
///   Optional env:  GENESIS_TIME (default block.timestamp), THRESHOLD (0),
///                  RATE_PER_SECOND (0), SETTLER (default deployer),
///                  FINAL_OWNER (timelock; required on non-dev chains),
///                  EXPECTED_CHAIN_ID (required on non-dev chains),
///                  VERIFIER_CEREMONY_ATTESTED (required "true" on non-dev chains)
contract DeployEngine is Script {
    error WrongChain(uint256 expected, uint256 actual);
    error NotAttested();
    error FinalOwnerRequired();
    error FinalOwnerNotContract(address owner);
    error PostCondition(string what);

    struct Result {
        address verifier;
        address implementation;
        address proxy;
        address owner;
        address settler;
    }

    function isDevChain(uint256 id) public pure returns (bool) {
        return id == 31337 || id == 1337;
    }

    function run() external returns (Result memory r) {
        uint256 genesisRoot = vm.envUint("GENESIS_ROOT");
        uint64 genesisTime = uint64(vm.envOr("GENESIS_TIME", block.timestamp));
        uint256 threshold = vm.envOr("THRESHOLD", uint256(0));
        uint256 rate = vm.envOr("RATE_PER_SECOND", uint256(0));
        address finalOwner = vm.envOr("FINAL_OWNER", address(0));

        if (!isDevChain(block.chainid)) {
            uint256 expected = vm.envOr("EXPECTED_CHAIN_ID", uint256(0));
            if (expected != block.chainid) revert WrongChain(expected, block.chainid);
            if (!vm.envOr("VERIFIER_CEREMONY_ATTESTED", false)) revert NotAttested();
            if (finalOwner == address(0)) revert FinalOwnerRequired();
        }
        if (finalOwner != address(0) && finalOwner.code.length == 0 && !isDevChain(block.chainid)) {
            revert FinalOwnerNotContract(finalOwner);
        }

        vm.startBroadcast();
        (, address deployer,) = vm.readCallers();
        address settler = vm.envOr("SETTLER", deployer);
        StateVerifier verifier = new StateVerifier();
        CirculatoryEngine impl = new CirculatoryEngine();
        bytes memory init = abi.encodeCall(
            CirculatoryEngine.initialize,
            (deployer, IStateVerifier(address(verifier)), threshold, rate, genesisRoot, genesisTime)
        );
        CirculatoryEngine engine = CirculatoryEngine(payable(address(new ERC1967Proxy(address(impl), init))));
        if (settler != deployer) engine.setSettler(settler);
        if (finalOwner != address(0) && finalOwner != deployer) engine.transferOwnership(finalOwner);
        vm.stopBroadcast();

        // post-conditions (fail-closed)
        if (engine.stateRoot() != genesisRoot) revert PostCondition("stateRoot");
        if (address(engine.verifier()) != address(verifier)) revert PostCondition("verifier");
        address expectOwner = finalOwner == address(0) ? deployer : finalOwner;
        if (engine.owner() != expectOwner) revert PostCondition("owner");
        bytes32 slot = vm.load(address(engine), bytes32(uint256(keccak256("eip1967.proxy.implementation")) - 1));
        if (address(uint160(uint256(slot))) != address(impl)) revert PostCondition("impl slot");

        r = Result(address(verifier), address(impl), address(engine), engine.owner(), engine.settler());
        console2.log("chain_id", block.chainid);
        console2.log("verifier", r.verifier);
        console2.log("implementation", r.implementation);
        console2.log("proxy", r.proxy);
        console2.log("owner", r.owner);
        console2.log("settler", r.settler);
    }
}
