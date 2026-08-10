# Release signing setup

The repository is designed so an official release cannot be produced merely because compilation succeeded. Real platform trust credentials are required.

## Recommended GitHub environment

Create a GitHub Actions environment named:

`release-signing`

For public distribution, protect that environment with required reviewers so a tagged release cannot access signing credentials without approval.

## macOS requirements

You need an Apple Developer Program membership and a **Developer ID Application** certificate exported as a password-protected `.p12` file.

Configure these secrets in the `release-signing` environment:

- `APPLE_CERTIFICATE_P12_BASE64` — base64 of the `.p12` file.
- `APPLE_CERTIFICATE_PASSWORD` — password protecting that `.p12`.
- `APPLE_ID` — Apple ID used for notarization.
- `APPLE_APP_SPECIFIC_PASSWORD` — app-specific password for notarization.
- `APPLE_TEAM_ID` — Apple Developer Team ID.

The workflow imports the certificate into a temporary keychain, signs nested Mach-O code from the inside out, signs the outer `.app`, sends the app to Apple's notary service, staples the app, creates a DMG, then signs/notarizes/staples the DMG as well.

The temporary signing keychain exists only on the ephemeral GitHub-hosted macOS runner.

## Windows requirements

You need a currently valid Windows code-signing certificate chaining to a trusted public certificate authority and exportable as a password-protected PFX for this pipeline.

Configure these secrets:

- `WINDOWS_CERTIFICATE_PFX_BASE64` — base64 of the PFX.
- `WINDOWS_CERTIFICATE_PASSWORD` — PFX password.

Configure this repository/environment variable:

- `WINDOWS_TIMESTAMP_URL` — RFC 3161 timestamp endpoint supplied/recommended by the certificate provider.

The workflow uses Windows SDK SignTool with SHA-256 file and timestamp digests. Inno Setup is given the signing hook while compiling, which means the generated Setup executable and generated uninstaller are signed as part of the installer build.

For a larger public project, a hardware-backed/cloud signing service is preferable to keeping an exportable PFX in CI. The current signing adapter can later be replaced without changing the application or installer layout.

## Releasing

1. Update the application version in source.
2. Commit and push the validated source.
3. Create a matching tag, for example `v0.4.0` for application version `0.4.0`.
4. Push the tag.
5. Approve the `release-signing` GitHub environment if protection is enabled.
6. The release pipeline builds and verifies Apple Silicon macOS, Intel macOS, and Windows x64 independently.
7. The publish job executes only after all three verified build jobs succeed.

The pipeline explicitly rejects a tag that does not exactly match the application version.
