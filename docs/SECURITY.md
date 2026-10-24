# TCL Security Model

> **In plain words.** This page lists what could go wrong and what protects against it. TCL is still an experiment. **No outside expert has reviewed it (no audit).** The secret setup for its proofs was made by one developer, so whoever holds it could fake proofs; it is for testing only. The check that each person is one real human is a stand-in (*mock*). The owner of the contracts is a single key. Do not put real money into it. Terms are in the [Glossary](GLOSSARY.md).
>
> **What this means for you:** treat every TCL coin today as play money. The "Known limitations" section says exactly what must be fixed first.

**Status:** research and pre-production. There has been no outside audit. Do not install it on any network that holds real value.

## Threat model

**In plain words.** A *threat model* is a list of things worth protecting, what could attack them, and what stops the attack. Each row below has four parts:

- **Asset:** the thing being protected.
- **Threat:** what could go wrong.
- **Mitigation:** what the code does about it.
- **Evidence:** the test that proves the protection works.

Some words in the table:

- *Off-chain ledger state* is TCL's own account book. A *partial write* is a change that stops halfway, for example in a crash. *Atomic* means all-or-nothing. The *root journal* is an undo log of past fingerprints (*roots*) of the book.
- *Forged decay* means someone lying about how much decay was charged. A *Groth16 proof* is a zero-knowledge proof that the math was done right.
- *Reentrancy* is a trick where a malicious contract calls back in before the first call finishes, to take money twice. *CEI* (checks-effects-interactions) means: check inputs, update records, and only then send money.
- *Accounting drift* means the records slowly stop matching the real money.
- *Proof replay* means sending an old proof again. A *fork* is a second, competing history. Each proof must start from the current root, so neither works.
- *Non-canonical inputs* are numbers written in a sneaky second form. All numbers must be smaller than the BN254 field size, so each number has only one form.
- *Admin* threats are someone taking over upgrades, pausing or payouts. Only the owner may do these, and the bare code can't be set up twice (`_disableInitializers`).
- *Runaway tasks* are jobs that never end or eat the machine. They get time limits and resource limits (*rlimits*).

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

**In plain words.** A proof *circuit* is a list of equations. A proof is only as safe as those equations. If one value is not tied down by an equation, a cheater could pick any value for it. This section explains, step by step, why every value is tied down.

- **Every value is tied down.** Each value (*signal*) is set with `<==` or checked with `===`. Both make real equations.
  - The only exceptions are two *hints* written with `<--`: `DivConst.q` and `DivConst.r`. They are the answer and remainder of a division, supplied by the prover.
  - Both hints are then forced by the equation `num === q*den + r`. *Range checks* (proofs that a number sits inside a range) add `0 <= r <= den-1` and `q < 2^64` or `2^128`. Together these allow only the one correct answer.
- **Numbers stay small, so nothing wraps around.** Proof math works like a clock: numbers wrap around at a huge prime. A cheater could try to exploit that. But balances are below `2^128`, the time step `dt` is at most 604,800 seconds (7 days), and every product stays below 2^200. That is far below the wrap point (about 2^254), so wrapping can't happen.
- **Path bits are 0 or 1.** The left/right choices on the way up the tree must be *boolean* (0 or 1). The equation `bit*(bit-1) === 0` forces this, because it only holds for 0 and 1.
  The old and new account leaves use the same path. So step 1 changes exactly one leaf.
- **Full settle (account + vault).** The circuit proves both writes of `Ledger.settle`, one after the other:
  1. `oldRoot -> midRoot`: the account leaf is updated.
  2. `midRoot -> newRoot`: the vault leaf gets `+= fee` and its time becomes `newTime`. If fee = 0 it stays the same, just like the ledger.

  So the public `newRoot` equals `Ledger.root()` after settling. `build_transition_input` and `prove_settlement` check this and refuse if it fails (*fail-closed*).
- **The vault is pinned in place.** The vault always lives at reserved slot 0.
  - `Ledger` reserves slot 0 in new account books. The prover refuses books where it does not.
  - The circuit has the vault's ID built in: `VAULT_ID = hash_account_id("__vault__")`. `tests/test_circuit_hygiene.py` checks this value. The vault's path bits are all 0.
  - The account being settled must have `accountId != VAULT_ID` and slot != 0. So the two updates can never point at the same leaf (*alias*).
  - Before the first fee, the vault doesn't exist yet. That case is proven as an empty slot 0 with `vaultExists = 0`, which forces balance = time = 0.
  - The vault balance is range-checked to 128 bits before and after adding the fee.
