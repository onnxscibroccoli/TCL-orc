// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "../CirculatoryEngine.sol";
import "./ProductionMintController.sol";

/// @notice Atomic deployment/configuration; external release authorization is verified off-chain.
contract ProtocolDeploymentFactory {
    struct Configuration {
        address governance;
        address guardian;
        address initialHolder;
        uint256 supply;
        address verifier;
        bytes32 verifierCodeHash;
        bytes32 releaseManifestHash;
        address enrollmentAuthority;
    }
    event ProtocolDeployed(address indexed engine, address indexed controller,
        bytes32 indexed releaseManifestHash, address governance, bytes32 verifierCodeHash);

    function deployProtocol(Configuration calldata config) external returns (address engine, address controller) {
        CirculatoryEngine ledger = new CirculatoryEngine(address(this), config.initialHolder, config.supply);
        ProductionMintController mint = new ProductionMintController(address(ledger), config.verifier,
            config.governance, config.guardian, config.releaseManifestHash,
            config.verifierCodeHash, config.enrollmentAuthority);
        ledger.setPhysicalMint(address(mint));
        ledger.proposeGovernor(config.governance);
        // The timelock must accept through its normal scheduled execution path.
        // No factory method can subsequently mint, redistribute or redirect escrow.
        engine = address(ledger);
        controller = address(mint);
        emit ProtocolDeployed(engine, controller, config.releaseManifestHash,
            config.governance, config.verifierCodeHash);
    }
}
