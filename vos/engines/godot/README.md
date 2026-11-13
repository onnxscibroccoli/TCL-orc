# Godot 4 Night Drive renderer — cross-platform pilot

**Purpose:** Reuse a maintained, open-source 3D game engine rather than
building TCL-orc's own graphics, animation, asset pipeline or renderer.

The checked-in scene uses **Godot 4.6.1**, with the compatibility renderer
for modest Android hardware. Godot is a separately maintained MIT-licensed
engine, supporting Android, desktop, Web and other export targets. TCL-orc's
implementation remains freely licensed by its own project.

## Engine integration, not kernel lock-in

The vOS wallet and immutable game credit rules remain in the *host*.
`scripts/night_drive.gd` is intentionally an **adapter and renderer**, not
a currency accounting library. It exposes:

```text
attach_host(callback)
request_move(direction)
update_from_host({"ok": true, "state": {
  "nightdrive": {"x": 3, "y": 2},
  "wallet": {"credits": 516}
}})
```

A bound callback must route actions to the existing vOS host. Gameplay
records, issuance, game rewards, wallet debits and replay protection cannot
be decided by an untrusted game scene.

Without a host callback, the scene displays a **cosmetic walk/drive preview
only**. It cannot earn or spend any τ credits, does not create a second wallet
and does not silently diverge from Civitas. This distinction is intentional.

## Already implemented

- Godot scene under `scenes/night_drive.tscn`.
- Procedural 24×24 city based on the current Night Drive road grid.
- GPU-batched road/building meshes, vehicle and delivery goal marker.
- 3D camera and compatibility rendering profile.
- Keyboard arrow/WASD input mapped to game action IDs.
- Host callback injection and fail-closed state validation.
- Deterministic scene navigation smoke test, exercised by
  `vos-godot.yml` using the upstream Godot distribution.

## Run without an Android SDK

```bash
godot --headless --editor --path vos/engines/godot/night_drive --quit
godot --headless --path vos/engines/godot/night_drive \
  --script res://tests/smoke.gd
godot --path vos/engines/godot/night_drive
```

The **installable MVP APK** remains the separate `android/vos` native host
with Canvas2D games and a universal local τ wallet. The Godot renderer is
a **cross-platform host adapter pilot**, not an already-integrated Android
Godot export or a replacement for the playable APK.

Next integrations: a typed Godot/GDExtension host-port module; signed and
capability-limited game bundles; packaging the Godot export inside the
vOS host without creating isolated wallets; optional advanced 3D assets,
AI traffic and environment systems. Prefer established open-source plugins,
licensed environment packs, physics helpers and rendering extensions.
Never copy proprietary games, maps or textures.

Engine sources and upstream export instructions:
- https://github.com/godotengine/godot
- https://docs.godotengine.org/en/4.6/tutorials/export/exporting_for_android.html
