# TCL vOS — Android Arcade MVP (native offline APK)

**Status:** Standalone offline Android debug prototype. Supports Android 8.0+ (API 26+), including Android 15. Does **not** require root, Shizuku, Termux, an account, or an Internet connection.

This is the **first installable operating-system shell + two-game vertical slice**. A central native `GameKernel` owns **one TCL vOS game-credit wallet (τ)**. All games debit and credit that same account. There are **no separate per-game currency balances** and no JavaScript function able to award arbitrary credits. Game-specific resources, missions and cosmetics remain game-specific.

> **Do not confuse these vOS credits with existing TCL-orc consensus tokens.** The current APK is a deliberately disconnected local simulation. Credits cannot be sold, purchased with fiat, redeemed, exchanged for external assets, sent to an external address, or transferred to another device. Real blockchain/custodial integrations are absent.

## Playable games

| Game | Earn universal τ | Spend universal τ | Gameplay |
| --- | --- | --- | --- |
| **Civitas 99** | +16 τ per completed resource-production shift; NPC purchases of surplus resources credit the wallet. | Buy energy, water, and alloy from an offline NPC. | Resource simulation, trade, shifts and city turns. |
| **Night Drive** | +45 τ per courier delivery. | Buy turbo (100 τ), neon paint (55 τ), and refueling (20 τ). | Retro top-down 24×24 explorable city, road navigation, delivery goals, upgrades, and decorative traffic. |

**Demonstrable cross-game economy:** Work in Civitas → buy turbo in Night Drive → complete a delivery → return to Civitas and buy a resource. All four transactions touch **the same** native credit balance, and restart/replay preserves it.

The Android Civitas market initially uses fixed NPC quotes, **not the complete limit-order book** in the separate Python `tcl.vos` prototype. Its goal is a standalone installable vOS wallet/game shell. Integrating the full market-matching engine is the next game-specific phase.

## Native game operating-system boundaries

- **GameKernel.java:** Host-owned integer accounting, explicit validated game actions, idempotent request IDs, per-profile save files, SHA-256-linked replay checks, atomic private writes, inventory and game-state invariants. `issued - sunk == universalBalance` on every accepted action. Android WebView cannot directly change wallets, grant awards, or write ledger files.
- **MainActivity.java:** A permission-minimal native Android shell. Pages are served only from a **fixed allowlist of APK assets** under an HTTPS-shaped local origin. WebView file/content access and external navigation are disabled. No `INTERNET`, camera, microphone, or contacts permission. On invalid replay, the app **stops without wiping the wallet**.
- **assets:** Offline original HTML/CSS/Canvas2D game library, wallet view, isometric-inspired top-down city map, virtual D-pad + keyboard control, accessible text labels and reduced-motion support. No remote fonts, libraries, advertising, telemetry, or external assets.
- **EntropyMeter.java:** Input-timing diversity, computed in memory on the device (up to 32 intervals); **advisory-only**. It never denies play, never exports timing data, and is **not personhood, Sybil resistance, or identity verification**. A local nickname selects an unverified profile.
- **Non-root:** Uses standard Android SDK APIs only, no elevated permissions.

The real account/game sharing model requires authenticated peers, identity verification, signed game definitions and cross-device settlement. Those are future milestones, not features shipped in this APK. Until then, profiles are private **on-device**, not secure multi-user identities.

## CI / direct APK download

GitHub Actions workflow: [TCL vOS Android APK](../../.github/workflows/android-vos.yml).

For each pull request, the workflow validates the Java economic engine, runs offline JavaScript and permissions checks, compiles the Android APK using Android Gradle Plugin 8.7.3, verifies its debug signature, and uploads the **actual installable APK** as the named artifact:

`TCL-vOS-Civitas-NightDrive-Android-debug-APK`

The workflow summary prints a **direct GitHub Actions artifact URL** and the APK SHA-256. Download the artifact ZIP from GitHub, extract `app-debug.apk`, and install on the phone. The GitHub artifact may require being logged into GitHub. For Android 15, allow your chosen file manager/browser to install downloaded apps if prompted. This APK is a **debug-signed test build**, not a Play Store release or a published production game.

To build locally with a supported JDK 17, Android SDK 35 and Gradle 8.9:

```bash
cd android/vos
# Requires Android SDK commandline-tools, platforms;android-35 and build-tools;35.0.0
# Ensure ANDROID_HOME is configured.
gradle --no-daemon :app:assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk
```

To test the native ledger **without** an Android SDK:

```bash
mkdir -p /tmp/tcl-vos-test
javac --release 17 -encoding UTF-8 -d /tmp/tcl-vos-test \
  android/vos/app/src/main/java/org/tclorc/vos/GameKernel.java \
  android/vos/app/src/main/java/org/tclorc/vos/EntropyMeter.java \
  android/vos/tests/org/tclorc/vos/GameKernelTest.java
java -ea -cp /tmp/tcl-vos-test org.tclorc.vos.GameKernelTest
node --check android/vos/app/src/main/assets/app.js
```

**Current acceptance tests cover:** cross-game earn/spend, restart/replay, multiple local profiles, duplicate request idempotency, rejection of arbitrary credit minting, tampered on-disk records, field validation, concurrent command sequencing, unchanged wallet on invalid commands, and advisory entropy behavior.

## Engineering limitations and next steps

This is a **MVP**, not Grand Theft Auto or a fully featured game distribution platform. Night Drive has a retro top-down city and deliveries, **not** high-end 3D physics, pedestrians, combat, AI traffic behavior, external assets, or network multiplayer. It does not emulate Nintendo 64 games or redistribute copyrighted assets. Performance on target Android hardware still requires physical device acceptance.

Next increments: re-use one canonical portable game action/matching schema across native Android and Python vOS; implement a signed game manifest and capability SDK; add game UI save/export, driving dynamics, missions and asset streaming; implement authenticated P2P sessions with safe authoritative currency reconciliation. No biometric/personhood gate is required for the offline MVP; production-grade anti-abuse/identity remains an independent future program.
