# TCL Setup

> **In plain words.** This page shows how to install TCL on your own computer and run its tests. You type the commands shown in grey boxes into a terminal (the text window where you type commands). The basic install needs only Python and git. The proof tools (circom, snarkjs) and blockchain tools (Foundry) are only needed if you want to make proofs and run the contracts. Words like *daemon*, *container* and *circuit* are explained in the [Glossary](GLOSSARY.md).
>
> **What this means for you:** start with "Install" and "Run tests". If those pass, your setup works.

## Requirements

- Python 3.11 or newer
- git 2.20 or newer
- Optional: podman (rootless runs) or Docker with Compose v2 (`docker compose`)
- For ZK + settlement: circom 2.1.9, node 20 + `npm ci` (snarkjs 0.7.6, circomlib), foundry (forge/cast/anvil), `git submodule update --init --recursive` (OpenZeppelin, forge-std)

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` or edit `config.yaml`.
Precedence: CLI flag > `TCL_*` env > `.env` > `config.yaml` (or `$TCL_CONFIG`) > defaults. All CLI commands resolve their options this way.
Keys: `db`, `log_level`, `mode`, `tree_depth`, `socket`, `tasks_db`, `http_host`, `http_port`, `zk_build`, `zk_depth`, `ptau_power`, `rpc_url`, `chain_private_key`, `deployment` (env: `TCL_<KEY>`).
The tree depth is fixed when a database is created. Opening it with another depth fails closed.

## Run tests

```bash
pytest -q            # ~25 s; includes the 1,000-transition SIGKILL recovery test
pytest -q tests/test_poseidon.py   # circomlib known-answer vectors only
```

## Rootless container

```bash
podman build -t tcl-test -f Containerfile .
podman run --rm tcl-test
```

## CLI

```bash
export TCL_DB=./tcl.db
tcl ledger init
tcl ledger deposit alice 6000000000000000000000000
tcl ledger apply-decay
tcl ledger balance alice
tcl ledger root --history          # current Poseidon root, integrity check, history
tcl ledger proof alice > p.json    # inclusion proof
tcl ledger verify-proof p.json
tcl ledger rollback --root 0x...   # restore a prior verified root
tcl worktree create feat/example --claim ledger/storage.py
tcl worktree list
```

Set `TCL_LOG_LEVEL=INFO` to get JSON log lines on stderr.

## Provenance

```bash
TCL_TEST_RESULTS='{"passed": N, "failed": 0}' python3 scripts/record_provenance.py
```

## ZK and contracts toolchain (Phase 3)

Everything installs in user space, with no sudo needed:

```bash
# circom 2.1.9 (prebuilt)
curl -sSfL -o ~/.local/bin/circom https://github.com/iden3/circom/releases/download/v2.1.9/circom-linux-amd64
chmod +x ~/.local/bin/circom
npm ci                                   # circomlib 2.0.5, snarkjs 0.7.6, solhint
curl -L https://foundry.paradigm.xyz | bash && ~/.foundry/bin/foundryup
cargo install --locked circomspect       # optional; needs rustc >= 1.88 for --locked deps
git submodule update --init --recursive  # forge-std, OpenZeppelin v5.1.0
```

```bash
TCL_REQUIRE_ZK=1 pytest -q tests/test_zk_prover.py    # Groth16 end-to-end, about 75 s
forge test                                            # Solidity, including the real-proof suite
circomspect -a CS0005 -L node_modules circuits/puf_verify.circom circuits/lib/*.circom
npx solhint 'contracts/**/*.sol' --ignore-path .solhintignore --max-warnings 0
python scripts/gen_zk_fixture.py   # regenerates StateVerifier.sol and the proof fixture together
```

## Phase 4: end-to-end (local)

```bash
anvil --port 8545 &                       # local EVM
export TCL_DB=demo.db TCL_TREE_DEPTH=4    # ledger depth must equal the circuit depth (zk_depth=4)
tcl zk compile                            # one-time, ~60 s, cached in .tcl/zk (DEV setup)
NOW=$(date +%s); T0=$((NOW-3*86400))
tcl ledger deposit alice 7000000000000000000000000 --now $T0
tcl chain deploy                          # verifier from .tcl/zk zkey, genesis = ledger root
tcl zk prove alice --now $((NOW-2)) --out proof.json
tcl zk verify proof.json
tcl chain settle proof.json               # prints the StateSettled event
```

Integration tests (skip without tools; `TCL_REQUIRE_ZK=1 TCL_REQUIRE_CHAIN=1` makes them fail closed):

```bash
TCL_ZK_CACHE=/tmp/zk pytest -q -s test/integration
```

## Docker Compose (Acceptance 1)

```bash
docker compose up --build -d --wait
curl -fsS http://127.0.0.1:8787/health    # 200 {"status": "healthy", ...}
docker compose run --rm toolchain         # circom/snarkjs/forge versions
docker compose run --rm deploy            # deploy contracts to the compose anvil
docker compose down -v
```

## Releases

Push a tag `vX.Y.Z`; the `release` CI job builds wheel + sdist, writes `SHA256SUMS` and creates a GitHub release after every other job passes.
Note: `tcl chain` / `tcl zk` use the repo's `contracts/`, `circuits/` and `lib/`, so they need a source checkout (or the container), not just the wheel.
