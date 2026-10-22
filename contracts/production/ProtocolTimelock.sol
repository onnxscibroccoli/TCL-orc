// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;
import "@openzeppelin/contracts/governance/TimelockController.sol";

/// @notice OpenZeppelin timelock with an effective two-day floor and no bootstrap admin.
contract ProtocolTimelock is TimelockController {
    constructor(uint256 delay, address multisig)
        TimelockController(delay, _proposers(multisig), _executors(), address(0))
    {
        require(delay >= 2 days && multisig.code.length > 0, "governance policy");
    }
    function _proposers(address multisig) private pure returns (address[] memory result) {
        result = new address[](1);
        result[0] = multisig;
    }
    function _executors() private pure returns (address[] memory result) {
        result = new address[](1);
        result[0] = address(0);
    }
    // Inherited scheduling consults this virtual getter, including after updateDelay.
    function getMinDelay() public view override returns (uint256) {
        uint256 configured = super.getMinDelay();
        return configured < 2 days ? 2 days : configured;
    }
}
