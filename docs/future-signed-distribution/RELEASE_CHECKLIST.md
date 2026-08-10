# Verified release checklist

A release is acceptable only when all checks below are green.

## Source / backend

- Core smoke tests pass.
- Python source compiles.
- Packaging definitions pass static validation.
- Git tag exactly matches the application version.

## macOS — Apple Silicon and Intel independently

- Native app created on the matching macOS runner architecture.
- Frozen executable `--self-test` passes before signing.
- Nested Mach-O payloads signed before the containing bundles.
- Outer app signed with Developer ID and hardened runtime.
- `codesign --verify --deep --strict` passes.
- Apple notarization returns success.
- App notarization ticket is stapled and validates.
- Gatekeeper `spctl` assessment accepts the app.
- DMG is created with Applications shortcut.
- DMG is signed, notarized and stapled.
- Final DMG is mounted in CI.
- App inside final DMG passes signature, staple, Gatekeeper and packaged self-test checks.
- SHA-256 and `release_status=verified` manifest emitted.

## Windows x64

- Native app created on Windows runner.
- Main executable Authenticode signed and timestamped.
- Signature verification passes before installer creation.
- Frozen executable `--self-test` passes.
- Inno Setup signs Setup and generated uninstaller using the release signing hook.
- Final installer passes SignTool verification and Windows Authenticode validation.
- Final installer is actually installed in CI.
- Installed executable signature is Valid.
- Installed executable `--self-test` passes.
- Installed uninstaller signature is Valid.
- Silent uninstall succeeds and removes the executable.
- SHA-256 and `release_status=verified` manifest emitted.

## Publishing gate

- Exactly three verified manifests exist: macOS Apple Silicon, macOS Intel, Windows x64.
- No manifest has `release_status` other than `verified`.
- Only then may the GitHub Release be created/uploaded.
