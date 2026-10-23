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
