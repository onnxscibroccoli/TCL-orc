# TCL Security Model

> **In plain words.** This page lists what could go wrong and what protects against it. TCL is still an experiment. **No outside expert has reviewed it (no audit).** The secret setup for its proofs was made by one developer, so whoever holds it could fake proofs; it is for testing only. The check that each person is one real human is a stand-in (*mock*). The owner of the contracts is a single key. Do not put real money into it. Terms are in the [Glossary](GLOSSARY.md).
>
> **What this means for you:** treat every TCL coin today as play money. The "Known limitations" section says exactly what must be fixed first.

Status: the research and pre-production stage, with no external audit.
Don't deploy to a network that holds real value.

## Threat model

| Asset | Threat | Mitigation | Evidence |
| --- | --- | --- | --- |
| Off-chain ledger state | Crash or partial write | SQLite WAL transactions; leaf-to-root updates are atomic; a root journal allows rollback | `tests/test_crash_recovery.py` (SIGKILL) |
| Off-chain state transitions | Forged decay or balance | Groth16 proof of `StateTransition(depth)` | `tests/test_zk_prover.py` |
| On-chain funds | Reentrancy | `nonReentrant` on every mutating entry point; CEI; `withdraw` is the only ETH transfer and comes last | `test_ReentrancyBlocked` |
| On-chain funds | Accounting drift | Invariant `balance == totalBalances + vault` | `invariant_Conservation`, `invariant_LazyConservation` |
| Settlement | Proof replay or fork | `pubSignals[2]` must equal the current `stateRoot`; `newTime` must be strictly increasing and not in the future | `test_ReplayRejected`, `test_SettleEpochChecks` |
| Settlement | Non-canonical public inputs | Every signal must be below the BN254 field (checked in the engine and in the verifier) | `test_SettleEpochChecks` |
| Admin | Unauthorized upgrade, pause or redistribution | `OwnableUpgradeable`; `_authorizeUpgrade` is `onlyOwner`; the implementation constructor calls `_disableInitializers` | `test_AccessControl`, `test_InitializeOnce` |
| Agents | Runaway tasks | Subprocess timeouts and rlimits (Phase 2) | `tests/test_orchestrator*` (PR #3) |

## Circuit constraint guarantees

- All signals are assigned with `<==` or constrained with `===`.
  The only `<--` hints are `DivConst.q` and `DivConst.r`, bound by `num === q*den + r` and the range checks `0 <= r <= den-1` and `q < 2^64` or `2^128`.
  Bounds: `balance < 2^128`, `dt <= 604800`, and every product stays below 2^200, far below the field, so modular wrap is impossible.
- Path bits are boolean (`bit*(bit-1) === 0`).
  Old and new account leaves share the same path, so step 1 is a single-leaf update.
- **Full settle (account + vault):** the circuit proves both leaf writes of `Ledger.settle`, sequentially: `oldRoot -> midRoot` (account leaf) and `midRoot -> newRoot` (vault leaf += fee, time = newTime; unchanged when fee = 0, matching the ledger). The public `newRoot` therefore equals `Ledger.root()` after settle, which `build_transition_input` and `prove_settlement` check fail-closed.
- **Vault binding:** the vault lives at the reserved leaf index 0 (`Ledger` reserves it on fresh stores; the prover refuses stores where it does not). The circuit hard-codes `VAULT_ID = hash_account_id("__vault__")` (pinned by `tests/test_circuit_hygiene.py`) and vault path bits = 0, and requires the account's `accountId != VAULT_ID` and index != 0, so the two updates can never alias. An absent vault (first fee) is proven as the empty leaf 0 with `vaultExists = 0` forcing balance = time = 0; vault balances are range-checked to 128 bits before and after adding the fee.
- Taylor-term non-negativity and `fee <= surplus` are enforced with `Num2Bits` range checks.
- CI fails closed: the `zk` job sets `TCL_REQUIRE_ZK=1`, so a missing toolchain fails instead of skipping.
  The `lint` job runs circomspect and fails on any finding other than CS0005, and `tests/test_circuit_hygiene.py` proves those CS0005 hints are bound.
- Tamper tests: a wrong balance, time, account, root or sibling, a wrong vault balance, vault time, vault-exists flag or vault sibling, an account placed at the vault index, a non-boolean path bit, `dt` above 7 days or below 0, any changed public signal, or a corrupted proof point each fail at witness generation or at verification.

## Known limitations

1. **Trusted setup:** the ptau and zkey are DEV ONLY (local, single party). Use a public Perpetual Powers of Tau file and an MPC phase 2 before production.
2. **Vault leaf: closed.** The proof now covers both the account and the vault leaf update (see above). Remaining scope: other ledger writes (`deposit`, `redistribute`) are not proven, so a deployment's on-chain root only tracks the ledger across consecutive *settlements*; any unproven write in between makes the next `settleEpoch` fail closed with `StaleRoot`. Legacy stores whose vault is not at leaf 0 cannot be proven.
3. **Multi-step decay:** gaps longer than 7 days need several proofs, one per Taylor step. `build_transition_input` refuses longer gaps.
4. **On-chain and off-chain decay differ:** the contract's per-second linear decay on deposits isn't the same function as the off-chain Taylor rule. The two are separate ledgers, linked only through `settleEpoch`.
5. **Settler trust:** `settleEpoch` is permissioned (settler or owner). Proofs bind the state, but the settler controls liveness.
6. **Owner key:** a single `Ownable` owner. Use a multisig or timelock in production.

7. **Consecutive epochs: closed.** After each `settleEpoch` the on-chain `stateRoot` equals the ledger root, so consecutive settlements chain on one deployment. Tested by `CirculatoryEngineChainTest` (Foundry, chained real-proof fixture `test/contracts/fixtures/state_transition_chain.json`, incl. a fee = 0 step and out-of-order rejection) and `test/integration/test_multi_epoch.py` (5 settlements on anvil, on-chain root == ledger root after each, replay of the last proof rejected).

## Delivery-layer controls (Phase 4)

- **Dev key:** `chain_private_key` defaults to anvil's public account #0 key so local runs work out of the box. It is only safe against local dev chains; set `TCL_CHAIN_PRIVATE_KEY` from a secret store for anything else, and never put it in `config.yaml`.
- **HTTP /health** is read-only (GET only, no task submission) and binds to `127.0.0.1` by default; the container binds `0.0.0.0` but compose publishes it on `127.0.0.1:8787` only. Task submission stays on the `0600` Unix socket.
- **Replay:** re-sending a settled proof reverts with `StaleRoot` (tested in `test/integration/test_cli_zk_chain.py`); a tampered public signal fails `tcl zk verify`.
- **Verifier/zkey binding:** `tcl chain deploy` exports the verifier from the same zkey that `tcl zk prove` uses, and checks the deployed `stateRoot` equals the ledger root before writing `deployment.json`.
- **Container:** non-root UID 10001; the podman CI job runs it rootless with `--userns=keep-id`.

## Deployment-readiness security pass (2026-10-23)

### Static analysis (Slither 0.11.3)

CI job `slither` runs `slither .` with `slither.config.json` and fails on any medium or high finding.
Generated code (`contracts/verifiers/`), `lib/` and `test/` are filtered.

| Finding | Severity | Location | Status |
| --- | --- | --- | --- |
| `incorrect-return` in `checkPairing` / `verifyProof` | High (tool) | `contracts/verifiers/StateVerifier.sol` | **False positive.** snarkjs Groth16 template; the Yul `return(0, 0x20)` is the intended early exit with `false`. Covered by `test_TamperedProofRejected` and `test_RejectingVerifierFailsClosed`. Path filtered. |
| `incorrect-equality` `dt == 0` | Medium (tool) | `CirculatoryEngine._fee` | **Justified.** `dt` is elapsed seconds, not a balance; equality is a fast path. Inline `slither-disable-next-line`. |
| `missing-zero-check` on `owner_` → `settler` | Low | `CirculatoryEngine.initialize` | **Fixed.** Explicit `ZeroAddress` revert (OZ also rejects zero owner). Test: `test_InitializeRejectsBadParams`. |
| `timestamp` comparisons | Low | `CirculatoryEngine` | **Justified.** Decay is time-based by design; miner skew of seconds changes fees by `rate * skew * surplus`, negligible. Detector excluded. |
| `low-level-calls` | Info | `withdraw` | **Justified.** Single ETH transfer, last statement, `nonReentrant`, result checked. |
| `assembly`, `naming-convention`, `solc-version`, `missing-inheritance` | Info | verifier | Generated code. |
| `weak-prng` `dt % h` | Medium (tool) | `ConvictionVoting.decayFactor` | **False positive.** Modulo splits elapsed time into whole half-lives and a remainder; no randomness is derived. Inline disable. |
| `divide-before-multiply` | Medium (tool) | `ConvictionVoting.decayFactor` | **Justified.** WAD fixed-point Taylor series for `exp(-y)`; each term is rescaled by design, error < 1e-15 per term, covered by voting decay tests. Inline disable. |
| `incorrect-equality` (`t == 0`, `dt == 0`, `lastChangeCycle == cycle`, `windowId == now/window`) | Medium (tool) | `BountyModule.withdrawBond`, `ConvictionVoting.convictionOf`, `ParameterRegistry.setParameter`, `TieredPersonhoodLimits.remaining` | **Justified.** Sentinel/counter/epoch-id comparisons, not balance checks; cannot be manipulated by sending tokens. Inline disable. |
| `unused-return` `ECDSA.tryRecover` | Medium (tool) | `DoubleDepositVerifier._signedBy`, `InvalidProofVerifier.verify` | **Justified.** Only the third (error-argument) value is discarded; the recovered address and error code are both checked. Inline disable. |
| `incorrect-equality` (`amt == 0`, `now/epochLength == currentEpoch`) | Medium (tool) | `NoteController._release`, `NoteController.quote` | **Justified.** Zero-amount no-op early return and epoch-id comparison; neither gates value on an externally inflatable balance. Inline disable. |
| `constable-states` | Optimization | `PhysicalMintController` | **Resolved** — contract deleted (see below). |

**Manual finding (High) — RESOLVED:** `contracts/PhysicalMintController.sol` was a skeleton
(`lockNote` had no access control or value transfer; `redeemNote` accepted any proof).
It has been **deleted** in PR #11 and superseded by `contracts/physical/NoteController.sol`,
which verifies Groth16 note-deposit proofs (`NoteDepositVerifier`), burns/mints against `CirculatoryEngine`,
and is covered by the NoteController unit, fuzz and invariant tests and by the `slither` CI gate.

### Test inventory

| Area | Tests | Notes |
| --- | --- | --- |
| Upgrade safety | `test_UpgradeByNonOwnerReverts`, `test_UpgradeToNonUUPSReverts`, `test_ImplementationCannotBeInitialized`, `test_ImplementationUpgradeDirectReverts`, `test_UpgradePreservesAllState` | `_authorizeUpgrade` is `onlyOwner` |
| Storage layout | `scripts/security/check_storage_layout.py` vs `docs/storage-layout/CirculatoryEngine.json` | CI fails if any existing slot changes label, slot, offset or type; appends allowed. Update with `--update` only in a reviewed PR. |
| Pause | `test_PauseBlocksEveryUserPath`, `test_PauseUnpauseOnlyOwner`, `test_PausedDecayStillAccruesButFundsIntact` | deposit, withdraw, settle, settleEpoch blocked |
| Reentrancy | `test_ReenterDepositFromWithdrawBlocked`, `test_ReenterWithdrawBlocked`, `test_ReenterSettleHarmless` | |
| Settlement | `test_RejectingVerifierFailsClosed`, `test_SettleEpochNonSettlerReverts`, `testFuzz_SettleEpochOutOfFieldRejected` | |
| Invariants | `FullInvariantTest`: ETH conservation, ghost conservation (`deposited == withdrawn + balances + vault`), sum of balances, root tracking | handler covers deposit, withdraw, warp, settle, redistribute, settleEpoch, pause toggle; 512 runs × depth 128 |
| Python properties | `tests/test_properties.py` (hypothesis) | demurrage conservation/bounds/monotonicity/rate accuracy, ledger conservation and Merkle integrity over random op sequences, tree incremental = rebuild, tamper rejection, field bounds, nullifier double-spend |
| Circuit negatives | `tests/test_zk_prover.py` | every input has a negative test (static check `test_every_circuit_input_has_negative_test`), every Merkle path position tampered, 2^128 range checks |

### CI security jobs

- `slither`: static analysis, fail on medium+.
- `forge-security`: storage layout check, high-run fuzz/invariant suite (fixed seed), gas snapshot `--check --tolerance 2` against `.gas-snapshot`, coverage gate (CirculatoryEngine lines ≥ 95%).
- `dependency-audit`: `pip-audit` over pinned `requirements-test.lock`, `npm audit --audit-level=high`, `npm ci` lockfile check, submodules pinned to commits.

Foundry is pinned to v1.8.6 in these jobs so gas numbers are reproducible.
`npm audit fix` removed a high-severity `underscore` advisory (GHSA-qpx9-hpmf-5gmw) pulled in by snarkjs → bfj → jsonpath.

### Threat matrix status

| Threat | Status |
| --- | --- |
| Reentrancy on funds | Mitigated, tested |
| Accounting drift | Mitigated, invariant-tested |
| Unauthorized upgrade / storage corruption on upgrade | Mitigated, tested, layout-gated in CI |
| Proof replay, fork, non-canonical inputs | Mitigated, tested |
| Under-constrained circuit | Mitigated; negative-test completeness enforced |
| Vulnerable dependencies | Gated in CI |
| Single owner key | **Open**: move owner to a multisig + timelock before mainnet (governance workstream) |
| Dev trusted setup | **Open**: MPC phase 2 required before mainnet |
| Physical-note controller | **Open (High)**: skeleton, must not be deployed |
| External audit | **Open** |

### Pre-deployment audit checklist

- [x] Slither clean at medium+ with every exclusion justified above
- [x] Invariant, fuzz, pause, reentrancy and upgrade tests green
- [x] Storage layout snapshot committed and gated
- [x] Circuit negative tests cover every input
- [x] Dependencies audited and pinned (npm lockfile, Python lock, submodule commits)
- [ ] Owner transferred to multisig + timelock; `setSettler` set to a dedicated key
- [ ] Production trusted setup (Perpetual Powers of Tau + MPC phase 2); verifier regenerated from the final zkey
- [x] `PhysicalMintController` removed (superseded by `NoteController`)
- [ ] Verify deployed bytecode against the tagged release; record proxy and implementation addresses
- [ ] Confirm `initialize` was called atomically with proxy deployment (no front-run window)
- [ ] External audit and a bug bounty

## Reporting a vulnerability

Open a private GitHub Security Advisory on `onnxscibroccoli/TCL-orc` (Security → Report a vulnerability).
Please don't open public issues for security bugs.