- **No negative terms, no over-charging.** The Taylor terms can't go negative, and `fee <= surplus`. Both are enforced by `Num2Bits` range checks.
- **The robots fail closed.** In CI (the automatic checks on every change):
  - The `zk` job sets `TCL_REQUIRE_ZK=1`. If the proof tools are missing, the job fails instead of skipping.
  - The `lint` job runs circomspect (a circuit checker) and fails on any finding except CS0005. `tests/test_circuit_hygiene.py` proves the CS0005 hints are tied down.
- **Tamper tests.** Each of these changes makes proof-building or checking fail:
  - a wrong balance, time, account, root or sibling;
  - a wrong vault balance, vault time, vault-exists flag or vault sibling;
  - an account placed at the vault's slot;
  - a path bit that is not 0 or 1;
  - `dt` above 7 days or below 0;
  - any changed public value;
  - a damaged proof point.

## Known limitations

**In plain words.** These are the known weak spots. "Closed" means fixed. The rest must be fixed before real use.

1. **Trusted setup (open).** Proofs need secret keys made in a one-time *setup*. Whoever keeps the leftover secret could fake proofs. TCL's setup files (the *ptau* and *zkey*) are DEV ONLY: one person made them on one machine. Before real use, switch to the public Perpetual Powers of Tau file and an *MPC phase 2*. MPC (multi-party computation) means many people each add randomness. The setup is safe if even one of them is honest.
2. **Vault leaf (closed, with limits).** The proof now covers both the account update and the vault update (see above).
   - Still not proven: other ledger writes, `deposit` and `redistribute`.
   - So the on-chain root only follows the ledger across back-to-back *settlements* (recorded proofs).
   - Any unproven write in between makes the next `settleEpoch` fail closed with `StaleRoot` ("out of date").
   - Old account books whose vault is not at slot 0 cannot be proven.
3. **Multi-step decay (open).** A proof covers at most 7 days. A longer gap needs several proofs, one per 7-day Taylor step. `build_transition_input` refuses longer gaps.
4. **On-chain and off-chain decay differ (by design).** The contract charges a straight-line fee per second on deposits. The off-chain book uses the Taylor rule. These are different formulas. The two books are separate and are linked only through `settleEpoch`.
5. **Settler trust (open).** Only the *settler* or the owner may call `settleEpoch`. Proofs stop them from lying about the state. But they control *liveness*: whether updates get recorded at all.
6. **Owner key (open).** One single key owns the contract. For real use, the owner should be a *multisig* (needs several people to approve) or a *timelock* (forces a waiting period before changes).
7. **Back-to-back epochs (closed).** After each `settleEpoch`, the on-chain `stateRoot` equals the ledger root. So settlements chain one after another on one deployment. Two tests prove it:
   - `CirculatoryEngineChainTest` (Foundry) uses a chain of real proofs, `test/contracts/fixtures/state_transition_chain.json`. It includes a fee = 0 step and checks an out-of-order proof is rejected.
   - `test/integration/test_multi_epoch.py` records 5 settlements on anvil (a practice chain). After each, the on-chain root equals the ledger root. Replaying the last proof is rejected.

## Delivery-layer controls (Phase 4)

**In plain words.** These are protections in the tools people run.

- **Dev key.** A *private key* is the secret that controls a blockchain account. By default, `chain_private_key` is anvil's account #0 key. That key is public on purpose, so local tests work right away. It is only safe on a local practice chain. For anything else, set `TCL_CHAIN_PRIVATE_KEY` from a secret store. Never put it in `config.yaml`.
- **HTTP /health.** This web check is read-only. It only answers GET requests and can't accept tasks. By default it listens only on `127.0.0.1`, which means "this computer only." Inside the container it listens on `0.0.0.0` (all connections), but compose only exposes it on `127.0.0.1:8787`. Tasks can only be sent over the private Unix socket (mode `0600`, owner only).
- **Replay.** Sending a proof that was already recorded fails with `StaleRoot`. This is tested in `test/integration/test_cli_zk_chain.py`. A changed public value fails `tcl zk verify`.
- **The checker matches the keys.** `tcl chain deploy` builds the on-chain checker from the same zkey that `tcl zk prove` uses. Before writing `deployment.json`, it checks the installed `stateRoot` equals the ledger root.
- **Container.** It runs as a normal user (UID 10001), not as admin (*root*). The podman CI job runs it *rootless* with `--userns=keep-id`.

