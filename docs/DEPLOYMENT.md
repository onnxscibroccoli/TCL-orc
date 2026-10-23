# TCL deployment runbook (v0.1.0rc1)

> **Status: release candidate, unaudited. Deploy to testnets only.** Mainnet requires
> (1) a public multi-party trusted setup, (2) an external audit, and (3) ownership held by
> a timelock — the deploy script enforces (1) and (3) by refusing to run otherwise.

## 1. Prerequisites

| Item | Who supplies it | Notes |
|---|---|---|
| RPC URL for the target chain | operator | `RPC_URL`; never commit it if it embeds an API key |
| Funded deployer key | operator | import into a Foundry keystore: `cast wallet import deployer --interactive` (or use `--ledger`). Never put it in `.env`, the repo, or CI logs |
| Explorer API key | operator | `ETHERSCAN_API_KEY` for `forge verify-contract` |
| Timelock / multisig address | governance | `FINAL_OWNER`; must be a deployed contract on non-dev chains |
| Ceremony zkey | ZK ceremony | see §2 |
| Toolchain | — | Foundry (forge/cast/anvil), Python ≥ 3.11 + `pip install tcl-*.whl`, circom ≥ 2.1 + snarkjs + circomlib for proving |

Check the machine first (exit code 1 = not ready):

```bash
tcl doctor                    # core + zk + chain toolchain, bundled artifacts, config
TCL_RPC_URL=$RPC_URL tcl doctor -c chain --rpc   # + RPC reachability / key sanity
```

Release artifacts are signed. Verify before use:

```bash
cosign verify-blob --bundle tcl-0.1.0rc1-py3-none-any.whl.sigstore.json \
  --certificate-identity-regexp 'https://github.com/onnxscibroccoli/TCL-orc/.github/workflows/ci.yml@refs/tags/v.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com tcl-0.1.0rc1-py3-none-any.whl
gh attestation verify tcl-0.1.0rc1-py3-none-any.whl -R onnxscibroccoli/TCL-orc
cosign verify ghcr.io/onnxscibroccoli/tcl-orc:0.1.0rc1 \
  --certificate-identity-regexp 'https://github.com/onnxscibroccoli/TCL-orc/.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## 2. Trusted setup warning

`contracts/verifiers/StateVerifier.sol` and everything `tcl zk compile` produces come from a
**single-party development setup**. Whoever ran it can forge proofs and settle arbitrary
state roots. Before any non-dev deployment:

1. Use a public Powers-of-Tau (e.g. Hermez/perpetual ptau) of sufficient power.
2. Run a multi-party phase-2 ceremony on `StateTransition` (`snarkjs zkey contribute`, ≥ 3
   independent contributors, final random beacon), publish transcripts and hashes.
3. Regenerate the verifier from the final zkey and commit it:
   ```bash
   python -c "from tcl.zk import export_verifier; export_verifier('circuit_final.zkey', 'contracts/verifiers/StateVerifier.sol')"
   ```
4. Only then set `VERIFIER_CEREMONY_ATTESTED=true`.

## 3. Deploy

```bash
cp .env.example .env && $EDITOR .env      # RPC_URL, EXPECTED_CHAIN_ID, GENESIS_ROOT, FINAL_OWNER, ...
set -a; . ./.env; set +a
tcl ledger root --db ledger.db            # -> GENESIS_ROOT (decimal, < BN254 field)

# 1) simulate (no transactions)
forge script script/deploy/DeployEngine.s.sol:DeployEngine --rpc-url "$RPC_URL" --account deployer
# 2) broadcast + verify on the explorer in one go
forge script script/deploy/DeployEngine.s.sol:DeployEngine --rpc-url "$RPC_URL" --account deployer \
  --broadcast --verify --etherscan-api-key "$ETHERSCAN_API_KEY"
