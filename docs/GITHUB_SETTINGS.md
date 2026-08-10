# GitHub Repository Settings

This repository uses a two-channel workflow:

- `dev` — active development and temporary validated development installers.
- `main` — stable source and public Community Releases.

Do not create the permanent `dev` branch until Repository Patch 001 has been applied, validated, committed, and pushed to `main`. Creating it earlier would branch from the incomplete initial repository.

## 1. General repository settings

Open **Settings → General**.

### Pull Requests

Recommended:

- **Allow merge commits:** OFF
- **Allow squash merging:** ON
- **Allow rebase merging:** OFF
- **Always suggest updating pull request branches:** ON
- **Allow auto-merge:** optional; leave OFF initially
- **Automatically delete head branches:** OFF

`dev` is a permanent branch, so automatic head-branch deletion is intentionally disabled. Short-lived feature branches can be deleted manually after merge.

## 2. GitHub Actions

Open **Settings → Actions → General**.

### Actions permissions

Use the restrictive option that allows this repository plus selected external actions, and allow **actions created by GitHub**. The current workflows use GitHub-maintained actions such as `actions/checkout`, `actions/setup-python`, `actions/upload-artifact`, and `actions/download-artifact`.

Do not enable a requirement that all actions be pinned to full commit SHAs yet; the current workflow references use major-version tags. This can be tightened later as a separate supply-chain-hardening patch.

### Fork pull request workflow approval

For a public repository, select the strictest available option that requires approval before workflows from outside contributors run. The project executes Python tests and build steps, so untrusted fork code should not automatically receive runner execution.

### Workflow permissions

Recommended:

- **Read repository contents and packages permissions:** ON
- **Read and write permissions:** OFF
- **Allow GitHub Actions to create and approve pull requests:** OFF

The Community Release workflow declares its own narrow `contents: write` permission so it can create a version tag and GitHub Release after validation.

### Artifact and log retention

Recommended repository default: **30 days**.

Development installers use a **7-day fallback retention**, but after a successful native build the workflow automatically deletes older `DEV-*` artifacts and keeps only the newest successful macOS/Windows pair. Community-release staging artifacts use a **3-day fallback retention** and are deleted after the GitHub Release is successfully published; the published Release assets remain available independently of Actions artifact storage.

## 3. Security settings

Open **Settings → Advanced Security** (wording can vary slightly as GitHub evolves the UI).

Recommended:

- **Dependabot alerts:** ON
- **Dependabot security updates:** ON
- **Dependabot malware alerts:** ON if offered
- **Private vulnerability reporting:** ON
- **Secret scanning / Secret Protection:** ON where GitHub offers the control
- **Push protection:** ON where available
- **CodeQL analysis:** Default setup, Python

Do not make CodeQL a required merge check until its first scans have completed successfully.

The repository includes `.github/dependabot.yml` so routine GitHub Actions dependency updates are proposed weekly against `dev`. Routine pip version-update PRs are intentionally disabled; vulnerability-driven Dependabot security updates remain separate.

## 4. Branch creation order

After Repository Patch 001 is committed and pushed to `main` and the new CI run is green:

1. Create `dev` directly from the updated `main`.
2. Push `dev` to GitHub.
3. Confirm the **Development builds** workflow runs on `dev`.
4. Confirm both macOS Apple Silicon and Windows x64 development artifacts are produced.
5. Only then activate the full `main` ruleset described below.

## 5. `main` branch ruleset

Open **Settings → Rules → Rulesets → New ruleset → New branch ruleset**.

Use:

- **Ruleset name:** `Stable main`
- **Enforcement status:** Active
- **Bypass list:** none initially
- **Target:** Include branch `main` (or the default branch selector while `main` is default)

Enable:

- **Restrict deletions**
- **Require linear history**
- **Require a pull request before merging**
- **Require conversation resolution before merging**
- **Require status checks to pass** — but add this only after CI has produced the check once
- **Block force pushes**

For pull-request reviews:

- Required approving reviews: **0** for now. You are currently the sole maintainer, so requiring another reviewer would deadlock normal releases.
- Dismiss stale approvals: OFF
- Require review from Code Owners: OFF
- Require approval of the most recent push: OFF

For required status checks, add:

- `Repository validation`

Enable **Require branches to be up to date before merging** after the `Repository validation` check exists and has run successfully.

Leave these OFF for now:

- Require signed commits
- Require deployments to succeed
- Merge queue
- Restrict creations
- Restrict updates
- Required CodeQL/code-scanning results (until CodeQL has proven stable)

## 6. `dev` branch ruleset

After `dev` exists, create another **branch ruleset**:

- **Ruleset name:** `Development branch safety`
- **Enforcement status:** Active
- **Target:** Include branch `dev`
- **Bypass list:** none

Enable only:

- **Restrict deletions**
- **Block force pushes**

Optional:

- **Require linear history**

Do **not** require a pull request or status checks on `dev` yet. The current workflow intentionally lets you push development commits directly to `dev`; CI and development packaging then run after the push. If more developers join later, change `dev` to a PR-only integration branch.

## 7. Release tag ruleset

Create **New ruleset → New tag ruleset**:

- **Ruleset name:** `Release tags`
- **Enforcement status:** Active
- **Target tags:** pattern `v*`

Enable protections that prevent existing matching tags from being updated or deleted.

Do **not** restrict creation of matching tags, because the Community Release workflow must be able to create a new `vX.Y.Z` tag after both installers validate.

## 8. Release model

### Development build

A push to `dev` triggers:

1. CI/source validation.
2. Native Apple Silicon packaging on GitHub's macOS ARM runner.
3. Native Windows x64 packaging on GitHub's Windows runner.
4. Packaged self-tests.
5. DMG / installer validation.
6. Temporary downloadable Actions artifacts with a 7-day fallback retention.
7. After both platform builds succeed, older `DEV-*` artifacts are deleted so only the newest successful pair is retained.

History-only `main` → `dev` synchronization merges and documentation-only changes do not trigger the native development packaging workflow when GitHub reports no build-relevant changed files. Lightweight `CI` still runs on ordinary `dev` pushes.

No GitHub Release or permanent version tag is created.

### Public Community Release

A normal `main` push always runs release preflight but publishes only when the release version was intentionally advanced.

The version is tracked in `release/VERSION` and must match the application/package metadata. When a release PR changes that version and is merged from `dev` to `main`, the Community Release workflow builds both platforms and only publishes after both validation manifests report `community-validated`.

After both release packages have been published as GitHub Release assets, the workflow removes the temporary `community-*` Actions artifacts from that run. If publication fails, cleanup does not run and the temporary artifacts remain available until their 3-day fallback expiration.

The first `0.4.1` release is deliberately special: adding `release/VERSION` in Repository Patch 001 does **not** auto-publish. Once the `dev` pipeline is proven, the initial version can be published manually from **Actions → Community Release → Run workflow**, with `publish_current_version` enabled.

## 9. Normal working pattern

For current solo development:

1. Work on `dev` for ordinary patches.
2. Push and inspect CI + temporary development installers.
3. When a release is ready, update the application version consistently and update `release/VERSION`.
4. Open a PR from `dev` to `main`.
5. Let required checks pass.
6. Squash merge the PR.
7. `main` automatically builds and publishes the validated Community Release.
8. Merge/sync `main` back into `dev` if the release merge created any history difference that `dev` does not yet contain.

For larger changes later, use short-lived `feature/...` or `fix/...` branches targeting `dev`.
