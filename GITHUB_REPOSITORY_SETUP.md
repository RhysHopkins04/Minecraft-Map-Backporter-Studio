# GitHub Repository Setup

## Repository

**Owner:** `RhysHopkins04`
**Repository:** `Minecraft-Map-Backporter-Studio`
**Visibility:** Public
**Default/stable branch:** `main`
**Development branch:** `dev`

**Description:**

> Cross-platform desktop toolkit for backporting Minecraft Java worlds, analysing mod/modpack block assets, and building reusable source-to-target block mappings. Apple Silicon macOS and Windows x64 community builds.

Suggested topics:

`minecraft`, `minecraft-java`, `map-converter`, `world-converter`, `anvil`, `nbt`, `forge`, `modding`, `pyside6`, `minecraft-tools`

## Repository Patch 001 bootstrap

The initial GitHub import accidentally omitted the contents of `.github/`, `src/`, `scripts/`, `tests/`, `packaging/`, `resources/`, and `docs/`.

Repository Patch 001 restores those files and establishes the two-branch CI/release model. Apply and validate that patch on `main` before creating the permanent `dev` branch.

## Branch model

- `dev`: normal active development; CI plus temporary validated development installers.
- `main`: stable source and public Community Releases.

Development artifacts are uploaded to GitHub Actions rather than cluttering the public Releases page. A normal release is promoted through a `dev → main` pull request.

## First release

Do not tag `v0.4.1` before the development packaging workflow has successfully produced both target-platform artifacts.

Repository Patch 001 introduces `release/VERSION` without automatically publishing it. After the `dev` branch pipeline is proven, the initial version can be published manually from the **Community Release** workflow using its `publish_current_version` input.

Subsequent releases are version-gated: advancing `release/VERSION` as part of a release PR and merging that PR to `main` triggers the full validated Community Release pipeline automatically.

See [`docs/GITHUB_SETTINGS.md`](docs/GITHUB_SETTINGS.md) for the complete repository settings, Actions permissions, security settings, and branch/tag rulesets.
