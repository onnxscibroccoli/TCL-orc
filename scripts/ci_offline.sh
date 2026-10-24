#!/usr/bin/env bash
# Runs INSIDE a network namespace with only loopback (see the `offline` CI job / docs/OFFLINE.md).
# Installs only from vendor/ and runs the suite + a localnet smoke. Fails if egress works.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"

echo "== egress must be impossible"
"$PY" - <<'PY'
import socket
for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53), ("140.82.112.3", 443)):
    try:
        socket.create_connection((host, port), timeout=3).close()
    except OSError as e:
        print(f"  {host}:{port} unreachable ({e.__class__.__name__}) - good")
    else:
        raise SystemExit(f"network reachable at {host}:{port}; not an offline sandbox")
try:
    socket.getaddrinfo("pypi.org", 443)
except OSError:
    print("  DNS unavailable - good")
else:
    raise SystemExit("DNS resolves; not an offline sandbox")
PY

echo "== bundle verify + install from bundle only"
"$PY" scripts/bundle_verify.py
VENV="${OFFLINE_VENV:-$(mktemp -d)/venv}"
"$PY" -m venv "$VENV"
"$PY" scripts/offline_install.py --python "$VENV/bin/python"
export PATH="$VENV/bin:$PATH"
tcl offline verify
python scripts/check_no_ethereum.py

echo "== test suite (offline)"
python -m pytest -q -p no:cacheprovider

if [ -d src/tcl/localnet ]; then
  echo "== localnet smoke (4 validators, offline)"
  D="$(mktemp -d)/ln"
  tcl localnet start -n 4 --dev --data-dir "$D"
  trap 'tcl localnet stop --data-dir "$D" || true' EXIT
  tcl localnet mine 3 --data-dir "$D"
  tcl localnet time advance 86400 --data-dir "$D"
  snap="$(tcl localnet snapshot --data-dir "$D" | tail -1)"
  tcl localnet mine 2 --data-dir "$D"
  tcl localnet revert "$snap" --data-dir "$D"
  tcl localnet status --data-dir "$D"
else
  echo "== src/tcl/localnet not on this branch yet (PR #15); localnet smoke skipped"
fi
echo "offline: OK"
