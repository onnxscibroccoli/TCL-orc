"""Locate the non-Python artifacts `tcl zk` / `tcl chain` need.

Resolution order (first hit wins, fail-closed if none):
  1. ``$TCL_RESOURCE_ROOT`` (explicit override)
  2. a source checkout (``<repo>/circuits`` next to ``src/tcl``)
  3. data bundled in the wheel at ``tcl/_data`` (circuits/, contracts/, lib/ OZ sources)

circomlib (GPL-3.0) is *not* bundled: install it with ``npm i circomlib`` and point
``TCL_CIRCOMLIB`` at the ``node_modules`` dir (``tcl doctor`` checks this).
"""
from __future__ import annotations

import os
from pathlib import Path

_PKG = Path(__file__).resolve().parent
_CHECKOUT = _PKG.parents[1]
REQUIRED = ("circuits/puf_verify.circom", "contracts/CirculatoryEngine.sol",
            "contracts/verifiers/StateVerifier.sol",
            "lib/openzeppelin-contracts/contracts/proxy/ERC1967/ERC1967Proxy.sol",
            "lib/openzeppelin-contracts-upgradeable/contracts/proxy/utils/UUPSUpgradeable.sol")


class ResourcesMissing(RuntimeError):
    pass


def _complete(root: Path) -> bool:
    return all((root / r).is_file() for r in REQUIRED)


def candidates() -> list[Path]:
    env = os.environ.get("TCL_RESOURCE_ROOT")
    if env:
        return [Path(env)]  # explicit override is never silently ignored
    return [_CHECKOUT, _PKG / "_data"]


def resource_root() -> Path:
    for c in candidates():
        if _complete(c):
            return c
    missing = {str(c): [r for r in REQUIRED if not (c / r).is_file()] for c in candidates()}
    raise ResourcesMissing(
        "TCL contract/circuit artifacts not found. Install a release wheel (bundles them), "
        "run from a source checkout with `git submodule update --init`, or set "
        f"TCL_RESOURCE_ROOT. Missing: {missing}")


def checkout_root() -> Path:
    """Repo root for dev-only paths (node_modules, test fixtures)."""
    return _CHECKOUT
