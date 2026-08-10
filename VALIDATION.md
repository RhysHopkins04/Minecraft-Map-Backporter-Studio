# Validation

The repository uses layered validation.

## Local/source validation

Before committing a patch, run:

```bash
python3 scripts/verify_project.py
python3 -m compileall -q launcher.py src tests scripts
bash -n packaging/macos/*.sh
```

If the project dependencies are already installed, also run:

```bash
python3 tests/test_smoke.py
```

Repository Patch packages include their own validation wrapper so the exact expected patch state can be checked without requiring a packaged desktop build on the local machine.

## GitHub CI

`CI` runs for `main`, `dev`, and pull requests targeting either branch. It installs the core dependencies and runs the project verifier, smoke tests, Python compilation, and shell-script validation.

## Development packaging

Every push to `dev` builds temporary validated packages for:

- macOS Apple Silicon
- Windows x64

The final development DMG/installer is exercised before being uploaded as a temporary GitHub Actions artifact. Development artifacts use the `dev-validated` manifest status and are retained for 14 days.

## Community release validation

A public Community Release is published only when both target-platform jobs succeed and both final manifests report `community-validated`.

The macOS job verifies the ARM64 frozen application, creates the DMG, mounts that final DMG, and runs the packaged self-test from inside it.

The Windows job verifies the frozen executable, builds the Inno Setup installer, installs that final installer, runs the installed self-test, uninstalls the application, and verifies removal.
