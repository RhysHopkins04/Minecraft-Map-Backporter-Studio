#!/usr/bin/env bash
set -euo pipefail

APP="${1:?Usage: sign_app.sh /path/to/App.app}"
IDENTITY="${MACOS_CODESIGN_IDENTITY:?MACOS_CODESIGN_IDENTITY is required}"
ENTITLEMENTS="${2:-$(cd "$(dirname "$0")" && pwd)/entitlements.plist}"

[[ -d "$APP" ]] || { echo "App bundle not found: $APP" >&2; exit 1; }

# Apple recommends signing nested code from the inside out. Sign every Mach-O
# payload first, then nested bundles/frameworks, and finally the outer app.
while IFS= read -r -d '' file; do
  if [[ -L "$file" ]]; then
    continue
  fi
  if file -b "$file" | grep -q 'Mach-O'; then
    echo "Signing Mach-O: $file"
    codesign --force --timestamp --options runtime --sign "$IDENTITY" "$file"
  fi
done < <(find "$APP/Contents" -type f -print0)

# Frameworks and any nested bundles must be sealed after their contents.
while IFS= read -r bundle; do
  [[ -n "$bundle" ]] || continue
  echo "Signing nested bundle: $bundle"
  codesign --force --timestamp --options runtime --sign "$IDENTITY" "$bundle"
done < <(find "$APP/Contents" -depth -type d \( -name '*.framework' -o -name '*.app' -o -name '*.bundle' -o -name '*.plugin' \) -print)

codesign --force --timestamp --options runtime --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
echo "Developer ID signing verification passed for $APP"
