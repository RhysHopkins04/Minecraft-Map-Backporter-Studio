# Installation

WG Map Backporter Studio community releases are distributed as normal desktop application packages. End users do not need Python, Homebrew, pip, or a development environment.

## macOS — Apple Silicon only

Supported Macs use Apple Silicon (M1, M2, M3, M4, and later compatible ARM-based Macs).

1. Download `WGMapBackporterStudio-<version>-macOS-AppleSilicon.dmg` from the project's GitHub Releases page.
2. Open the DMG.
3. Drag **WG Map Backporter Studio.app** into **Applications**.
4. Try to open the application from Applications.
5. Because community builds are not Apple-notarized, macOS may block the first launch. Use **System Settings → Privacy & Security → Open Anyway**, then confirm that you want to open the application.
6. Future launches should work normally for that authorised application.

Do not disable Gatekeeper globally and do not use scripts that weaken macOS security settings.

## Windows x64

1. Download `WGMapBackporterStudio-<version>-Windows-x64-Setup.exe` from the project's GitHub Releases page.
2. Run the installer.
3. Choose whether to create the optional desktop shortcut.
4. Launch the application from the Start Menu or desktop shortcut.

Community builds are not Authenticode-signed. Windows SmartScreen may show an unrecognised-app warning. Verify that the installer came from the project's own release and compare its SHA-256 checksum before choosing to run it.

## Verifying a download

Each community release publishes SHA-256 checksum files beside its installer packages. Compare the downloaded file's hash with the published value before bypassing any first-run warning.

## Supported desktop targets

- macOS Apple Silicon (ARM64)
- Windows x64

Intel macOS is intentionally not an active release target.
