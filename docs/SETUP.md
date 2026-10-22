# Operator setup

Use Python 3.12+, Node 22 and Git for development.
Run npm ci and install the pinned production, verification and test Python requirements.
Run bin/tcl-cli --state .runtime/operator.sqlite health to initialize and verify the operator database.
Use local disk with reliable SQLite file locking and fsync behavior; network filesystems are not qualified.
Back up through SQLite backup tooling, including live WAL state; copying only the main file during writes is insufficient.

Run npm run check in the validation image for the complete acceptance suite.
The rootless GitHub workflow resolves image digests, runs offline with resource limits, and collects immutable-image replay evidence.
Local browser execution requires Chromium through TCL_CHROMIUM.
A two-CPU, 2 GiB profile is separately measured.
Docker compose boot, HTTP health checks, Anvil settlement and Foundry tests are not implemented acceptance paths in this release.
No production keys, witnesses, identity documents or custody secrets belong in operator batches.
