#!/usr/bin/env bash
# Fail-closed Android runtime smoke for the shipped, signed vOS debug APK.
# The APK and wallet remain offline; GitHub's emulator is a test environment.
set -euo pipefail

apk="${1:?Pass the signed app-debug.apk path}"
out="${VOS_SMOKE_OUT:-/tmp/tcl-vos-runtime-evidence}"
pkg="org.tclorc.vos"
mkdir -p "$out"

capture() {
  adb exec-out screencap -p > "$out/screen.png" 2>/dev/null || true
  adb shell uiautomator dump /sdcard/tcl-vos-ui.xml >/dev/null 2>&1 || true
  adb exec-out cat /sdcard/tcl-vos-ui.xml > "$out/ui.xml" 2>/dev/null || true
  adb logcat -d -t 5000 > "$out/logcat.txt" 2>/dev/null || true
}
trap capture EXIT

test -s "$apk"
adb wait-for-device
adb install -r "$apk" | tee "$out/install.txt"
adb shell logcat -c
adb shell am force-stop "$pkg" || true
adb shell am start -W -n "$pkg/.MainActivity" | tee "$out/launch.txt"

# Android can take several seconds to start WebView. An accessible app must
# expose at least one of its native HTML title/nav labels to UIAutomator.
found=0
for iteration in $(seq 1 24); do
  if ! adb shell pidof "$pkg" >/dev/null 2>&1; then
    echo "vOS process terminated unexpectedly" >&2
    exit 1
  fi
  adb shell uiautomator dump /sdcard/tcl-vos-ui.xml >/dev/null 2>&1 || true
  adb exec-out cat /sdcard/tcl-vos-ui.xml > "$out/ui.xml" 2>/dev/null || true
  if python3 - "$out/ui.xml" <<'PY'
import sys
from pathlib import Path
from xml.etree import ElementTree
xml = Path(sys.argv[1])
try:
    root = ElementTree.parse(xml).getroot()
except (ElementTree.ParseError, OSError):
    sys.exit(1)
text = " ".join((n.attrib.get("text", "") + " " + n.attrib.get("content-desc", ""))
                for n in root.iter()).casefold()
if "civitas" not in text or "night drive" not in text:
    sys.exit(1)
print("Accessible game-library labels detected")
PY
  then
    found=1
    break
  fi
  sleep 2
done

if [[ "$found" != 1 ]]; then
  echo "The Android vOS game library did not become accessible in WebView" >&2
  exit 1
fi

adb exec-out screencap -p > "$out/screen.png"
python3 - "$out/screen.png" <<'PY'
from pathlib import Path
import struct
import sys
p = Path(sys.argv[1])
data = p.read_bytes()
if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 4000:
    raise SystemExit("Missing or invalid Android runtime screenshot")
width, height = struct.unpack(">II", data[16:24])
if width < 320 or height < 320:
    raise SystemExit("Unexpected emulator screenshot dimensions")
print(f"vOS screenshot captured: {width}x{height}, {len(data):,} bytes")
PY

if ! adb shell pidof "$pkg" >/dev/null 2>&1; then
  echo "vOS process exited before final runtime assertion" >&2
  exit 1
fi
echo "PASS: TCL vOS APK installed, launched, rendered accessible games and remained alive."
