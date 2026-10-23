# TCL Setup

## Requirements

- Python 3.11 or newer
- git 2.20 or newer
- Optional: podman for rootless container runs

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` or edit `config.yaml`.
Environment variables (`TCL_DB`, `TCL_MODE`, `TCL_TREE_DEPTH`, `TCL_LOG_LEVEL`) override `.env`, and `.env` overrides `config.yaml`.
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
