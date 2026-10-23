# 14 — `tcl localnet`: offline, EVM-free local testnet (replaces anvil)

Status: **MVP implemented** (`src/tcl/localnet/`, tests in `tests/test_localnet.py`).
Consensus inside the localnet is a **PLACEHOLDER** (round-robin proposer + >2/3 equal-weight
state-root quorum) until the sovereign-chain design (docs/13, docs/13a on `docs/sovereign-chain`)
lands. RPC names, tx/block shape, and reject codes follow **doc 13a v0**.

## 1. Goals
1. One command starts N local validators with a deterministic genesis.
2. Fully offline: no network egress, no third-party services, every runtime dependency in-repo.
3. Dev controls: mine on demand, time warp, snapshot/revert, faucet, fault injection, inspection.
4. A harness that exercises every TCL layer (ledger, notes, personhood, governance, ZK) natively, not
   through an EVM.
5. Fail-closed: dev-only controls are refused unless `--dev`; time never runs backwards except via
   revert; a failed round leaves no partial state.

## 2. CLI (implemented)
```
tcl localnet start [-n 4] [--port 8645] [--seed tcl-dev] [--dev/--no-dev] [--foreground] [--data-dir .tcl/localnet]
tcl localnet stop | status
tcl localnet mine [COUNT]
tcl localnet time advance SECONDS | time set UNIX_TS
tcl localnet snapshot            -> prints snapshot id
tcl localnet revert SNAPSHOT_ID
tcl localnet faucet ADDR AMOUNT
tcl localnet fault VALIDATOR_IDX offline|online|byzantine|honest
tcl localnet account ADDR | block HEIGHT
```
`start` spawns `python -m tcl.localnet` in the background, writes `node.json` (pid, url, socket)
into the data dir and prints `tcl_status`. State (per-validator SQLite in WAL mode, `blocks.jsonl`,
`clock.json`, `genesis.json`) persists across restarts; restarting with a different genesis
is refused.

## 3. Architecture
| Piece | File | Notes |
|---|---|---|
| Genesis | `genesis.py` | Seeded and deterministic: dev accounts `devN:<sha256>`, mock personhood tiers 1–3, mock note denominations 5–500, fixed genesis time 2026-01-01Z, chain id `tcl-localnet-1`. `Genesis.hash()` pins it. |
| Node | `node.py` | N `Validator`s, each with its **own** `tcl.ledger.Ledger` and SQLite DB. Every validator re-executes every block, and the block commits only when >2/3 of validators report the same Poseidon root. All validators' block transactions are held open and committed or rolled back together. |
| State machine | `Validator.apply_tx` | Module `circulatory`: `transfer`, `settle`, `redistribute`, `spend_nullifier`, `faucet` (dev). It uses the existing ledger transitions: lazy fixed-point demurrage to the vault, Poseidon tree, nullifiers. Each tx runs in a SAVEPOINT, so a reject reverts it fully and records the 13a reject code in the receipt. |
| Blocks | `node.py` | 13a header fields: `prev_hash`, SHA-256 `tx_root`, Poseidon `state_root`, `aux_roots` (zeros for now), `validators_hash`, `proposer`, `app_version`. `block_hash = H(header)` and `tx_id = H(tx without sig)`, using canonical JSON. |
| RPC | `rpc.py` | JSON-RPC 2.0 on `127.0.0.1` HTTP (`POST /`, `GET /health`), plus newline-delimited JSON-RPC on a 0600 Unix socket. All ledger access runs on one worker thread. |
| CLI | `cli.py` (`localnet` group) | Thin client over the RPC. |

