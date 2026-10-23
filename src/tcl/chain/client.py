"""Deploy CirculatoryEngine (UUPS proxy) + a snarkjs-exported StateVerifier to an
EVM node with forge, and submit ``settleEpoch`` with cast.

The verifier must match the proving key, so ``build_project`` assembles an
out-of-tree Foundry project: repo contracts + a verifier freshly exported from
the given zkey (or the committed one), with remappings to the repo ``lib/``.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Optional

from ..utils.logger import get_logger

log = get_logger("chain")
# Anvil's well-known account #0 key (public; local dev chains only).
ANVIL_DEV_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEV_CHAIN_IDS = frozenset({31337, 1337})
STATE_SETTLED_SIG = "StateSettled(uint64,uint256,uint256,uint256,uint64)"
SETTLE_SIG = "settleEpoch(uint256[2],uint256[2][2],uint256[2],uint256[4])"
INIT_SIG = "initialize(address,address,uint256,uint256,uint256,uint64)"


class ChainError(RuntimeError):
    pass


def discover() -> dict[str, str]:
    tools = {}
    for name in ("forge", "cast", "anvil"):
        p = os.environ.get(f"TCL_{name.upper()}") or shutil.which(name) or \
            _existing(Path.home() / ".foundry/bin" / name)
        if not p:
            raise ChainError(f"missing EVM toolchain: {name}")
        tools[name] = p
    return tools


def _existing(p: Path) -> Optional[str]:
    return str(p) if p.exists() else None


def _run(cmd: list, what: str, cwd: Optional[Path] = None) -> str:
    log.info("chain.run", extra={"step": what})
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise ChainError(f"{what} failed (exit {r.returncode}): {(r.stderr or r.stdout)[-2000:]}")
    return r.stdout


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_rpc(rpc: str, timeout: float = 20.0, cast: Optional[str] = None) -> int:
    cast = cast or discover()["cast"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run([cast, "chain-id", "--rpc-url", rpc], capture_output=True, text=True)
        if r.returncode == 0:
            return int(r.stdout.strip())
        time.sleep(0.2)
    raise ChainError(f"RPC {rpc} not reachable after {timeout}s")


@contextlib.contextmanager
def anvil(port: Optional[int] = None) -> Iterator[str]:
    """Run a throwaway anvil node; yields its RPC URL."""
    tools = discover()
    port = port or _free_port()
    proc = subprocess.Popen([tools["anvil"], "--port", str(port), "--silent"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    rpc = f"http://127.0.0.1:{port}"
    try:
        wait_rpc(rpc, cast=tools["cast"])
        yield rpc
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


def build_project(work: Path, zkey: Optional[Path] = None) -> Path:
    """Assemble + ``forge build`` a project with a verifier matching ``zkey``."""
    from ..zk import export_verifier
    from ..resources import ResourcesMissing, resource_root
    tools = discover()
    try:
        REPO_ROOT = resource_root()
    except ResourcesMissing as e:
        raise ChainError(str(e)) from e
    work = Path(work)
    src = work / "src"
    (src / "verifiers").mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO_ROOT / "contracts/CirculatoryEngine.sol", src / "CirculatoryEngine.sol")
    ver = src / "verifiers/StateVerifier.sol"
    if zkey:
        export_verifier(Path(zkey), ver)
    else:
        shutil.copy(REPO_ROOT / "contracts/verifiers/StateVerifier.sol", ver)
    (src / "Proxy.sol").write_text(
        "// SPDX-License-Identifier: MIT\npragma solidity 0.8.24;\n"
        'import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";\n'
        "contract EngineProxy is ERC1967Proxy {\n"
        "    constructor(address impl, bytes memory data) payable ERC1967Proxy(impl, data) {}\n}\n")
    lib = REPO_ROOT / "lib"
    (work / "foundry.toml").write_text(
        '[profile.default]\nsrc = "src"\nout = "out"\nlibs = []\nsolc_version = "0.8.24"\n'
        'evm_version = "cancun"\noptimizer = true\noptimizer_runs = 200\nremappings = [\n'
        f'  "@openzeppelin/contracts/={lib}/openzeppelin-contracts/contracts/",\n'
        f'  "@openzeppelin/contracts-upgradeable/={lib}/openzeppelin-contracts-upgradeable/contracts/",\n'
        "]\n")
    if not (lib / "openzeppelin-contracts/contracts").exists():
        raise ChainError("lib/openzeppelin-contracts missing; run git submodule update --init")
    _run([tools["forge"], "build", "--root", work], "forge-build")
    return work


@dataclass
class Deployment:
    rpc_url: str
    chain_id: int
    owner: str
    verifier: str
    implementation: str
    engine: str
    genesis_root: str
    genesis_time: int

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path: Path) -> "Deployment":
        return cls(**json.loads(Path(path).read_text()))


def _create(tools, root: Path, target: str, rpc: str, key: str, args: list[str]) -> str:
    cmd = [tools["forge"], "create", "--root", root, "--rpc-url", rpc, "--private-key", key,
           "--broadcast", "--json", target]
    if args:
        cmd += ["--constructor-args", *args]
    out = _run(cmd, f"create {target.split(':')[-1]}")
    dec = json.JSONDecoder()
    i = out.find("{")
    while i != -1:
        with contextlib.suppress(json.JSONDecodeError):
            d, _ = dec.raw_decode(out, i)
            if isinstance(d, dict) and "deployedTo" in d:
                return d["deployedTo"]
        i = out.find("{", i + 1)
    raise ChainError(f"forge create gave no address: {out[-500:]}")


def deploy(rpc: str, genesis_root: int, genesis_time: int, work: Path,
           zkey: Optional[Path] = None, key: str = ANVIL_DEV_KEY,
           threshold: int = 0, rate_per_second: int = 0) -> Deployment:
    tools = discover()
    chain_id = wait_rpc(rpc, cast=tools["cast"])
    if key.lower() == ANVIL_DEV_KEY and chain_id not in DEV_CHAIN_IDS:
        raise ChainError(f"refusing to deploy to chain {chain_id} with the public anvil dev key; "
                         "set TCL_CHAIN_PRIVATE_KEY (or use script/Deploy.s.sol with a keystore)")
    root = build_project(work, zkey)
    owner = _run([tools["cast"], "wallet", "address", "--private-key", key], "owner").strip()
    verifier = _create(tools, root, "src/verifiers/StateVerifier.sol:StateVerifier", rpc, key, [])
    impl = _create(tools, root, "src/CirculatoryEngine.sol:CirculatoryEngine", rpc, key, [])
    init = _run([tools["cast"], "calldata", INIT_SIG, owner, verifier, str(threshold),
                 str(rate_per_second), str(genesis_root), str(genesis_time)], "calldata").strip()
    proxy = _create(tools, root, "src/Proxy.sol:EngineProxy", rpc, key, [impl, init])
    got = _run([tools["cast"], "call", proxy, "stateRoot()(uint256)", "--rpc-url", rpc],
               "stateRoot").split()[0]
    if int(got) != int(genesis_root):
        raise ChainError(f"deployed stateRoot {got} != genesis {genesis_root}")
    dep = Deployment(rpc, chain_id, owner, verifier, impl, proxy, str(genesis_root), genesis_time)
    log.info("chain.deployed", extra=dep.to_json())
    return dep


def state_root(dep: Deployment) -> int:
    """Current on-chain ``stateRoot`` of the engine proxy."""
    out = _run([discover()["cast"], "call", dep.engine, "stateRoot()(uint256)",
                "--rpc-url", dep.rpc_url], "stateRoot")
    return int(out.split()[0])


def _arr(xs) -> str:
    return "[" + ",".join(_arr(x) if isinstance(x, list) else str(x) for x in xs) + "]"


def settle_epoch(dep: Deployment, calldata: dict, key: str = ANVIL_DEV_KEY) -> dict:
    """Send settleEpoch; return the decoded StateSettled event (fail-closed)."""
    tools = discover()
    if key.lower() == ANVIL_DEV_KEY and dep.chain_id not in DEV_CHAIN_IDS:
        raise ChainError(f"refusing to sign for chain {dep.chain_id} with the public anvil dev key")
    out = _run([tools["cast"], "send", dep.engine, SETTLE_SIG, _arr(calldata["a"]),
                _arr(calldata["b"]), _arr(calldata["c"]), _arr(calldata["pubSignals"]),
                "--rpc-url", dep.rpc_url, "--private-key", key, "--json"], "settleEpoch")
    rcpt = json.loads(out)
    if int(str(rcpt.get("status", "0")), 0) != 1:
        raise ChainError(f"settleEpoch reverted: {rcpt.get('transactionHash')}")
    topic0 = _run([tools["cast"], "keccak", STATE_SETTLED_SIG], "keccak").strip().lower()
    for lg in rcpt.get("logs", []):
        if lg["address"].lower() == dep.engine.lower() and lg["topics"][0].lower() == topic0:
            data = bytes.fromhex(lg["data"][2:])
            words = [int.from_bytes(data[i:i + 32], "big") for i in range(0, len(data), 32)]
            ev = {"event": "StateSettled", "epoch": int(lg["topics"][1], 16),
                  "oldRoot": str(words[0]), "newRoot": str(words[1]), "fee": str(words[2]),
                  "newTime": words[3], "tx": rcpt["transactionHash"],
                  "block": int(str(rcpt["blockNumber"]), 0), "gasUsed": int(str(rcpt["gasUsed"]), 0)}
            log.info("chain.settled", extra=ev)
            return ev
    raise ChainError("settleEpoch receipt has no StateSettled event")
