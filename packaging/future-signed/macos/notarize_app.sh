#!/usr/bin/env bash
set -euo pipefail

APP="${1:?Usage: notarize_app.sh /path/to/App.app}"
: "${APPLE_ID:?APPLE_ID is required}"
: "${APPLE_APP_SPECIFIC_PASSWORD:?APPLE_APP_SPECIFIC_PASSWORD is required}"
: "${APPLE_TEAM_ID:?APPLE_TEAM_ID is required}"

TMP_ZIP="${RUNNER_TEMP:-/tmp}/WGMapBackporterStudio-notary.zip"
rm -f "$TMP_ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$TMP_ZIP"

xcrun notarytool submit "$TMP_ZIP" \
  --apple-id "$APPLE_ID" \
  --password "$APPLE_APP_SPECIFIC_PASSWORD" \
  --team-id "$APPLE_TEAM_ID" \
  --wait

xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
spctl --assess --type execute --verbose=4 "$APP"
rm -f "$TMP_ZIP"
echo "Apple notarization and Gatekeeper verification passed for $APP"
