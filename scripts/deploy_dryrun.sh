#!/usr/bin/env bash
# CI/local dry-run of the production deploy path against a throwaway anvil.
# Uses anvil's public dev account only (chain 31337); real deploys use a keystore.
set -euo pipefail
PORT="${PORT:-8546}"
RPC="http://127.0.0.1:${PORT}"
anvil --port "$PORT" --silent & APID=$!
trap 'kill $APID 2>/dev/null || true' EXIT
for _ in $(seq 50); do cast chain-id --rpc-url "$RPC" >/dev/null 2>&1 && break; sleep 0.2; done
DEV_PK=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
export GENESIS_ROOT="${GENESIS_ROOT:-12345}" THRESHOLD=5000000 RATE_PER_SECOND=5787037037

# 1) simulation only (no --broadcast)
forge script script/deploy/DeployEngine.s.sol:DeployEngine --rpc-url "$RPC" --private-key "$DEV_PK"
# 2) broadcast to anvil, then verify on-chain state with cast
forge script script/deploy/DeployEngine.s.sol:DeployEngine --rpc-url "$RPC" \
  --private-key "$DEV_PK" --broadcast >/dev/null
proxy=$(jq -r '.transactions[] | select(.contractName=="ERC1967Proxy") | .contractAddress' \
        broadcast/DeployEngine.s.sol/31337/run-latest.json)
test -n "$proxy" && test "$proxy" != null
root=$(cast call "$proxy" 'stateRoot()(uint256)' --rpc-url "$RPC" | awk '{print $1}')
[ "$root" = "$GENESIS_ROOT" ] || { echo "stateRoot $root != $GENESIS_ROOT"; exit 1; }
echo "deploy ok: proxy=$proxy stateRoot=$root"
# 3) upgrade path executes on dev chain
PROXY="$proxy" EXECUTE=true forge script script/deploy/UpgradeEngine.s.sol:UpgradeEngine \
  --rpc-url "$RPC" --private-key "$DEV_PK" --broadcast >/dev/null
echo "upgrade ok"
# 4) fail-closed: a non-dev chain without attestation/owner must revert
anvil --port $((PORT+1)) --chain-id 11155111 --silent & APID2=$!
trap 'kill $APID $APID2 2>/dev/null || true' EXIT
sleep 1
if EXPECTED_CHAIN_ID=11155111 forge script script/deploy/DeployEngine.s.sol:DeployEngine \
     --rpc-url "http://127.0.0.1:$((PORT+1))" --private-key "$DEV_PK" >/dev/null 2>&1; then
  echo "FAIL: non-dev deploy without attestation/owner did not revert"; exit 1
fi
echo "fail-closed guard ok"
