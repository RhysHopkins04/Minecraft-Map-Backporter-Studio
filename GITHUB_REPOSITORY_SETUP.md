# GitHub Repository Setup

## Recommended repository

**Owner:** `RhysHopkins04`  
**Repository name:** `Minecraft-Map-Backporter-Studio`  
**Visibility:** Public

**Description:**

> Cross-platform desktop toolkit for backporting Minecraft Java worlds, analysing mod/modpack block assets, and building reusable source-to-target block mappings. Apple Silicon macOS and Windows x64 community builds.

Suggested topics:

`minecraft`, `minecraft-java`, `map-converter`, `world-converter`, `anvil`, `nbt`, `forge`, `modding`, `pyside6`, `minecraft-tools`

## When creating the repository on GitHub

Create the repository **empty**:

- do not add GitHub's generated README,
- do not add a GitHub-generated `.gitignore`,
- do not choose another licence from the GitHub licence picker.

This project already includes all three and uses a source-available licensing model that is intentionally different from MIT/GPL/Apache.

After the files are pushed, enable GitHub Actions. If available, also enable **Private vulnerability reporting** under the repository Security settings.

## First release

Once the initial repository is pushed and CI is green, create/tag `v0.4.1`. The `release-community.yml` workflow is designed to build and publish exactly two validated community artifacts:

- macOS Apple Silicon DMG
- Windows x64 Setup EXE

No Apple Developer or Windows code-signing secrets are required for the community workflow.
