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


## World-content audit, staged output, and legacy round-trip validation

Patch 013 deliberately separates **terrain/block conversion** from world content that is not yet safe to translate across modern Java → Forge 1.7.10.

Before conversion, verify that:

- modern chunk `block_entities` are counted by ID during preflight instead of being silently ignored;
- when the selected source is a full world folder, a `region` folder with a sibling `entities` folder, or a ZIP containing `entities/*.mca`, modern entity-region records are counted by ID;
- region-only inputs that do not expose entity-region data are reported as `entity audit unavailable` rather than being treated as zero entities;
- the preflight summary clearly states that entities/block entities are currently placed in a loss manifest and are not yet translated;
- the conversion fingerprint includes sibling entity-region files for directory-based world inputs so entity changes make a previous preflight stale.

The current 1.7.10 writer must continue to:

- serialize `Entities` and `TileEntities` as empty legacy lists until an explicit translator exists;
- report the number/type of omitted block entities and audited source entities;
- identify the content policy as `terrain_blocks_with_loss_manifest`;
- identify the block-property strategy as `source_properties_to_legacy_metadata_plus_runtime_neighbors`;
- report how many unique source palette states carried properties and which property keys were observed;
- identify the lighting strategy as `target_runtime_relight`;
- identify the bootstrap heightmap strategy as `bootstrap_highest_non_air`;
- serialize output chunks with `LightPopulated=0`.

Before a requested output world is exposed, verify the staged-output lifecycle:

1. preflight completes successfully;
2. a hidden sibling staging clone is created;
3. converted chunk NBT is structurally checked before region writing;
4. each completed region is reopened through the Anvil reader;
5. every written chunk is validated for coordinate/index agreement, section Y bounds, array lengths, biome/heightmap sizes, and `LightPopulated=0`;
6. only a zero-failure, fully round-trip-verified staging world is promoted to the requested output path.

If any chunk or round-trip verification fails, the requested output world must remain absent. The staging directory must be cleaned up, and external `<output>.WG_BACKPORT_FAILED_REPORT.json/.txt` diagnostics must be written beside the requested output location.

For the next packaged functional conversion test, use a disposable output path and first run **Preflight conversion**. Confirm the content-audit counts are plausible before clicking Convert. On a successful conversion, confirm the report says `Output status: PROMOTED`, converted/verified region and chunk counts match, the loss manifest is explicit, and the application says the output requests target-side relighting. Only then open the converted world in the exact target Forge 1.7.10 modpack.

## Analyzer → Catalog Workspace and persistence validation

Before the next integrated map conversion test, verify the packaged application also preserves the analyzer/workspace workflow:

- analyse a known JAR and confirm **Add to Catalog Workspace** is enabled beside **Export catalog JSON…**;
- click **Add to Catalog Workspace** and confirm the catalog appears immediately without exporting/re-importing JSON;
- analyse a local modpack and confirm **Add catalogs to Workspace** adds its available embedded mod catalogs directly;
- confirm the application creates `WG Map Backporter Studio/Catalogs`, `Workspaces`, and `Exports` under the platform Documents location;
- confirm `Workspaces/default-workspace.json` is updated automatically after adding/removing a catalog and after changing a catalog checkbox;
- restart the packaged application and confirm the default workspace, source enable/disable state, and active candidate pool are restored automatically;
- confirm a directly added analyzer catalog is also snapshotted under the application `Catalogs` directory;
- confirm **Save workspace copy…** defaults to the application `Workspaces` directory while remaining user-selectable;
- confirm **Export catalog JSON…** and **Export combined analysis…** default to the application `Catalogs` directory while remaining user-selectable;
- confirm **Storage folder** opens the user-visible application storage root;
- after restoring a workspace, run Map Backporter preflight and confirm the restored enabled catalogs participate in the active mapping profile exactly as they did before restart.


## Cross-generation JAR analysis and backport-provider validation

For the analyzer/provider work, verify all of the following before relying on a new catalog:

- legacy Forge JARs without `mcmod.info` can still be identified from manifest/class evidence when possible;
- enum-backed block registries are discovered instead of collapsing to texture-only candidates;
- modern Forge/NeoForge/Fabric/Quilt blockstate/model layouts remain discoverable;
- packaged legacy `TileEntity` and modern `BlockEntity` subclasses are listed separately from blocks;
- the right-hand preview uses packaged static JSON/OBJ/texture information and never executes a mod's custom renderer;
- HBM remains an architectural fallback rather than receiving automatic exact-name mapping authority;
- recognized backport-provider catalogs are carried separately in the active workspace snapshot;
- an exact provider target is selected only when the target world registry actually contains it;
- a provider never shadows a vanilla block already present in the target version;
- changing an enabled provider/catalog invalidates the verified conversion preflight;
- preflight reports placed, in-range, non-air block counts by mapping quality plus the highest-impact non-exact mappings;
- the text/JSON conversion reports preserve those mapping-impact diagnostics.

Real-JAR regression targets used during development include Et Futurum Requiem, UpToDateMod, and HBM NTM. Exact candidate counts can change as those mods evolve, so validation should assert plausible non-trivial discovery rather than hard-coding upstream inventories into application behavior. Static discovery is intentionally best-effort for custom runtime-generated/obfuscated registrations.
