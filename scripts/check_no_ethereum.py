#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`no-ethereum` gate: the sovereign code paths must not depend on Ethereum/EVM tooling.

Checked (AST, not grep, so docs/docstrings may still *mention* anvil when comparing):
  * imports of Ethereum/EVM packages (web3, eth_*, evm, rlp, solcx, ...) or of the legacy
    EVM client `tcl.chain`;
  * string literals naming Ethereum JSON-RPC methods (`eth_*`, `net_version`, `anvil_*`,
    `evm_*`) or EVM tool executables (anvil, forge, cast, solc) -- docstrings excluded;
  * `keccak` anywhere in code identifiers/strings (TCL hashes are SHA-256 / Poseidon);
  * Ethereum packages in the offline wheel set (vendor/requirements-offline.txt).
Circuits under circuits/lib/tcl must not include circomlib.

LEGACY (not gated, scheduled for removal once native modules reach parity, see
docs/DECISIONS.md D-OFF-5): contracts/, script/, lib/ (forge-std, OpenZeppelin),
test/contracts/, src/tcl/chain/, foundry.toml, foundry.lock, slither.config.json.

  python scripts/check_no_ethereum.py [paths...]     # exit 1 on any violation
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOVEREIGN = ["src/tcl/consensus", "src/tcl/localnet", "src/tcl/node", "src/tcl/offline.py",
             "scripts/bundle.py", "scripts/bundle_verify.py", "scripts/offline_install.py",
             "scripts/check_no_ethereum.py"]
LEGACY = ["contracts/", "script/", "lib/", "test/contracts/", "src/tcl/chain/", "foundry.toml",
          "foundry.lock", "slither.config.json"]
BAD_MODULES = {"web3", "eth_abi", "eth_account", "eth_utils", "eth_typing", "eth_keys",
               "eth_hash", "eth_rlp", "eth_tester", "ethereum", "evm", "pyevm", "py_evm",
               "rlp", "solcx", "vyper", "hexbytes", "brownie", "ape", "foundry", "ckzg",
               "coincurve", "pysha3", "sha3", "Crypto.Hash.keccak"}
BAD_PREFIX = ("eth_",)
BAD_STRINGS = re.compile(r"^(eth_|anvil_|evm_|hardhat_|net_version$|web3_)|^(anvil|forge|cast|"
                         r"solc|hardhat|ganache)$")
KECCAK = re.compile(r"keccak", re.I)
BAD_REQS = re.compile(r"^(web3|eth[-_].*|py-evm|rlp|py-solc-x|vyper|hexbytes|eth-brownie|"
                      r"eth-ape|ckzg|coincurve|pysha3)\b", re.I)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", [])
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                ids.add(id(b[0].value))
    return ids


def check_py(path: Path) -> list[str]:
    rel = path.relative_to(ROOT)
    if rel.as_posix() == "scripts/check_no_ethereum.py":
        return []  # this file necessarily spells the forbidden names
    tree = ast.parse(path.read_text(), str(path))
    docs = _docstring_nodes(tree)
    out = []
    for n in ast.walk(tree):
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods = [n.module] if n.level == 0 else [f"tcl.{n.module}" if n.level else n.module]
        for m in mods:
            top = m.split(".")[0]
            if top in BAD_MODULES or m in BAD_MODULES or top.startswith(BAD_PREFIX) \
                    or m == "tcl.chain" or m.startswith("tcl.chain."):
                out.append(f"{rel}:{n.lineno}: forbidden import {m}")
        if isinstance(n, ast.ImportFrom) and n.level and n.module and \
                n.module.split(".")[0] == "chain":
            out.append(f"{rel}:{n.lineno}: forbidden relative import of legacy tcl.chain")
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            if BAD_STRINGS.search(n.value.strip()) or KECCAK.search(n.value):
                out.append(f"{rel}:{n.lineno}: Ethereum/EVM string literal {n.value[:40]!r}")
        if isinstance(n, ast.Name) and KECCAK.search(n.id):
            out.append(f"{rel}:{n.lineno}: keccak identifier {n.id}")
    return out


def check_circom(path: Path) -> list[str]:
    rel = path.relative_to(ROOT)
    return [f"{rel}:{i}: includes circomlib" for i, line in enumerate(path.read_text().splitlines(), 1)
            if line.strip().startswith("include") and "circomlib" in line]


def main(argv: list[str]) -> int:
    targets = [ROOT / p for p in (argv or SOVEREIGN)]
    problems: list[str] = []
    scanned = 0
    for t in targets:
        files = sorted(t.rglob("*.py")) if t.is_dir() else ([t] if t.is_file() else [])
        for f in files:
            scanned += 1
            problems += check_py(f)
    for f in sorted((ROOT / "circuits").rglob("*.circom")):
        scanned += 1
        problems += check_circom(f)
    req = ROOT / "vendor/requirements-offline.txt"
    if req.is_file():
        for i, line in enumerate(req.read_text().splitlines(), 1):
            if BAD_REQS.match(line.strip()):
                problems.append(f"vendor/requirements-offline.txt:{i}: Ethereum package {line}")
    for p in problems:
        print(f"no-ethereum: {p}", file=sys.stderr)
    present = [p for p in SOVEREIGN if (ROOT / p).exists()]
    print(f"no-ethereum: scanned {scanned} files in {present} + circuits/; "
          f"legacy (ungated): {LEGACY}; " + ("FAILED" if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