RPC methods: `tcl_chainId`, `tcl_status`, `tcl_getBlock`, `tcl_getTx`, `tcl_sendTx`, `tcl_getAccount`,
`tcl_getProof`, `tcl_getValidators`, `tcl_getParams`, `tcl_verifyState`. The dev-only methods
`tcl_mine`, `tcl_warp`, `tcl_setTime`, `tcl_snapshot`, `tcl_revert`, `tcl_faucet`, `tcl_injectFault`
and `tcl_shutdown` return error `-32001` with `data.reject = "UNAUTHORIZED"` unless the node runs with `--dev`.
A tx reject returns `-32002` with `data.reject = <13a code>`. Localnet errors such as no quorum or a bad
snapshot return `-32003`. `tcl_setTime` and `tcl_shutdown` are localnet additions to 13a, proposed
upstream.

Faults: an `offline` validator skips rounds and catches up by replaying committed blocks when it
comes back `online`. Divergence while replaying is a hard error. A `byzantine` validator reports a
corrupted root. If that drops agreement below quorum, the round fails, every validator's state
rolls back, the clock rewinds and the txs return to the mempool. Planned additions: `partition`,
`delay`, `equivocate`, `crash-mid-commit`.

**Known MVP gaps:**
- No Ed25519 signatures, because Ed25519 isn't in the stdlib (see §6 D2). Dev txs are unsigned.
- Personhood tiers and notes are genesis mocks.
- No Groth16 verification in-node yet; the `zk` harness still shells out to snarkjs.
- `aux_roots` are zero.

## 4. Harness for all layers (plan)
`tcl.localnet` is importable as a library (`Localnet(tmp_path, Genesis(...))`), which makes it the
pytest fixture for every layer:
- **Ledger and demurrage:** implemented. The tests warp 30 days and check that vault decay applies
  only above threshold T.
- **Notes:** a native `notes` module ports `NoteController.sol` (mint burn, cost credit, reward,
  era nullifiers) as Python modules under the 13a Module API, with state-root parity tests against
  the Solidity fixtures until Solidity is retired.
- **Personhood, governance and bounty:** native modules against mock vouch graphs from genesis.
  Conviction voting is checked with `tcl_warp` across half-lives.
- **ZK:** an in-repo Groth16 verifier (§5, R1) exposed as `tcl_verifyProof`, using fixture proofs
  already committed under `test/contracts/fixtures`.
- **Consensus:** swap the placeholder for the 13 engine behind the same `Localnet` facade. The
  fault-injection tests are the acceptance suite.

## 5. Dependency inventory (current repo @3f66c6c) and decisions
Legend:
- **V** = vendor pinned with SHA-256 into the content-addressed store
- **R** = reimplement in-house (Apache-2.0)
- **B** = build/dev-time only, not needed by a running localnet
- **D** = drop once EVM is retired

| Dependency | Version | Used for | License | Decision |
|---|---|---|---|---|
| Python | ≥3.11 | everything | PSF | V: pinned interpreter in bundle (python-build-standalone tarball, hashed) |
| typer (+click, rich, shellingham, markdown-it-py, mdurl, pygments, annotated-doc) | 0.27.3 | CLI | MIT/BSD | V: wheelhouse. Localnet core doesn't need it (`python -m tcl.localnet`); R optional with argparse |
| pydantic / pydantic-core / pydantic-settings / annotated-types / typing-* / python-dotenv | 2.14.1 / 2.50.1 | config | MIT | V: wheelhouse (pydantic-core is a compiled wheel, so per-platform wheels). R candidate: config is simple enough for dataclasses |
| pyyaml | 6.0.3 | config | MIT | V: wheelhouse |
| pytest, hypothesis, iniconfig, pluggy, packaging, attrs, sortedcontainers | lock | tests | MIT/MPL-2.0 | B: wheelhouse (dev extra) |
| Poseidon (BN254) | in-repo `tcl.crypto.poseidon` | state commitments | Apache-2.0 | Already R. Constants JSON is in-repo and hashed |
| circom compiler | v2.1.9 | compile circuits | GPL-3.0 | B: vendor the source tarball plus a hashed prebuilt binary. Only needed when circuits change; compiled r1cs/wasm/zkey artifacts are content-addressed |
| circomspect | 0.9.0 | circuit lint | Apache/MIT | B |
| snarkjs | 0.7.6 | Groth16 setup and prove | GPL-3.0 | R: verifier (pairing on BN254 in pure Python, about 600 LOC, slow but fine for localnet) as `tcl.zk.groth16`. Prover: B (vendored npm tarball plus Node) until R |
| circomlib | 2.0.5 | Poseidon and comparator circuits | **GPL-3.0** | V: source in `circuits/vendor/` with hash and license kept. Circuits that include it are GPL-derived, see D4 |
| Node.js | 20 | runs snarkjs and solhint | MIT | B: hashed tarball in bundle; gone once the prover is R |
| solhint | 5.0.5 | Solidity lint | MIT | D |
| Foundry (forge, cast, anvil) | foundry.lock | EVM tests and deploy | MIT/Apache | D: replaced by `tcl localnet`. Kept B until the native modules reach parity |
| forge-std v1.9.4, OpenZeppelin (+upgradeable) v5.1.0 | git submodules, pinned rev | contracts | MIT | D, along with the EVM |
| solc | 0.8.24 | contracts | GPL-3.0 | D |
| Podman / Docker, compose | host | container image | Apache | B: optional packaging. The localnet needs no container |
| git | host | worktrees (orchestrator) | GPL-2.0 | B: host tool, not linked |
| sqlite3 | stdlib | storage | public domain | Already stdlib |

