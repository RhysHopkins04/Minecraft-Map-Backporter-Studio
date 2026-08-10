# Validation status — v0.4.1 community repository package

This package is the repository-ready community-distribution revision of WG Map Backporter Studio.

## Source checks expected locally / in CI

- project-structure verification,
- core smoke tests,
- Python compile validation,
- shell syntax validation for active macOS packaging scripts.

## Release-platform validation

The public release workflow builds on native GitHub-hosted target runners:

- `macos-15` for Apple Silicon ARM64,
- `windows-latest` for Windows x64.

The release gate does not claim operating-system publisher verification because community builds are unsigned.

Instead, each final artifact must pass application/installer smoke tests and receive `release_status=community-validated` plus a SHA-256 checksum before the GitHub Release publish job can run.

## Important distinction

`community-validated` means the packaged artifact passed this project's automated tests. It does **not** mean Apple notarized the application or Microsoft verified the publisher.
