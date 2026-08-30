# WG Map Backporter Studio

> Cross-platform desktop tooling for backporting Minecraft Java worlds, analysing mod/modpack block assets, and building reusable source-to-target block mappings.

[![CI](https://github.com/RhysHopkins04/Minecraft-Map-Backporter-Studio/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/RhysHopkins04/Minecraft-Map-Backporter-Studio/actions/workflows/ci.yml)
[![Dev Build](https://github.com/RhysHopkins04/Minecraft-Map-Backporter-Studio/actions/workflows/build-dev.yml/badge.svg?branch=dev)](https://github.com/RhysHopkins04/Minecraft-Map-Backporter-Studio/actions/workflows/build-dev.yml)
[![Community Release](https://github.com/RhysHopkins04/Minecraft-Map-Backporter-Studio/actions/workflows/release-community.yml/badge.svg?branch=main)](https://github.com/RhysHopkins04/Minecraft-Map-Backporter-Studio/actions/workflows/release-community.yml)

**Current version:** `0.4.1`
**Current conversion writer:** modern Java Anvil → Forge/Minecraft **1.7.10**
**Desktop targets:** macOS **Apple Silicon** and Windows **x64**

WG Map Backporter Studio is being developed as a reusable Minecraft world-conversion and block-analysis application rather than a one-off conversion script.

## What it does

### Map Backporter

The current working backend converts modern palette-based Minecraft Java Anvil chunks into legacy Forge/Minecraft 1.7.10 chunk storage.

It can:

- read a world folder, `region` folder, ZIP, or individual `.mca` file,
- stage a clone of a real 1.7.10 Forge target/template world and only promote it after successful conversion/verification,
- resolve the target world's persisted Forge/FML registry instead of hard-coding mod numeric IDs,
- write legacy `Blocks`, `Data`, and `Add` arrays,
- preserve and translate surface structures while reporting unavoidable height loss,
- apply conservative, reviewed mod architectural substitutions only from namespaces enabled in Catalog Workspace,
- leave source/template inputs untouched,
- run a read-only conversion preflight before output creation and reuse it when the source/template/settings/catalog profile remain unchanged,
- audit modern block entities and, when a full world/ZIP exposes them, modern entity-region records before conversion,
- write an explicit loss manifest for entities/block entities that the current terrain-focused backend does not yet translate,
- translate modern block-state properties into legacy metadata where that state exists, while leaving legacy runtime-neighbour-derived shapes/connections to the target runtime,
- emit legacy chunks with a bootstrap heightmap and `LightPopulated=0` so target-side relighting is requested,
- round-trip validate every written legacy chunk/region before the staged world can be promoted,
- generate conversion reports for approximate, unsupported, cropped, active mapping-profile, content-loss, lighting, and verification decisions.

The original large validation map used during development contains 34 populated regions and 27,761 populated chunks and uses a modern palette-based chunk format.

### Mod / JAR Analyzer

The analyzer performs bounded static inspection without loading or executing the mod. It understands common legacy and modern metadata layouts (legacy Forge/FML, modern Forge/NeoForge, Fabric, Quilt, and LiteLoader) and also uses packaged bytecode/assets when formal metadata is absent. It can discover:

- legacy static `Block` holder fields and enum-backed registries such as Et Futurum Requiem's `ModBlocks`,
- modern blockstate/model-driven block candidates,
- packaged `TileEntity` / `BlockEntity` subclasses and block-entity type evidence,
- JSON block models plus legacy OBJ/DAE/HMF/TCN assets where packaged,
- block textures and English-first display/localization hints,
- likely registry-name hints with confidence/evidence labels,
- a conservative right-side **packaged inventory/item icon** preview that resolves exact legacy `textures/items` / modern item-model layer assets and refuses to substitute block textures, OBJ/TESR/BER model textures, or fuzzy same-name files when an inventory representation cannot be proven,
- a provider role that distinguishes ordinary catalogs, reviewed architectural fallbacks, and likely vanilla-content backport providers.

The goal is broad coverage from the 1.7.10 era through modern 1.21-era JAR layouts, not a claim that arbitrary runtime-generated registration can always be reconstructed statically. Published mods can use custom registries, obfuscated/intermediary names, runtime renderers, or generated assets that cannot be proven without actually loading that exact Minecraft/loader environment; those cases remain explicitly advisory rather than being invented.

For legacy Forge mods, class/enum evidence is stronger than blindly treating every file under `textures/blocks` as registered. Backport providers can also associate a registered provider block with faithful assets intentionally packaged under `assets/minecraft`, as Et Futurum does. Static model/texture evidence is still retained in the catalog for human review and matching diagnostics, but the desktop preview no longer tries to reconstruct those models. Instead it shows only a confidently associated packaged inventory representation: an exact legacy `assets/<namespace>/textures/items/...` / `textures/item/...` sprite, or an explicit modern item-model layer texture. Only the selected block's exact namespaced registry basename may establish a direct legacy item-sprite or item-model identity; TileEntity/BlockEntity class names, display names, prefix-stripped names, and broad substring/fuzzy matches are intentionally rejected because they can collide with unrelated items. If no inventory icon can be proven, the preview says so rather than substituting a block face or model atlas. This visual preview is strictly presentation-only and never changes catalog mapping or conversion targets.

### Modpack Analyzer

Local instances, ZIPs, and CurseForge-style exports can be scanned to build a combined target-block catalog. JARs physically present in the pack can be analysed directly; manifest-only entries remain unresolved until their actual files are available.

### Catalog Workspace

The workspace can combine multiple individual mod catalogs and modpack analyses at the same time. Each loaded catalog can be enabled/disabled independently, removed, searched as part of the active target-block pool, and saved in a reusable workspace JSON. Analyzer results can also be sent directly into Catalog Workspace without an export/import round trip.

Catalog Workspace is persistent by default. On first use, the desktop application creates a user-visible storage tree under the platform Documents location:

```text
WG Map Backporter Studio/
├── Catalogs/
├── Workspaces/
│   └── default-workspace.json
└── Exports/
```

Adding, removing, enabling, or disabling a catalog automatically updates `default-workspace.json`, and that workspace is restored on the next application launch. Catalog snapshots added to the workspace are also stored under `Catalogs/`. Manual **Save workspace copy…** and JSON export actions remain available for sharing or archival.

The Map Backporter consumes the enabled catalog set as a live mapping profile. Ordinary catalogs enable only reviewed rules for their namespaces. Catalogs classified as **backport providers** may additionally contribute conservative same-name modern-vanilla targets, but only when the actual target/template world's Forge registry confirms that exact provider block is registered. This lets an enabled UpToDate/Campfire-style provider outrank a poor HBM/vanilla approximation without trusting an asset catalog as a numeric-ID authority.

**Et Futurum Requiem is additionally treated as a first-class 1.7.10 target provider.** When the selected template's Forge registry contains registered `etfuturum:*` blocks, the writer tries a matching Et Futurum identity before catalog providers, HBM architectural substitutions, or vanilla approximations. This works without manually adding an Et Futurum catalog: the saved target registry proves which blocks are actually enabled. It also does not depend on the JAR containing Mojang textures/models, so Et Futurum Plus builds that obtain modern assets through their launch-time asset downloader remain fully usable as mapping targets. Exact modern identities are preferred first, followed by a bounded set of established Et Futurum packed-subtype mappings such as bountiful stone, prismarine, concrete, modern wood families, deepslate/tuff variants, and copper families. A real 1.7.10 vanilla identity is never displaced.

Provider catalogs are therefore advisory, not proof that a target block is available. Preflight reports each provider namespace's actual registered block count, the number of catalog targets confirmed by the selected template, and high-impact source blocks whose provider candidate is absent because the feature is disabled or the template was created without it. When Et Futurum is present, preflight also states that native target-registry priority is active and reports its registered block count.

Preflight also counts actual in-range non-air block placements by mapping quality and reports the highest-impact non-exact mappings. This makes it possible to prioritize a bad replacement used tens of thousands of times instead of manually hunting through every unique palette state.

## Target versions

The architecture is intended to support multiple target eras, including 1.7.10 through 1.16.5, but **1.7.10 is currently the only conversion writer considered implemented**.

Unsupported target selectors must refuse conversion rather than silently produce a world that only appears valid.

Planned future work includes:

- visual block-mapping workspace,
- texture/material/geometry similarity scoring,
- manual/visual source→target mapping overrides layered on top of the live catalog-gated profile,
- richer Forge/Fabric/NeoForge mod analysis,
- CurseForge/Modrinth dependency resolution,
- validated 1.12.2 and 1.16.5 writers,
- safer tile-entity/entity conversion,
- more complete surface-only extraction modes.

## Downloads

End users should use the files published on the repository's **Releases** page rather than cloning the source.

Community releases provide:

- `WGMapBackporterStudio-<version>-macOS-AppleSilicon.dmg`
- `WGMapBackporterStudio-<version>-Windows-x64-Setup.exe`
- SHA-256 checksum files for both packages

These community builds are intentionally **unsigned**. They are built and smoke-tested on the target operating system, but they are not Apple-notarized and do not carry a paid Windows publisher certificate.

See [`docs/INSTALLATION.md`](docs/INSTALLATION.md) for the first-launch process.

## Community-build verification

A successful release workflow must independently validate both supported platforms.

### macOS Apple Silicon

GitHub Actions:

1. runs source checks,
2. builds the native ARM64 `.app`,
3. confirms the packaged executable is ARM64,
4. runs the frozen application's `--self-test`,
5. creates a drag-to-Applications DMG,
6. mounts the final DMG,
7. runs the self-test from the application inside that DMG,
8. generates a SHA-256 checksum and validation manifest.

### Windows x64

GitHub Actions:

1. runs source checks,
2. builds the native x64 application,
3. runs the packaged `--self-test`,
4. compiles the Inno Setup installer,
5. installs the final Setup EXE silently on the CI machine,
6. runs the installed application's `--self-test`,
7. uninstalls it and verifies removal,
8. generates a SHA-256 checksum and validation manifest.

The publishing job refuses to create the GitHub Release unless **both** platform manifests report `community-validated`. Development builds use the separate `dev-validated` status and remain temporary GitHub Actions artifacts.

## Development and release channels

The repository uses two long-lived branches:

- `dev` is the active development/staging branch. Every push runs CI and produces temporary, validated macOS Apple Silicon and Windows x64 installer artifacts. Development artifacts have a 7-day fallback retention and are **not** published as GitHub Releases.
- `main` is the stable public-release branch. Changes should normally reach `main` through a pull request from `dev`. A Community Release is created only when the release version is intentionally advanced, or when the initial version is explicitly published through the workflow's manual control.

A normal development cycle is therefore `feature/fix work → dev → validated development artifacts → dev-to-main release PR → main → validated Community Release`.

The application is implemented in Python using PySide6/Qt and packaged with PyInstaller.

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1

python -m pip install -r requirements-build.txt
python -m pip install -e .
python scripts/verify_project.py
python tests/test_smoke.py
python -m wgmap_backporter_studio
```

A local PyInstaller build can be produced with:

```bash
python -m PyInstaller --clean --noconfirm WGMapBackporterStudio.spec
```

PyInstaller is not a cross-compiler; official community release jobs build on the operating system they target.

## Repository safety

Do not commit real Minecraft worlds, region files, mod JARs, exported modpacks, signing keys, certificates, or other large/private test inputs. The repository `.gitignore` intentionally blocks the common forms of these files.

## Licensing

This project is **source-available, not open source**.

Original project code is made available under the **PolyForm Strict License 1.0.0**. In broad terms, the licence permits qualifying non-commercial use but does not grant permission to distribute the software or make modified/derivative versions.

Read [`LICENSE.md`](LICENSE.md) and [`docs/LICENSING.md`](docs/LICENSING.md) before using the source.

Third-party dependencies retain their own licences; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Contributing

Bug reports, compatibility information, and feature suggestions are welcome. Code pull requests are not accepted by default while the project's contribution/licensing model is being established. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Security

Please do not post exploitable archive/parser issues publicly. See [`SECURITY.md`](SECURITY.md).

## Disclaimer

Minecraft is a trademark of Microsoft Corporation. WG Map Backporter Studio is not affiliated with, endorsed by, sponsored by, or approved by Mojang Studios or Microsoft Corporation. Third-party Minecraft mods and their assets remain the property of their respective owners.
