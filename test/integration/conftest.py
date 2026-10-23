"""Shared fixtures for end-to-end tests.

Skipped when circom/snarkjs or foundry are missing, unless TCL_REQUIRE_ZK=1 /
TCL_REQUIRE_CHAIN=1 (CI), in which case a missing tool is a hard failure.
The circuit build + DEV Groth16 setup is a one-time cost; it is cached in
$TCL_ZK_CACHE when set (CI caches it) and timed separately from the pipeline.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest

from tcl.chain import ChainError, discover
from tcl.zk import Toolchain, ToolchainMissing, ensure_artifacts

DEPTH = 4


def _require(flag: str, err: Exception):
    if os.environ.get(flag) == "1":
        pytest.fail(f"{flag}=1 but {err}")
    pytest.skip(str(err))


@pytest.fixture(scope="session")
def zk_tc():
    try:
        return Toolchain.discover()
    except ToolchainMissing as e:
        _require("TCL_REQUIRE_ZK", e)


@pytest.fixture(scope="session")
def evm_tools():
    try:
        return discover()
    except ChainError as e:
        _require("TCL_REQUIRE_CHAIN", e)


@pytest.fixture(scope="session")
def zk_art(zk_tc, tmp_path_factory):
    build = Path(os.environ["TCL_ZK_CACHE"]) if os.environ.get("TCL_ZK_CACHE") else \
        tmp_path_factory.mktemp("zkbuild")
    t0 = time.monotonic()
    art = ensure_artifacts(DEPTH, build, 13, tc=zk_tc)
    art_setup_seconds = time.monotonic() - t0
    print(f"\n[zk] one-time compile+setup: {art_setup_seconds:.2f}s (cache={build})")
    return art


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=r, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (r / "README").write_text("x")
    run("add", ".")
    run("commit", "-qm", "init")
    return r
