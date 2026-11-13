# TCL vOS + Civitas-99 — playable local alpha

**Stage: first playable vertical slice.** The TCL virtual gaming operating system (vOS) hosts a game called **Civitas-99: The Commons Exchange**. The game is a 2D city-production and virtual-resource limit-order trading simulation. It uses **only in-game CIV credits**; no fiat, real-world positions, brokerage orders, blockchain transfers, or external financial exit.

The game and vOS are deliberately separate from `tcl.ledger`, the TCL BFT node, and draft PR #27. No new consensus policy or identity proof is activated. The implementation is **local-only and unauthenticated**; it cannot be bound to a public interface. This is not an online multiplayer, secure user account system, or game-engine SDK ready to run arbitrary third-party packages.

## Run from checkout (Python 3.11+)

```bash
python -m pip install -e .
python -m tcl.vos demo
python -m tcl.vos serve --port 8765
# Or through the main TCL CLI:
tcl vos serve --port 8765
```

Open **http://127.0.0.1:8765/** in your browser. The persistent save defaults to `~/.local/share/tcl-vos/civitas99.json` (file permission `0600`). Use `--save /some/local/path.json` to select a different location. **Do not expose the port through a tunnel or proxy.**

For Termux:

```bash
pkg install python git
python -m pip install -e .
python -m tcl.vos serve --port 8765
```

No root, Shizuku, Play Services, wallet, Java, or WASM package is needed for basic gameplay.

## Play the market

1. Enter a two-character-or-longer avatar name and select **Enter city**.
2. Begin with **1,200 CIV** and eight units of each resource. Work a solar, water, or recycling shift to produce five more energy, water, or alloy units and gain reputation.
3. Place a **buy** or **sell** limit order specifying price and quantity. The game uses FIFO price-time matching at the **resting maker's price**. Unfilled orders remain visible in the order book.
4. The local city bot (`citybot`) posts starter **bid/ask liquidity** for all three resources, so you can trade solo immediately. You can also switch to a second avatar (for example, `bob`) to test person-to-person matching. This is local simulation, not player authentication. Cancel an unfilled order to release reserved credits or resources.
5. Contribute resources to community projects. Each contribution builds your civic reputation; completing projects awards an additional achievement bonus.
6. Advance a city turn to unlock another shift. City map tiles are procedurally and reproducibly generated from the world seed.

## vOS: implemented architecture

| Layer | Implemented now |
|---|---|
| Kernel | `tcl.vos.runtime.GameOS`: declared game manifest, versioned action schema, capability grants and deterministic dispatch. |
| Event bus | An append-only, SHA-256-linked command/event journal and trusted-host event observers with mutation isolation. |
| Economic state | `Civitas99`: per-player virtual credits/resources, escrow, price/time order book, partial fills, maker price improvement, cancels, production and civic quests. |
| Local persistence | Atomic private JSON saves; every command is replayed to verify the prior hash chain and conservation invariants. |
| Transport | stdlib HTTP on `127.0.0.1` only, strict JSON writes, same-origin checks, request-size bounds and CSP. |
| Accessible game client | Responsive HTML/CSS/JavaScript with controls for creating players, working, trading, viewing book/tape, city visualization, and completing projects. |
| WASM pilot | A browser Worker compiles a no-import WebAssembly score module. A host-enforced 500 ms worker timeout isolates a demo mini-game; it is not third-party WASI security certification. |
| JVM pilot | `vos/jvm/src/com/tcl/vos/TclVosHost.java` is a Java 17 lifecycle supervisor launching the same loopback vOS backend via a fixed argument vector. It does not execute or translate Python game rules into JVM bytecode. |

### Resource accounting

The central game invariant is **conservation**, even with partially filled orders:

```text
sum(available CIV) + sum(reserved buy price * remaining units) = issued game CIV
sum(available resources) + sum(reserved sell units)
 + sum(public works contributions) = issued game resources
```

No fee, mint, demurrage, or transfer is permitted except explicit starting virtual balances and scheduled game work production. The code rejects self-matching, malformed orders, cross-actor cancellations, unaffordable orders and unknown capability requests **before state is committed**. Each accepted command is applied to a draft of the in-memory game, checked, then appended as a reproducible event. Failed observers cannot rewrite the canonical journal.

No advanced anti-bot claims are made from player timing or entropy. An entropy score by itself does **not** establish identity or fair behavior, so anti-cheat metrics belong in a later opt-in diagnostic module, not an automatic punishment mechanism.

### WASM/JVM boundary

The browser WASM worker has zero imports from the host, no WASI, no market access, and a strict worker deadline. The JVM launcher runs the Python simulation as a child process without shell evaluation. The prototype does **not** currently provide arbitrary mod loading, third-party code signing, WIT/WASI resource capability validation, Hyperlight hardware isolation, or an independently audited VM. These are optional future integrations and must not be claimed as implemented.

## Test and verification

