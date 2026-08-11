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

Also verify the **Mod / JAR Analyzer**, **Modpack Analyzer**, and **Catalog Workspace** tables while resizing the application horizontally. Each analyzer column starts from the same text-relative sizing rule: rendered heading width plus a fixed sort/padding allowance and a small consistent comfort margin. Columns automatically contract toward their readable minimums as the window narrows instead of remaining at oversized fixed widths. User resizing remains interactive, but a column cannot be dragged below its readable heading floor; if all floors cannot fit, use a horizontal scrollbar rather than clipping headings. Check the active sort indicator on every sortable heading, including `Registry hint`, `Confidence`, `Block candidates`, and `Texture assets`. The JAR Analyzer preview/table splitter must not collapse either child completely.

The source verifier enforces the explicit Qt form/scroll sizing policy and interactive analyzer-header policy so these behaviours stay deterministic across platform styles.


## Legacy mod analysis and multi-catalog workspace validation

For a packaged functional test, analyse at least one Forge 1.7.10 mod that includes multiple localisation files and legacy/custom models. Confirm that:

- `en_US` display names win even when another locale appears earlier in the JAR;
- when legacy static `Block` fields can be recovered, arbitrary `textures/blocks` assets are not promoted into separate registered-block candidates;
- legacy OBJ/DAE/HMF/TCN model assets are counted and exact-name associations appear in the `Models` column;
- confidence/evidence varies according to blockstate, class-file, model, texture, and localisation evidence instead of every legacy entry reporting `low`;
- detected TileEntity subclasses are reported as analysis evidence without claiming that tile-entity rendering/model binding is fully implemented;
- sorting the JAR Analyzer table does not make texture previews point at a different underlying candidate;
- manually resized analyzer columns remain user-selected until an actual window-width change requires contraction, and expanding the window restores the remembered preferred widths.

For **Catalog Workspace**, load two or more individual mod catalog JSONs and at least one modpack analysis. Confirm that loading adds sources rather than replacing existing ones, each source has an independent checkbox, disabling a source removes its rows from the active count/view, `Remove selected` and `Clear all` behave as labelled, duplicate imports do not duplicate every row, and `Save workspace…` produces a JSON that can be loaded again with its enabled/disabled states preserved.

## Forge 1.7.10 registry and conversion preflight validation

Before trusting a modern → Forge 1.7.10 conversion, verify the target/template world was opened and saved in the exact destination Forge 1.7.10 modpack. Forge 1.7.10 persists blocks and items together in `FML/ItemData`; the first character of each key distinguishes block entries (`U+0001`) from item entries (`U+0002`). The backporter must strip that discriminator, retain only block entries for block-ID resolution, and reject an unusable registry snapshot before creating an output world.

Repository/runtime validation covers the following invariants:

- ordinary vanilla names such as `minecraft:air`, `minecraft:stone`, and `minecraft:bedrock` resolve from a synthetic Forge 1.7.10 `ItemData` snapshot;
- item entries are ignored instead of being mixed into the block registry;
- HBM block names remain discoverable after the discriminator is removed;
- a target registry missing core vanilla sentinel blocks is rejected before output creation;
- every in-range unique source palette state is mapped and resolved during a source/target preflight before the template world is cloned;
- preflight parse or mapping failures leave the requested output folder untouched;
- post-preflight chunk failures are aggregated by error and only a bounded set of examples is written to the report;
- the desktop UI distinguishes a clean conversion from a conversion that completed with chunk failures.

For a packaged functional test, use a newly created/saved Forge 1.7.10 template and a new empty output path. The conversion log should identify the registry as `Forge 1.7.10 FML/ItemData`, report a nonzero HBM count when HBM is installed in that template, complete the source/target preflight, and only then state that the template was cloned and conversion started.


## Active catalog mapping-profile and reusable preflight validation

The packaged desktop application links **Catalog Workspace** to **Map Backporter**. Enabled catalog sources provide the mod namespaces that are eligible for reviewed safe mapping rules; the target/template world's Forge registry still determines whether a concrete target block actually exists. A catalog never automatically invents an unreviewed source→target mapping pair.

Verify the following:

- load an HBM catalog, leave it enabled, and confirm Map Backporter reports `hbm` in the active catalog namespaces;
- disable that catalog and confirm the Backporter immediately marks the existing preflight stale and reports that safe mod rules will fall back to vanilla targets;
- re-enable HBM and confirm a new preflight can use reviewed HBM architectural targets when those targets exist in the template registry;
- enable a catalog for a mod with no reviewed mapping rules and confirm it is reported in the profile without causing arbitrary target substitutions;
- changing source, template, target version, vertical offset, strip/fill setting, safe-mod replacement setting, or enabled catalog state invalidates the previous preflight;
- changing only the output folder does not invalidate a still-current read-only preflight;
- `Convert map` remains disabled until the exact current inputs have a successful preflight;
- a successful preflight creates no output world and reports target registry information, mapping-profile information, source chunk count, unique in-range palette-state count, and potential Y-range cropping;
- an unchanged preflight is reused by conversion rather than scanning every source chunk twice;
- if files/settings/catalogs change after preflight, the engine fingerprint refuses to reuse the stale result and reruns preflight before output creation;
- the 1.7.10 target exposes a **Use recommended** action that selects vertical offset `0` and strip/fill below Y `0` for normal surface/RTG alignment;
- `WG_BACKPORT_REPORT.json` and `.txt` record the mapping profile and whether the verified preflight was reused.