```

The script deploys `StateVerifier`, the `CirculatoryEngine` implementation and an
`ERC1967Proxy` that calls `initialize(...)` atomically (no front-runnable init), then sets the
settler and transfers ownership to `FINAL_OWNER`. It reverts (nothing is broadcast) if:
`EXPECTED_CHAIN_ID` ≠ chain id, `VERIFIER_CEREMONY_ATTESTED` ≠ true, `FINAL_OWNER` is unset or
not a contract (non-dev chains), or any post-condition fails (state root, verifier, owner,
EIP-1967 implementation slot). Addresses are logged and recorded in
`broadcast/DeployEngine.s.sol/<chainId>/run-latest.json` — archive that file (it is gitignored).

CI runs the same script against anvil on every PR (`scripts/deploy_dryrun.sh`, job `deploy-dryrun`).

## 4. Verify on the explorer

If `--verify` was skipped or failed:

```bash
forge verify-contract <VERIFIER>       contracts/verifiers/StateVerifier.sol:StateVerifier --chain $EXPECTED_CHAIN_ID
forge verify-contract <IMPLEMENTATION> contracts/CirculatoryEngine.sol:CirculatoryEngine --chain $EXPECTED_CHAIN_ID
forge verify-contract <PROXY> lib/openzeppelin-contracts/contracts/proxy/ERC1967/ERC1967Proxy.sol:ERC1967Proxy \
  --chain $EXPECTED_CHAIN_ID --constructor-args $(cast abi-encode "f(address,bytes)" <IMPLEMENTATION> <INIT_CALLDATA>)
```

Then mark the proxy as a proxy in the explorer UI and post-check:

```bash
cast call <PROXY> "owner()(address)"     --rpc-url $RPC_URL   # == FINAL_OWNER
cast call <PROXY> "stateRoot()(uint256)" --rpc-url $RPC_URL   # == GENESIS_ROOT
cast call <PROXY> "verifier()(address)"  --rpc-url $RPC_URL   # == VERIFIER
```

## 5. Run the settler / daemon

```bash
docker run -d --name tcl -p 127.0.0.1:8787:8787 \
  -e TCL_RPC_URL="$RPC_URL" -e TCL_DEPLOYMENT=/data/deployment.json \
  --env-file /run/secrets/tcl-settler.env ghcr.io/onnxscibroccoli/tcl-orc:0.1.0rc1
```

`/run/secrets/tcl-settler.env` holds `TCL_CHAIN_PRIVATE_KEY=` for the `SETTLER` address, injected by a secret manager (never baked into the image). `tcl chain`
refuses the public anvil key on non-dev chains.

Probes: `GET /livez` (liveness), `GET /readyz` (readiness, 503 when not ready),
`GET /health` (full status), `GET /version`. Kubernetes example:

```yaml
livenessProbe:  { httpGet: { path: /livez,  port: 8787 }, periodSeconds: 10 }
readinessProbe: { httpGet: { path: /readyz, port: 8787 }, periodSeconds: 5 }
```

The image runs as uid 10001. It is `linux/amd64` only (circom ships no arm64 binary).

## 6. Ownership transfer to the timelock

Preferred: set `FINAL_OWNER` at deploy time (done atomically by the script). To transfer later,
from the current owner:

```bash
cast send <PROXY> "transferOwnership(address)" <TIMELOCK> --rpc-url $RPC_URL --account deployer
```

`OwnableUpgradeable` transfers are **single-step and immediate** — double-check the address
(must be a contract you control on this chain). After transfer, all `onlyOwner` calls
(`pause`, `setSettler`, `redistribute`, `upgradeToAndCall`) go through the timelock's
schedule → wait → execute flow.

## 7. Upgrade and rollback

```bash
# dev chain / owner is an EOA: deploy new impl and upgrade
PROXY=<PROXY> EXECUTE=true forge script script/deploy/UpgradeEngine.s.sol:UpgradeEngine \
  --rpc-url $RPC_URL --account deployer --broadcast
# timelock-owned: deploy impl only, print calldata to schedule on the timelock (target = PROXY)
PROXY=<PROXY> forge script script/deploy/UpgradeEngine.s.sol:UpgradeEngine --rpc-url $RPC_URL --account deployer --broadcast
# rollback: point back at the previous implementation (no new deploy)
PROXY=<PROXY> IMPLEMENTATION=<PREVIOUS_IMPL> forge script script/deploy/UpgradeEngine.s.sol:UpgradeEngine ...
```

Before any upgrade: storage-layout diff (`forge inspect CirculatoryEngine storageLayout` old vs
new; only append), full test suite, testnet rehearsal. Emergency: the owner can `pause()`;
off-chain ledger state can be rolled back with `tcl ledger rollback --root <prior>`.

## 8. Release process (maintainers)

1. Bump `__version__` in `src/tcl/__init__.py`, add a `## [x.y.z]` section to `CHANGELOG.md`.
2. Merge to `main`, then `git tag v<version> && git push origin v<version>`.
3. CI gates on all jobs, checks tag == version and changelog, then publishes the GitHub release
   (wheel, sdist, SBOM, SHA256SUMS, sigstore bundles, provenance) and the signed GHCR image.
