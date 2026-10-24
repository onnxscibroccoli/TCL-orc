"""TCL localnet: a self-contained, offline, deterministic local test network.

Replaces anvil for TCL-native development. Stdlib-only (plus existing tcl.ledger).

PLACEHOLDER CONSENSUS: block production is round-robin with a 2/3 state-root
quorum among in-process validators. It exists only to exercise the ledger and
tooling and will be replaced by the design in docs/13a_consensus_interfaces.md.
"""
from .genesis import Genesis, DevAccount
from .node import Localnet, LocalnetError, Block, Tx

__all__ = ["Genesis", "DevAccount", "Localnet", "LocalnetError", "Block", "Tx"]
