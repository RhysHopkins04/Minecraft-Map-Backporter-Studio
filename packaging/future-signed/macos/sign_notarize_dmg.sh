#!/usr/bin/env bash
set -euo pipefail

DMG="${1:?Usage: sign_notarize_dmg.sh /path/to/file.dmg}"
IDENTITY="${MACOS_CODESIGN_IDENTITY:?MACOS_CODESIGN_IDENTITY is required}"
: "${APPLE_ID:?APPLE_ID is required}"
: "${APPLE_APP_SPECIFIC_PASSWORD:?APPLE_APP_SPECIFIC_PASSWORD is required}"
: "${APPLE_TEAM_ID:?APPLE_TEAM_ID is required}"

codesign --force --timestamp --sign "$IDENTITY" "$DMG"
codesign --verify --verbose=2 "$DMG"
xcrun notarytool submit "$DMG" \
  --apple-id "$APPLE_ID" \
  --password "$APPLE_APP_SPECIFIC_PASSWORD" \
  --team-id "$APPLE_TEAM_ID" \
  --wait
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
codesign --verify --verbose=2 "$DMG"

echo "Signed and notarized DMG: $DMG"
