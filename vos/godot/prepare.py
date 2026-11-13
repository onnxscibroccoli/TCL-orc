#!/usr/bin/env python3
"""Reproducible OpenCity (MIT/CC0) Godot adapter for TCL vOS.

No reimplementation of vehicle movement, traffic, pedestrians or 3D scenes:
use the pinned source snapshot. The TCL economy is an overlay and Godot autoload.
This script never imports Godot into the TCL consensus or Python ledger.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

UPSTREAM = "https://github.com/webgl-games/opencity"
PIN = "ba9514cdd88ecc2d40ecad503a644449fc98e665"
HERE = Path(__file__).resolve().parent
CONTROL_MAP = {
    "KEY_W": "vos_forward", "KEY_UP": "vos_forward",
    "KEY_S": "vos_back", "KEY_DOWN": "vos_back",
    "KEY_A": "vos_left", "KEY_LEFT": "vos_left",
    "KEY_D": "vos_right", "KEY_RIGHT": "vos_right",
    "KEY_SHIFT": "vos_boost", "KEY_SPACE": "vos_brake",
}


def add_touch_actions(gd: str) -> str:
    # This preserves all original keyboard handling and adds phone controls.
    for key, action in CONTROL_MAP.items():
        needle = f"Input.is_key_pressed({key})"
        gd = gd.replace(needle, f'({needle} or Input.is_action_pressed("{action}"))')
    return gd


def prepare(source: Path, destination: Path) -> None:
    source = source.resolve()
    if source == destination.resolve():
        raise ValueError("Destination must not overwrite the upstream source tree")
    if not (source / "project.godot").is_file():
        raise ValueError("OpenCity project.godot not found")
    license_file = source / "LICENSE"
    credits = source / "CREDITS.md"
    if not license_file.is_file() or "MIT License" not in license_file.read_text():
        raise ValueError("Missing upstream MIT license")
    if not credits.is_file() or "Quaternius" not in credits.read_text():
        raise ValueError("Missing upstream credits / asset provenance")
    if (source / ".git").is_dir():
        commit = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        if commit != PIN:
            raise ValueError(f"Unpinned OpenCity revision: {commit}")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
        ".git", ".godot", "screenshots", "*.import", "*.uid", "__pycache__"
    ))

    wallet = destination / "vos"
    wallet.mkdir(exist_ok=True)
    for name in ("tcl_wallet.gd", "tcl_overlay.gd"):
        shutil.copy2(HERE / name, wallet / name)

    config = destination / "project.godot"
    original = config.read_text()
    target = '[autoload]\n\n'
    if target not in original or 'DevCapture="*res://scripts/dev_capture.gd"' not in original:
        raise ValueError("Pinned upstream project autoload structure changed")
    config.write_text(original.replace(
        'DevCapture="*res://scripts/dev_capture.gd"',
        'DevCapture="*res://scripts/dev_capture.gd"\nTCLWallet="*res://vos/tcl_wallet.gd"'
    ).replace('config/name="OpenCity"', 'config/name="TCL vOS — OpenCity"'))

    scene = destination / "scenes" / "world.tscn"
    text = scene.read_text()
    if '[gd_scene load_steps=14 format=3]' not in text:
        raise ValueError("Pinned upstream scene format changed")
    if '[node name="HUD" type="CanvasLayer" parent="."]' not in text:
        raise ValueError("Pinned upstream HUD node is missing")
    text = text.replace('[gd_scene load_steps=14 format=3]',
                        '[gd_scene load_steps=15 format=3]')
    text = text.replace('[ext_resource type="Script" path="res://scripts/game.gd" id="1_game"]',
                        '[ext_resource type="Script" path="res://scripts/game.gd" id="1_game"]\n'
                        '[ext_resource type="Script" path="res://vos/tcl_overlay.gd" id="tcl_overlay"]')
    text += '\n[node name="TCLvOS" type="CanvasLayer" parent="."]\n'
    text += 'script = ExtResource("tcl_overlay")\n'
    scene.write_text(text)

    for name in ("player.gd", "car.gd", "heli.gd"):
        script = destination / "scripts" / name
        script.write_text(add_touch_actions(script.read_text()))

    (destination / "TCL_ORIGIN.txt").write_text(
        f"Upstream: {UPSTREAM}\nPinned commit: {PIN}\n"
        "Source license: LICENSE (MIT)\nAsset credits: CREDITS.md\n"
        "vOS additions: TCL-orc project under its own repo license\n"
        "Economy: offline, non-redeemable game tau; NOT TCL financial ledger\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True,
                        help="Locally checked-out OpenCity source at pinned revision")
    parser.add_argument("--out", type=Path, required=True,
                        help="Generated Godot build directory")
    args = parser.parse_args()
    prepare(args.source, args.out)
    print(f"Godot 4.7+ TCL vOS / OpenCity adapter prepared: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
