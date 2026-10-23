# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[PEP 440](https://peps.python.org/pep-0440/) (release tags are `v<version>`, e.g. `v0.1.0rc1`).

## [Unreleased]

## [0.1.0rc1] - 2026-10-23

First release candidate. **Not audited. Testnet only** until the trusted-setup
ceremony, external audit and ownership hand-off to a timelock are complete
(see `docs/DEPLOYMENT.md`).

### Added
- Single-source package version (`src/tcl/__init__.py`, read by `pyproject.toml`); `tcl version`.
- Wheel bundles circuit sources, contracts and OpenZeppelin sources as `tcl/_data`, so
  `tcl zk` / `tcl chain` work from an installed wheel. Resolution: `$TCL_RESOURCE_ROOT` >
  source checkout > bundled data; fails closed with an actionable error otherwise.
  circomlib (GPL-3.0) is not bundled; point `TCL_CIRCOMLIB` at an npm install.
- `tcl doctor [-c core|zk|chain] [--rpc] [--json]`: toolchain/config checks, exit 1 on failure.
- Daemon HTTP probes `/livez`, `/readyz` (503 when not ready or on error), `/version`;
  IPC op `ready` and `tcl daemon ready`.
- Forge scripts `script/deploy/DeployEngine.s.sol` (verifier + UUPS implementation + ERC1967
  proxy, env-driven, post-condition checks, optional ownership transfer to a timelock) and
  `script/deploy/UpgradeEngine.s.sol` (upgrade / rollback / timelock calldata).
- `scripts/deploy_dryrun.sh` and CI job `deploy-dryrun` (anvil simulate + broadcast +
  upgrade + negative guard test).
- CI job `image`: builds the non-root `app` image on every PR with a smoke test; on `v*`
  tags pushes to `ghcr.io/<owner>/tcl-orc` with SBOM, cosign keyless signature and SLSA
  provenance.
- Release job: tag/version/changelog consistency check, SPDX SBOM (syft), cosign keyless
  signatures (`*.sigstore.json`), GitHub SLSA build provenance, prerelease flag for rc tags.
- `docs/DEPLOYMENT.md` runbook and `.env.example`.

### Changed
- Version reset from the unreleased `0.2.0` placeholder to `0.1.0rc1` (no prior tags/releases).
- `tcl chain deploy` / settlement refuse the public anvil dev key on non-dev chain ids.

### Security
- Groth16 keys produced by `tcl zk compile` are single-party **dev** setups; deploy scripts
  refuse non-dev chains unless `VERIFIER_CEREMONY_ATTESTED=true`.