```bash
python -m pip install -r requirements-test.txt
python -m pip install -e .
pytest -q tests/test_vos.py
python -m compileall -q src/tcl/vos
node --check src/tcl/vos/web/app.js
node --check src/tcl/vos/web/guest_worker.js
javac --release 17 -d /tmp/tcl-vos-jvm vos/jvm/src/com/tcl/vos/TclVosHost.java
java -cp /tmp/tcl-vos-jvm com.tcl.vos.TclVosHost --help
```

CI also runs a real WebAssembly arithmetic fixture using Node's `WebAssembly` API. The main TCL CI includes the new Python game tests; the dedicated `vos-game` CI job checks Python, the browser's WASM fixture, and the Java 17 host. Tagged releases and container publication depend on that job.

### Future game milestones (not yet implemented)

- Configurable WASM guest manifest with signed hashes, strictly metered CPU/memory, no imports by default, explicit user-permission prompts, and independent sandbox fuzzing.
- Shared persistent co-op cities with authenticated player sessions and a replicated event stream.
- Advanced game production chains, quests, policy voting, district progression, market depth and order analytics, tutorial onboarding, optional accessibility customizations, and community asset workshop.
- Replay/time-machine UI, replayable market-making bots, and balancing dashboards.
- Optional use of TCL ledger commitments for game session attestations only after compatibility and independent security tests. **Do not mix game credits with the existing TCL financial ledger.**

The game is a **prototype**, not a public multiplayer service or a completed gaming operating system.

## Stage 2: deterministic courier gameplay and replication primitives

The first vOS game now includes an eight-cell virtual courier lane. Drive the
courier with five integer throttle choices (-2, -1, 0, +1, +2); every completed
delivery grants *five reputation*, not market credits. Gameplay uses the
**semi-implicit Euler** update in integer signed **Q32.32**:

```text
Q = 2^32
a = throttle * Q / 4
v[n+1] = clamp(v[n] + trunc(a * dt / Q), -3Q, 3Q)
x[n+1] = x[n] + trunc(v_raw[n+1] * dt / Q)
dt = Q for one simulated second
```

Velocity clamping occurs *after* position integration; the WASM worker uses
this same order. Arriving at x >= 8Q resets position and velocity and awards
one delivery. Negative positions reset to the origin. Malformed or overflowing
fixed-point inputs are rejected before any event or market balance changes.

- `src/tcl/vos/fixedpoint.py`: signed i64 arithmetic, truncation toward zero,
  reserved INT64_MIN error sentinel, no floats.
- `vos/wasm/q32.c`: integer-only WebAssembly implementation (no host imports)
  with signed 64-bit entrypoints for addition, multiplication, velocity and
  position. Not a general modding sandbox.
- `src/tcl/vos/web/q32.wasm.b64`: integrity-pinned compiled guest delivered by
  `GET /q32.wasm` only on the loopback demo server. The server verifies SHA-256.
- `vos/wasm/parity.js`: Node WASM parity tests against 108 Python vectors,
  including negative rounding, i64 extremes and overflow signaling.
- `tests/test_vos_fixedpoint.py`: deterministic game replay, conservation,
  invalid input rejection and local binary endpoint tests.

The server remains authoritative. The browser independently calculates a
courier move in the isolated WASM worker, compares its result with the accepted
Python event, and displays a mismatch diagnostic rather than granting currency.

### Delta-CRDT for optional social metadata

`src/tcl/vos/sync.py` provides bounded Last-Writer-Wins registers and
Observed-Remove Sets for **only presence/cosmetic state**. Delta packets use
strict versioned JSON schemas and Lamport clocks, with deterministic tie-breaks,
idempotent merging, tombstones and bounded replica size.

**Never use delta-CRDT merges to reconcile market orders, game balances,
credit issuance, voting, or cryptographic consensus.** These require ordered
authenticated state transitions, replay and conservation checks. Current CRDT
code is an in-process, adversarially tested protocol primitive, **not a
connected libp2p network**: there is no QUIC, Noise, WebRTC, AutoNAT, DCUtR,
relay service, peer authentication or production Internet-facing endpoint.
Tests cover eventual convergence under packet reorder, partitions, duplicate
delivery and invalid messages. It can later be connected through a narrowly
scoped authenticated transport adapter once peer identities and rate limits
are independently verified.

To execute just the new acceptance tests:

```bash
python -m pytest -q tests/test_vos_fixedpoint.py tests/test_vos_sync.py
clang --target=wasm32 -O2 -std=c11 -nostdlib -fvisibility=hidden \
  -Wl,--no-entry -Wl,--strip-all -Wl,--export=q32_add \
  -Wl,--export=q32_mul -Wl,--export=q32_velocity \
  -Wl,--export=q32_position -o /tmp/q32.wasm vos/wasm/q32.c
TCL_VOS_Q32_WASM=/tmp/q32.wasm node vos/wasm/parity.js
```

The CI release gates now require these tests plus both the rebuilt and the
bundled WASM module's Python parity checks. Identity, rPPG, ZK personhood and
Internet-wide NAT traversal are **not** implemented and are not needed for
local gameplay. A low-entropy input heuristic alone must not suppress users
with assistive technology or legitimate repetitive inputs.
