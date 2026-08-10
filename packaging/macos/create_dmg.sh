#!/usr/bin/env bash
set -euo pipefail

APP="${1:?Usage: create_dmg.sh /path/to/App.app /path/to/output.dmg [volume-name]}"
DMG="${2:?Usage: create_dmg.sh /path/to/App.app /path/to/output.dmg [volume-name]}"
VOLUME_NAME="${3:-WG Map Backporter Studio}"
STAGE="$(mktemp -d "${RUNNER_TEMP:-/tmp}/wgmapbackporter-dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "$VOLUME_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
echo "Created $DMG"
