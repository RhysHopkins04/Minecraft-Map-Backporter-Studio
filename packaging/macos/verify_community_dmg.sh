#!/usr/bin/env bash
set -euo pipefail
DMG="${1:?Usage: verify_community_dmg.sh /path/to/file.dmg}"
MOUNT="$(mktemp -d "${RUNNER_TEMP:-/tmp}/wgmapbackporter-mount.XXXXXX")"
cleanup() {
  hdiutil detach "$MOUNT" -quiet >/dev/null 2>&1 || true
  rmdir "$MOUNT" >/dev/null 2>&1 || true
}
trap cleanup EXIT

hdiutil attach "$DMG" -mountpoint "$MOUNT" -nobrowse -quiet
APP="$MOUNT/WG Map Backporter Studio.app"
BIN="$APP/Contents/MacOS/WGMapBackporterStudio"
[ -d "$APP" ] || { echo "Application missing from DMG"; exit 1; }
[ -x "$BIN" ] || { echo "Application executable missing from DMG"; exit 1; }

ARCH_INFO="$(file "$BIN")"
echo "$ARCH_INFO"
echo "$ARCH_INFO" | grep -q 'arm64' || { echo "Final DMG executable is not arm64"; exit 1; }
"$BIN" --self-test

echo "Unsigned Apple Silicon DMG validation passed."
