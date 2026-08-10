#!/usr/bin/env bash
set -euo pipefail

DMG="${1:?Usage: verify_dmg.sh /path/to/file.dmg}"
EXPECTED_BUNDLE_ID="${2:-dev.wargames.mapbackporterstudio}"
MOUNT="$(mktemp -d "${RUNNER_TEMP:-/tmp}/wgmapbackporter-mount.XXXXXX")"
cleanup() {
  hdiutil detach "$MOUNT" -quiet 2>/dev/null || true
  rmdir "$MOUNT" 2>/dev/null || true
}
trap cleanup EXIT

codesign --verify --verbose=2 "$DMG"
xcrun stapler validate "$DMG"
hdiutil attach "$DMG" -readonly -nobrowse -mountpoint "$MOUNT" -quiet
APP="$(find "$MOUNT" -maxdepth 1 -type d -name '*.app' -print -quit)"
[[ -n "$APP" ]] || { echo "No .app found inside DMG" >&2; exit 1; }

codesign --verify --deep --strict --verbose=2 "$APP"
xcrun stapler validate "$APP"
spctl --assess --type execute --verbose=4 "$APP"
"$APP/Contents/MacOS/WGMapBackporterStudio" --self-test
ACTUAL_BUNDLE_ID="$(defaults read "$APP/Contents/Info" CFBundleIdentifier)"
[[ "$ACTUAL_BUNDLE_ID" == "$EXPECTED_BUNDLE_ID" ]] || {
  echo "Unexpected bundle identifier: $ACTUAL_BUNDLE_ID" >&2; exit 1;
}

echo "DMG verification passed; bundled app is Developer-ID signed, notarized, stapled, accepted by Gatekeeper, and passed its packaged self-test."