The localnet itself (`src/tcl/localnet/*`) imports **only the stdlib and `tcl`**, enforced by
`test_localnet_stdlib_only`. A running localnet therefore needs only Python plus the `tcl` package's
own runtime wheels, and not even those via `python -m tcl.localnet`.

### Offline bundle (plan)
```
vendor/
  store/sha256/<hex>          # content-addressed blobs (wheels, tarballs, binaries, zkeys)
  manifest.json               # name, version, platform, sha256, license, source URL (provenance only)
  wheelhouse -> store links   # `pip install --no-index --find-links vendor/wheelhouse tcl`
  licenses/                   # full texts; GPL items listed separately
```
- `scripts/bundle.py fetch` runs **online, once, by a maintainer**. It downloads the manifest entries
  and verifies the hashes.
- `scripts/bundle.py verify` runs offline in CI and fails closed on any hash mismatch.
- `tcl doctor --offline` checks that every manifest entry is present.
- Large blobs go to Git LFS or a release asset, mirrored by hash. Which one is decision D3.

### Offline proof
- `test_rpc_end_to_end_offline` patches `socket.connect` and `create_connection` to refuse any
  non-loopback address. It asserts that external connects fail, runs the whole RPC flow (faucet, warp,
  mine, snapshot, revert, proofs, Unix socket) and asserts that no external connection was attempted.
- A planned CI job runs the full localnet suite in `unshare -rn` (no network namespace) with
  `pip install --no-index` from the bundle.

## 6. Open decisions (owner)
- **D1 Consensus:** the placeholder stays until doc 13's engine lands. Should the localnet always run
  the real engine with N in-process validators, or keep the fast placeholder mode as an option?
- **D2 Signatures:** Ed25519 per 13a. Pure-Python R (RFC 8032 reference, about 150 LOC, slow but
  auditable) or vendor PyNaCl/libsodium (a compiled wheel)? Recommendation: R for the localnet, with an
  optional accelerated backend.
- **D3 Where big blobs live** (zkeys, toolchain binaries): Git LFS, release assets, or in-tree?
- **D4 GPL:** circomlib, snarkjs and circom are GPL-3.0, and circuits including circomlib are
  GPL-derived. Options: (a) accept GPL for `circuits/`, keeping the Apache-2.0 core separate;
  (b) R Poseidon and comparator templates clean-room under Apache-2.0.
- **D5 Groth16 trust:** an in-repo verifier still needs the public multi-party setup. The localnet uses
  dev zkeys, clearly labelled.
- **D6 Platforms** for compiled wheels in the bundle: linux-x86_64 only, or arm64 and macOS too?
