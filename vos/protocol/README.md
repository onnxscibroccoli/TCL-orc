# TCL vOS Host Port v1 — engine-independent game contract

**Status:** The portable **contract** and browser adapter are implemented. The
first Android host binding remains a standalone reference adapter, *not* the
required kernel language. A shared vOS wallet is a logical OS service; it
must not be implemented as a separate wallet inside every game.

## First playable game library

- `civitas.game.json`: resource shifts and NPC market.
- `nightdrive.game.json`: city driving, courier rewards and garage spending.
- Both declare `currency=TCL-vOS-tau` and `wallet_scope=host`.
- Both currently use an HTML5/Canvas renderer shipped inside the Android APK.
- Their balances, issuance, spending and idempotency are enforced in one host
  account. Switching between games does not move funds between ledgers.

An Android reference implementation is located in
`android/vos/app/src/main/java/org/tclorc/vos/GameKernel.java`. The
`TCLNative` object is **an Android-specific transport adapter**, not a
mandate to use Java, JVM, ART, or any single language throughout vOS.

## Versioned host I/O

A renderer loads `android/vos/app/src/main/assets/host.js` and calls
`VOSHost.connect()` (or `VOSHost.create(customTransport)` when embedded in
another host). Any host implementation supplies the following calls:

```text
state() -> JSON string
selectProfile(profile: string) -> JSON string
command(game: string, action: string, argument: int, requestId: string) -> JSON string
observeInteraction() -> void   # OPTIONAL, advisory only
```

Responses use `{"ok":true,"state":{...}}` or
`{"ok":false,"error":"..."}`. The adapter checks response framing, payload
size and command shapes. **All actual authorization and game rule validation
belongs to the host**. A game cannot call a generic `mint` method. The host
must enforce a finite, signed/approved game manifest, predefined rewards,
balances, game action schemas, replay protection, accounting invariants,
capability isolation and versioned save migrations.

Every game spends or earns the same **non-redeemable** τ balance in the same
vOS profile. No token bridge, fiat purchase, withdrawals, staking, exchange
listing, external wallet, crypto asset or P2P credits are implemented.

The current v1 interface is intentionally narrow to get two games playable.
A future asynchronous, binary, version-negotiated interface can support
WASM components, Godot/GDExtension, Unity plugin hosts, desktop applications,
mobile native hosts and WebTransport. These are design paths, not working
connectors.

## Graphics engine: prefer established open source

The existing HTML/Canvas shell is the **fastest installable Android MVP**.
For more advanced 2D/3D games, the selected research direction is
**Godot 4.x**. It is an MIT-licensed engine with existing editor,
materials, scene graph, physics, animation, controller input, export
templates and Android/desktop/Web export pipelines. Those systems should
be reused rather than rewritten inside TCL-orc.

The kernel/credit service should remain separate from Godot itself. Games
send *approved* actions across a host port and receive a view of their
authorized game state; neither game plugins nor renderers may mutate the
accounting journal directly. One engine is not imposed on every title.

See `vos/engines/godot/` for an initial renderer pilot. It is not yet wired
into the standalone Android APK and does not maintain a second currency
wallet. When no host adapter is present, it may render the environment but
must not mint or spend τ.

## Abuse controls

Local input entropy is strictly advisory and never blocks accessibility
features, adaptive inputs, switch controls, rapid taps or assistive devices.
A deterministic input pattern is not evidence of a bot or unique human.
Proof-of-personhood, cryptographic attestations and authenticated game
distribution are separate future hardening milestones, not requirements
for the offline first-playable build.

## Tests

```bash
node --test vos/protocol/tests/host_contract.test.cjs
node --check android/vos/app/src/main/assets/host.js
java -ea -cp /tmp/tcl-vOS org.tclorc.vos.GameKernelTest
```

The game portal is a small, portable renderer. Its host contract prevents
**language lock-in** while preserving the one-wallet operating-system design.
