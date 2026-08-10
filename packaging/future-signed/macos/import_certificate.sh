#!/usr/bin/env bash
set -euo pipefail

: "${APPLE_CERTIFICATE_P12_BASE64:?APPLE_CERTIFICATE_P12_BASE64 is required}"
: "${APPLE_CERTIFICATE_PASSWORD:?APPLE_CERTIFICATE_PASSWORD is required}"

KEYCHAIN_PATH="${RUNNER_TEMP:-/tmp}/wg-map-backporter-signing.keychain-db"
KEYCHAIN_PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
CERT_PATH="${RUNNER_TEMP:-/tmp}/wg-map-backporter-developer-id.p12"

printf '%s' "$APPLE_CERTIFICATE_P12_BASE64" | base64 --decode > "$CERT_PATH"
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security set-keychain-settings -lut 21600 "$KEYCHAIN_PATH"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security import "$CERT_PATH" -P "$APPLE_CERTIFICATE_PASSWORD" -A -t cert -f pkcs12 -k "$KEYCHAIN_PATH"
security list-keychains -d user -s "$KEYCHAIN_PATH" login.keychain-db
security default-keychain -d user -s "$KEYCHAIN_PATH"
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH" >/dev/null

IDENTITY="$(security find-identity -v -p codesigning "$KEYCHAIN_PATH" | sed -n 's/.*"\(Developer ID Application:.*\)"/\1/p' | head -n 1)"
if [[ -z "$IDENTITY" ]]; then
  echo "No 'Developer ID Application' signing identity was found in the imported certificate." >&2
  exit 1
fi

{
  echo "MACOS_KEYCHAIN_PATH=$KEYCHAIN_PATH"
  echo "MACOS_KEYCHAIN_PASSWORD=$KEYCHAIN_PASSWORD"
  echo "MACOS_CODESIGN_IDENTITY=$IDENTITY"
} >> "${GITHUB_ENV:?GITHUB_ENV is required in CI}"

echo "Imported Developer ID identity: $IDENTITY"
rm -f "$CERT_PATH"
