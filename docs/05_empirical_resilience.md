# Empirical resilience acceptance

The Python demurrage reference is tested directly against deployed local-EVM
settlement, including exact integer rounding and carried fee remainders.
It is not a substitute ledger or a proof of Solidity correctness.
The earlier linear helper was incompatible with compounding and has been replaced.
The circuit and Solidity demurrage mechanics remain unchanged in this iteration.

`Containerfile` builds a dependency-complete test image from a committed source
archive and a digest-pinned base image. Actual rootless Docker executes the full
protocol check offline under a nonroot user, read-only root, no capabilities and
CPU/memory/PID limits. In-container probes verify effective restrictions.
Nonroot `USER` metadata alone does not establish rootless daemon execution.

The source `provenance.json` is a checked checksum inventory, not immutable
storage or execution evidence. Missing files or stale bytes fail verification.
GitHub's `rootless-protocol-validation` artifact separately records source archive
and image identities, runtime inspection, logs, generated proof/compiler artifacts
and the exact exported image for offline replay. It uses synthetic ceremony
parameters and does not certify an independently operated production ceremony.

Governance authorization, timelock and incident controls remain mandatory and
are covered by local-EVM regression tests inside the same containerized check.
Abstract symbolic results are supplemental; they cannot replace runtime evidence.
No production deployment is authorized by these tests.
