# Offline bundle: run and test TCL with no network

**In plain words:** everything TCL needs to install and run its tests is saved inside this
repository, each file checked by its fingerprint (a SHA-256 hash). You can unplug the internet,
run one command, and get a working install. If any saved file has been changed, even by one
byte, the check stops and tells you.

## Quick start (no network needed)
```bash
python3 -m venv .venv
make offline                    # = scripts/bundle_verify.py + scripts/offline_install.py
.venv/bin/pytest -q
```
`make offline PYTHON=/path/to/python3.12` targets another interpreter. Once `tcl` is installed,
`tcl offline verify` and `tcl offline install` do the same thing.

## What is in `vendor/`
| Path | What |
|---|---|
| `vendor/manifest.json` | One entry per artifact: name, version, kind, python/platform tags, sha256, size, license, license file, upstream URL (for provenance only; never fetched by installs). |
| `vendor/store/sha256/<hex>` | Wheels committed to git. The file name **is** its sha256. |
| `vendor/licenses/` | License text for every artifact. GPL items are build-time only. |
| `vendor/requirements-offline.txt` | The pinned set the store was built from (matches `requirements-test.lock`, plus `setuptools` for the no-build-isolation install). |
| `vendor/release-assets.json` | Large build-time tools kept **out of git** as release assets with pinned hashes (circom, circomspect source, Node.js, snarkjs, circomlib). |
| `vendor/assets/sha256/<hex>` | Where `scripts/bundle.py fetch` puts those assets (gitignored). |

Target: linux-x86_64, CPython 3.11, 3.12, 3.13 (decision D-OFF-5).

## Commands
| Command | Network | Purpose |
|---|---|---|
| `python3 scripts/bundle_verify.py [--require-assets]` | none | Fail closed on missing blob, wrong size, wrong sha256, missing license or orphan blob. |
| `python3 scripts/offline_install.py [--python P] [--runtime-only]` | none | Verify, copy the matching wheels into a temp wheelhouse, `pip install --no-index`, then `pip install --no-index --no-build-isolation -e .` |
| `python3 scripts/bundle.py build` | **online, maintainer only** | Re-download the pinned wheels, rebuild the store, licenses and manifest. |
| `python3 scripts/bundle.py fetch` | **online, optional** | Download the release assets and verify their hashes. |
| `python3 scripts/check_no_ethereum.py` | none | The no-ethereum gate (D-OFF-6). |
| `python3 scripts/gen_poseidon_constants.py --check` | none | Poseidon constants for `circuits/lib/tcl` are regenerated in-house and match. |

## CI
- `offline` job: `scripts/bundle_verify.py`, then the whole install and test suite inside
  `unshare -rn` (a private network namespace with only loopback). It first asserts that an outside
  connection really fails, installs only from the bundle, runs `pytest`, and runs the localnet
  smoke test when `src/tcl/localnet` is present. ZK/EVM tests skip there because circom, snarkjs
  and Foundry are build-time tools (B) that a running localnet does not need.
- `no-ethereum` job: the gate above plus the Poseidon constants check.

## What still needs the network (and why it is fine)
- Re-building the bundle (`bundle.py build`) and fetching release assets (`bundle.py fetch`):
  maintainer steps, done once, every result hash-pinned.
- The non-editable wheel build (`pip install .`) still stages the legacy OpenZeppelin sources
  from the `lib/` submodules; the offline path uses the editable install instead.
- The legacy EVM jobs (forge, anvil, slither) and the ZK prover jobs. Those tools are listed as
  release assets or as legacy (D-OFF-2, D-OFF-6).
- The Python interpreter itself is taken from the host. Bundling a hashed standalone interpreter
  is a planned follow-up.