## Deployment-readiness security pass (2026-10-23)

### Static analysis (Slither 0.11.3)

**In plain words.** *Static analysis* means a tool reads the code, without running it, and flags patterns that are often bugs. TCL uses *Slither*. Many flags turn out to be harmless. Each one below is marked:

- **Fixed:** it was a real problem and is now fixed.
- **Justified:** the pattern is there on purpose and is safe. The reason is given.
- **False positive:** the tool was wrong.
- **Resolved:** the code was removed.

The CI job `slither` runs `slither .` with `slither.config.json`. It fails on any medium or high finding. It skips generated code (`contracts/verifiers/`), `lib/` and `test/`.

Some words in the table: *Yul* is a low-level language inside Solidity. *WAD* is a number format where 10^18 stands for 1. A *sentinel* is a special marker value like 0. *ECDSA* is the standard signature method. A *front-run* is when someone sees your pending transaction and jumps ahead of it.

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

**Manual finding (High) — RESOLVED.** A person reviewing the code found this one.

- `contracts/PhysicalMintController.sol` was an unfinished draft (a *skeleton*). Anyone could call `lockNote`, and it moved no money. `redeemNote` accepted any proof at all.
- It was **deleted** in PR #11. `contracts/physical/NoteController.sol` replaces it.
- The new contract checks Groth16 note-deposit proofs (`NoteDepositVerifier`). It burns and mints against `CirculatoryEngine`.
- It is covered by NoteController unit, fuzz and invariant tests, and by the `slither` CI gate. It has **not** had an outside audit, and its proof setup is dev-only.

### Test inventory

**In plain words.** This table lists the safety tests by area. Some test types: *fuzz* tests throw thousands of random inputs at the code. *Invariant* tests check a rule stays true after every random action. A *ghost* variable is a side record the test keeps to compare against. *Property tests* (with the tool hypothesis) check a rule for many random cases. *Negative tests* feed in bad data and expect a refusal. The *storage layout* is where each value sits in the contract's memory; an upgrade must not move them, or data would be scrambled.

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

- `slither`: static analysis. Fails on medium or worse.
- `forge-security` does four things:
  1. Checks the storage layout has not shifted.
  2. Runs a long fuzz and invariant suite with a fixed *seed* (starting point for the random numbers), so runs can be repeated.
  3. Compares *gas* use (the fee a blockchain charges for work) with `.gas-snapshot`, using `--check --tolerance 2`.
  4. Requires *coverage* of at least 95% of `CirculatoryEngine` lines, meaning tests run at least 95% of its lines.
- `dependency-audit` checks outside code TCL relies on (*dependencies*) for known security holes:
  - `pip-audit` over the pinned `requirements-test.lock`;
  - `npm audit --audit-level=high`;
  - an `npm ci` lockfile check;
  - submodules pinned to exact commits.

Foundry is pinned to v1.8.6 in these jobs, so gas numbers come out the same each time.
`npm audit fix` removed a high-severity warning in `underscore` (GHSA-qpx9-hpmf-5gmw). It came in through snarkjs → bfj → jsonpath.

### Threat matrix status

**In plain words.** *Mitigated* means there is a protection and a test for it. *Open* means not fixed yet.

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
| Physical-note controller | Old skeleton **deleted** (PR #11). Its replacement, `NoteController`, is tested but unaudited, uses a dev-only setup and simulated chips |
| External audit | **Open** |

### Pre-deployment audit checklist

**In plain words.** Every box must be ticked before TCL goes on a real network. Ticked boxes are done; empty ones are not.

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

Found a security hole? Report it privately, so it can be fixed before anyone abuses it.

1. Go to `onnxscibroccoli/TCL-orc` on GitHub.
2. Click Security → Report a vulnerability. This opens a private *Security Advisory*.

Please don't open a public issue for security bugs.
