# Portable TCL vOS engine track — Godot 4 + OpenCity

**Status:** experimental cross-platform game-engine adapter, not a replacement
for the already-working [Android WebView MVP](../../android/vos/README.md).

The Android shell does **not** determine the architecture of TCL vOS.
Java is one possible **platform host** for Android. The long-term gaming
runtime uses existing open-source engines and a minimal shared-wallet API
which can be implemented inside each supported host without rewriting game
physics, maps, collision, input handling, and rendering.

## Reused, not reinvented

- **Godot Engine 4.7.2** — MIT licensed engine; builds for Android,
  Linux, Windows, macOS, iOS, and Web.
- **OpenCity**, by Charan Raj Kandula — MIT licensed open-world sample,
  pinned to commit `ba9514cdd88ecc2d40ecad503a644449fc98e665`.
  It already supplies a procedural city, 3D pedestrian character, car/truck
  steering, ambient traffic, pedestrians, camera, helicopter, and shaders.
- **Quaternius** Universal Animation Library mannequin — CC0; original
  asset license is included by retaining upstream `CREDITS.md`,
  `LICENSE` and the mannequin's original permission notice.

Source: https://github.com/webgl-games/opencity

The `prepare.py` script is an explicit, reviewable adapter and rejects
unexpected source revisions. It is the **only** local code that changes
the OpenCity scripts during composition; upstream sources are never
modified in-place.

## One vOS currency between game modules

`tcl_wallet.gd` is loaded as an autoload service in the Godot process
named `TCLWallet`. It is not owned by any individual game. Allowed
game capabilities are scoped per game:

| Module | Actions | Credit effect |
|---|---|---|
| Civitas-99 | Work once/turn, advance turn | +30 τ per verified local shift |
| OpenCity | Complete one physically validated courier mission | +45 τ |
| NightDrive | Buy turbo, paint, or refuel | −100 / −60 / −15 τ |

A player's *single local balance* is immediately usable by all three
registered game contexts. The reusable action contract is

`dispatch(game_id, action, idempotency_key) -> {ok, duplicate, state}`

Games request an authorized action; the vOS wallet issues or sinks integer
credits. Its journal is hashed and replayed with a strict order and
duplicate-command protection. Each accepted command saves to `user://`
atomically before the in-memory state changes. The credits are **fictional,
non-redeemable game points**, separate from the TCL financial ledger and
experimental consensus policies. A local hash is tamper-*evident*, not an
unforgeable identity or anti-cheat attestation.

**Important:** The Android Java-hosted MVP has a separate prototype save
format. This Godot adapter establishes the **shared game-level API**, not
yet cross-device save interchange or synchronization with the older APK.
Do not copy a wallet save from the Java prototype into Godot. A formally
versioned import/export conversion and regression fixtures must ship
before claiming interchangeability.

## Play / develop

```bash
git clone https://github.com/webgl-games/opencity /tmp/opencity
git -C /tmp/opencity checkout ba9514cdd88ecc2d40ecad503a644449fc98e665
python vos/godot/prepare.py --source /tmp/opencity --out /tmp/tcl-vos-godot
godot --headless --editor --path /tmp/tcl-vos-godot --import --quit
godot --path /tmp/tcl-vos-godot
```

**Keyboard:** OpenCity's existing WASD, Shift, Space, E, mouse controls remain
available. **Android:** the vOS overlay introduces large touch buttons for
movement, boost, brake, and enter/exit, without root, Shizuku, camera or AI.

After entering a car, start the courier mission and drive to the visible
green drop-off marker. Physically travel at least 35 metres to finish and
receive 45 τ. Work a Civitas shift to earn 30 τ, then purchase a NightDrive
turbo for 100 τ from the shared balance. The game engine's native physics
uses floating-point for **graphics and movement**, and is not a
deterministic replicated consensus engine; the *currency journal* and game
receipts remain integer-only.

## Verify and build

GitHub Actions runs `.github/workflows/godot-vos.yml` to:

1. Check out **only the pinned** OpenCity revision and its original notices.
2. Apply the adapter; parse and import the real Godot 4.7.2 project.
3. Run actual cross-game wallet/replay/idempotency/tamper checks inside Godot.
4. Publish an installable Godot **project/source bundle** for desktop
   and reproducible exports.

An Android 3D export job is included as an explicit opt-in
`workflow_dispatch/android_export=true`; it downloads the official
matching 4.7.2 templates (~1.3 GB) and uses the official debug exporter.
The 3D APK is **not considered tested** until that job succeeds and device
acceptance is recorded. For immediate Android testing, use the existing
WebView game APK from the separate Android workflow.

## Performance and evolution

The first public phone target is Android 8+ on an entry-level ARM64 device.
Godot OpenCity's desktop 3D effects, draw distances and population counts
must be profiled *on hardware* before a mobile release. Future work:

- Android performance preset (Mobile/GL Compatibility renderer, reduced
  lights, draw distance and procedural population while preserving gameplay).
- Versioned wallet capability tokens and migrations; cross-device signed
  receipt reconciliation (not unverified CRDT merging of monetary balances).
- A vOS game-launcher with discoverable manifests, a per-game permission
  review, two or more independently installed Godot games, and stable APIs.
- Experimental WASM deterministic game rules and cryptographic identity
  added only after reproducible test vectors and opt-in design review.

**Do not interpret the OpenCity demo as a finished GTA clone, a verified
security sandbox, or a multiplayer protocol.** The value is reusing a
working free engine and free driving prototype as a credible foundation.
