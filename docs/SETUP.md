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

## Run tests

```bash
pytest -q
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
tcl worktree create feat/example --claim ledger/storage.py
tcl worktree list
```

Set `TCL_LOG_LEVEL=INFO` to get JSON log lines on stderr.

## Provenance

```bash
TCL_TEST_RESULTS='{"passed": N, "failed": 0}' python3 scripts/record_provenance.py
```
