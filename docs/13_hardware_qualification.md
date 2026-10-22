# Physical enrollment qualification evidence

`production/hardware.py` is an offline evidence validator. This repository contains no physical measurement dataset or qualified extractor implementation. It does not generate keys, implement BCH/Polar/MRR, or establish HDMA security. Roadmap error rates, entropy values, timing, area and reliability figures remain unverified until independently reviewed evidence exists. Ambiguous BCH parameter notation must be resolved against the exact implementation and source literature, not guessed.

Run:

```sh
python -m production.hardware --policy reviewed-policy.json --policy-sha256 EXTERNALLY_PINNED_CANONICAL_SHA256 --evidence private/evidence.json --root private-artifact-root
```

The pin is SHA256 of canonical JSON (sorted keys, compact separators, UTF-8, no NaN), independently obtained through release governance. An evidence-supplied pin alone is not a trust anchor. Python callers use `verify(policy, evidence, root)` and must enforce that external policy pin themselves. Exit zero means conditional acceptance only; exit one means blocked. No raw measurement bytes appear in the report.

The example policy intentionally cannot pass. Supply actual reviewed voltage bounds, six required strata (-15, 25, 80 °C at both voltage bounds), an extractor suite version and source artifact SHA256, and at least two distinct Ed25519 reviewer public keys. Repeating one public key under aliases cannot satisfy quorum. Hashes bind artifact bytes; they do not establish truth or provenance. Corner tests alone do not establish behavior at all intermediate temperatures or voltages.

Evidence JSON has `policy_sha256`, `purpose` equal to `physical-enrollment-qualification`, an exact copy of `extractor_suite`, artifact specifications for `experiment_metadata`, `independence_protocol`, and `helper_authentication_tests`, an `independence_declaration`, `strata`, and `approvals`. Every artifact specification has `path` relative to the private artifact root and `sha256`. Retain actual enrolled raw measurements under controlled access, never on public transaction logs. Each stratum has `temperature_c`, `voltage_v`, positive `device_count`, `enrolled_measurements` artifact, integer `independent_trials`, and integer `failures`. No strata may be omitted or substituted.

The independence declaration requires nonempty `device_population`, `sample_selection`, `independent_trial_definition`, and `correlation_handling` descriptions. Metadata and protocol should record enrollment, acquisition calibration, voltage and temperature tolerances, lots, device identifiers under access control, selection and exclusion rules, trial denominator and failure definition. Repeated reads of a device are not automatically independent. Reviewers must assess dependence and the experimental unit before approving reported trial counts. A signed count remains a reviewer assertion; the validator cannot infer physical independence from bytes.

Helper authentication evidence must contain the actual test results and reviewed test procedure covering the selected construction and threat model, including tampering, replay and binding to enrollment and suite version. A Boolean `HDMA=true` is not evidence. Reviewers must explicitly assess adequacy of these tests; the gate verifies bytes and signatures, not cryptographic attack resistance.

Approvals sign canonical JSON `{"domain":"TCL-hardware-qualification-v1","evidence": E}` where E is the entire evidence object excluding `approvals`. Each approval is `{"key_id":"policy-key-id","signature":"Ed25519-hex"}`. This binds protocol, population declaration, raw artifact hashes, helper authentication evidence, suite, policy and all strata to the review.

For each stratum the gate computes a one-sided exact binomial (Clopper-Pearson) upper confidence bound, conditional on independent identically distributed Bernoulli trials. Confidence must be at least 95%, and each bound must be strictly below 10^-6. With zero failures, the bound is `1 - (1-confidence)^(1/n)`; at 95%, at least 2,995,731 independent trials are needed per stratum. Six passing strata at 95% each do not constitute a simultaneous 95% family-wide statement; set a stronger per-stratum confidence in policy if that claim is required. Nonzero failures require more trials. Numerical bisection evaluates the binomial CDF in log space.

Tests use explicitly synthetic artifacts to exercise acceptance mechanics and rejection paths. They are not measurements or evidence of physical qualification. Even conditional acceptance relies on reviewers for provenance, independence, enrollment correctness and helper-data security. Release governance must review that limitation alongside the report.
