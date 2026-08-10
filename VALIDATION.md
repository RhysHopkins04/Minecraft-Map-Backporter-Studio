# Validation

The repository uses layered validation.

## Local/source validation

The application declares Python **>=3.11**. Repository static validation itself is intentionally lighter-weight so patch wrappers can still verify workflow/document invariants on a developer machine whose default `python3` is older.

Before committing a patch, run:

```bash
python3 scripts/verify_project.py
bash -n packaging/macos/*.sh
```

When a Python 3.11+ interpreter is available, also run:

```bash
python3 -m compileall -q launcher.py src tests scripts
```

If the project dependencies are installed for that supported interpreter, also run:

```bash
python3 tests/test_smoke.py
```

Repository Patch packages include their own validation wrapper so the exact expected patch state can be checked without requiring a packaged desktop build on the local machine. A wrapper may explicitly **skip** local runtime smoke/compile checks when the local Python is below 3.11 or the runtime dependencies are absent; that skip is not treated as a patch failure. GitHub CI installs the dependencies and runs the authoritative runtime checks on Python 3.12.

## GitHub CI

`CI` runs for `main`, `dev`, and pull requests targeting either branch. It installs the core dependencies and runs the project verifier, smoke tests, Python compilation, and shell-script validation.

## Development packaging

Build-relevant pushes to `dev` build temporary validated packages for:

- macOS Apple Silicon
- Windows x64

The final development DMG/installer is exercised before being uploaded as a temporary GitHub Actions artifact. Development artifacts use the `dev-validated` manifest status, have a 7-day fallback retention, and older `DEV-*` artifacts are deleted only after both replacement platform builds succeed. This keeps the newest known-good pair without stacking old development installers.

History-only `main` → `dev` synchronization pushes contain no changed files, so the path-filtered development packaging workflow does not rerun for those sync commits. CI remains separate and lightweight.

## Community release validation

A public Community Release is published only when both target-platform jobs succeed and both final manifests report `community-validated`.

The macOS job verifies the ARM64 frozen application, creates the DMG, mounts that final DMG, and runs the packaged self-test from inside it.

The Windows job verifies the frozen executable, builds the Inno Setup installer, installs that final installer, runs the installed self-test, uninstalls the application, and verifies removal.


## Release artifact lifecycle

Community release jobs use temporary Actions artifacts only to transfer the validated macOS and Windows packages into the publish job. They have a 3-day fallback retention. After `gh release create` succeeds and the files are attached to the GitHub Release, a cleanup job deletes the temporary `community-*` workflow artifacts from that run. If publication fails, the cleanup job does not run, preserving the staging artifacts for diagnosis/retry.

## Packaged UI layout validation

Before a public release, exercise the packaged application on macOS Apple Silicon and Windows x64 at the normal minimum window size and at larger/taller window sizes.

For the **Map Backporter** page specifically, verify that:

- the Conversion job form remains left/top aligned instead of adopting platform-specific centered form geometry;
- Source map, Target version, Backend, Template world, and Output world rows retain normal control height and do not overlap;
- path fields expand horizontally with the page while their browse buttons remain visible;
- the Target version selector remains a compact selector instead of stretching across the entire form;
- the Surface / compatibility options group retains normal row height and compact numeric inputs;
- at short window heights the Backporter page scrolls vertically instead of crushing configuration rows;
- the scroll viewport/body uses the same application canvas colour, with no native grey bands behind group-box titles, button rows, or progress controls;
- the desktop minimum height keeps the normal configuration area usable while scrolling remains a fallback for constrained displays;
- the architectural replacement option uses generic mod-facing wording while the current 1.7.10 backend continues to document its HBM mapping support;
- at normal/tall heights the output/log panel receives the remaining vertical stretch.

Also verify the **Mod / JAR Analyzer**, **Modpack Analyzer**, and **Catalog Workspace** tables at the minimum window width. Column headings such as `Registry hint`, `Confidence`, `Block candidates`, and `Texture assets` must begin at readable widths. User resizing remains interactive, but each column has a per-column readability floor that includes room for the active sort indicator, so dragging a boundary cannot clip its heading. A horizontal scrollbar is preferable to violating those floors. The JAR Analyzer preview/table splitter must not collapse either child completely.

The source verifier enforces the explicit Qt form/scroll sizing policy and interactive analyzer-header policy so these behaviours stay deterministic across platform styles.
