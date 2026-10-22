# Isolated production execution

`agents/orchestrator.py` is the development path: commands have caller authority.
`production/executor.py` runs signed sequential tasks using a **rootless Docker daemon**.
It rejects rootful daemons. Docker itself and the daemon host remain trusted; this is
process isolation, not a VM security boundary.

Provision Python 3.12 or later, the executor and `cryptography` on a dedicated host, with a rootless Docker
context and resource controllers enabled. Keep its trust policy, plan envelope,
ledger, signing keys, and output outside the source checkout, writable only by the
trusted deployment operator. Do not use policy or signing keys supplied by a PR.
The example policy deliberately contains invalid placeholders. Approve a real
prebuilt OCI image digest from your registry; the executor never invents a digest
or builds/pulls an image. Load that exact image into the rootless daemon first.
The optional Dockerfile requires an explicitly pinned base image at build time;
building it alone does not authorize the resulting image.

## Signed input

Serialize the `plan` with Python `json.dumps(plan, sort_keys=True,
separators=(',', ':'), allow_nan=False).encode()`, sign those bytes with Ed25519,
with the domain prefix `b'TCL-EXECUTION-v1\n'` prepended before signing,
and supply `{ "key_id": "release", "plan": PLAN, "signature": BASE64_SIGNATURE }`.
The external policy maps key IDs to base64 raw 32-byte public keys. The plan is:

```json
{
  "version": 1,
  "source_sha256": "64 lowercase hexadecimal characters",
  "image": "registry/repository@sha256:64_lowercase_hex_characters",
  "resources": {"memory_mb": 128, "cpus": 1, "pids": 32, "tmpfs_mb": 16},
  "tasks": [{"id": "check", "command": ["python3", "-V"], "timeout": 30}]
}
```

Compute `source_sha256` over the exact bytes of `git archive --format=tar HEAD`.
The executor requires a clean repository root, rejects untracked files (including
ignored files), submodules and symlinks, and snapshots Git object content into a
private temporary directory. It binds only that verified snapshot read-only.
Execute from a dedicated clean clone; development dependency directories will
cause rejection. Tasks accept a signed `depends` array of task IDs. Dependencies must exist and
form an acyclic graph. Tasks run sequentially in topological order, stop executing
on failure, and record all remaining tasks as blocked. There are no retries.
The policy permits exact argv arrays and sets per-task and total time budgets,
task count, memory, CPU, PID and tmpfs ceilings. No plan environment, alternate
mounts, working directory, or Docker options are accepted.

```sh
python3 production/executor.py --plan /trusted/plan.json \
  --policy /trusted/policy.json --source /clean/source \
  --ledger /trusted/state.sqlite
```

The container runs UID/GID 65532, with all capabilities dropped,
no-new-privileges, read-only root/source, no network or IPC, no Docker socket,
and bounded PID, memory/swap, CPU and `/tmp` space. Images declaring volumes
are rejected to avoid implicit writable persistent storage. Image ENTRYPOINT is overridden
with the approved command. The rootless daemon must support the requested limits;
run the integration checks below on the actual deployment host.

## Secrets, state and provenance

Plans may contain `"secrets": ["credential"]` on a task. Only names listed in the
external policy's `secrets` mapping are accepted. Values there are absolute paths
to externally injected regular files readable by UID 65532; files mount read-only
at `/run/secrets/credential`. Provision permissions through your secret manager.
Never put secret values in plans, command arguments, environment variables or images.
The operator must ensure allowlisted commands do not copy secrets to external side
effects. Container stdout/stderr are discarded, with Docker logging disabled;
there are no output files or output hashes that could disclose secret output.

A locked SQLite ledger uses full synchronous durability and a unique identity over
the **canonical signed plan, decoded signing public key, and normalized external
policy** plus task ID. The policy key map is normalized to a set of decoded public
keys, so key aliases and alternate signature presentations cannot permit replay.
Base64 fields must use canonical encoding. Raw envelope and policy hashes remain
in provenance for auditing; they do not define the execution identity. Changes to
actual trusted policy contents or signing authority change identity; operators must
reconcile prior jobs before authorizing such changes. A deliberate rerun requires
a newly authorized signed plan (there is no unsigned rerun switch).
Only an absent job can enter running, and completion uses compare-and-swap.
Concurrent executors using the same ledger are rejected. A running row after a
crash is uncertain and never replayed automatically. Operators must investigate
and reconcile separately; deleting the ledger permits replay. Do not promise
exactly-once external side effects. Timeout, SIGTERM and SIGINT attempts force container removal and
kills the Docker client process group. Failure to remove leaves an uncertain row
and requires host investigation; abrupt host failure also requires reconciliation.

Stdout contains `tcl-provenance/v1`, with `tcl-execution/v1` records: plan/envelope/policy,
source and command SHA-256 identities, image digest, task ID, exit code, status,
and output-discarded marker. It includes neither secrets, secret paths nor argv.
The outer SHA-256 covers canonical provenance. `--provenance-key /trusted/key`
adds an Ed25519 signature using a raw 32-byte private key; verify against a separately
provisioned public key. A hash alone provides integrity comparison, not authenticity.
Protect ledger storage and signing keys against modification by task authors.

## Verification and CI smoke

Unit tests validate signature/policy rejection, DAG validation, command
construction, ledger replay prevention, and actual subprocess lifecycle against a
fake Docker executable (success/failure/timeout/SIGTERM). They do **not** demonstrate actual container isolation:

```sh
python3 -m unittest discover -s tests -p test_production_executor.py
```

Run an explicitly enabled integration smoke on a disposable rootless Docker host.
Before execution, check `docker info --format '{{json .SecurityOptions}}'` contains
`rootless`; inspect cgroup support and resource warnings. Pull a real approved
image by digest, create a clean committed source clone, generate an ephemeral
Ed25519 key outside that clone, calculate the archive digest, sign the sample plan,
and invoke the CLI above with policy limits permitting `python3 -V`.
Assert a succeeded record and matching provenance hash. Re-run with the same
ledger and assert rejection without a second execution. Add separate signed tasks
for a deliberately timed-out command and for an isolation probe: verify nonroot
UID, read-only `/workspace` and `/`, failed network access, absent Docker socket,
and requested resource limits via container inspection. Assert timeout removes the
named container. Unit command mocks must never be reported as this integration
smoke. CI should gate this path behind an explicit rootless-Docker integration job.
