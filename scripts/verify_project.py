from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
errors: list[str] = []


def error(message: str) -> None:
    errors.append(message)


for path in root.rglob("*.py"):
    if any(part in {".git", ".build-venv", ".venv", "build", "dist", "__pycache__"} for part in path.parts):
        continue
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception as exc:
        error(f"{path.relative_to(root)}: {exc}")

required = [
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    ".github/workflows/build-dev.yml",
    ".github/workflows/release-community.yml",
    ".gitignore",
    ".gitattributes",
    "README.md",
    "LICENSE.md",
    "NOTICE.md",
    "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "GITHUB_REPOSITORY_SETUP.md",
    "docs/INSTALLATION.md",
    "docs/LICENSING.md",
    "docs/GITHUB_SETTINGS.md",
    "release/VERSION",
    "packaging/macos/create_dmg.sh",
    "packaging/macos/verify_community_dmg.sh",
    "packaging/windows/WGMapBackporterStudio.iss",
    "packaging/windows/install_smoke_test.ps1",
    "packaging/windows/verify_packaged_exe.ps1",
    "packaging/windows/version_info.txt",
    "resources/app_icon.icns",
    "resources/app_icon.ico",
    "src/wgmap_backporter_studio/app.py",
    "src/wgmap_backporter_studio/core/legacy1710_engine.py",
    "src/wgmap_backporter_studio/ui/main_window.py",
    "tests/test_smoke.py",
]
for rel in required:
    if not (root / rel).is_file():
        error(f"missing {rel}")

version_path = root / "src/wgmap_backporter_studio/__init__.py"
version_text = version_path.read_text(encoding="utf-8") if version_path.exists() else ""
match = re.search(r'__version__\s*=\s*"([^"]+)"', version_text)
version = match.group(1) if match else None
if not version:
    error("could not resolve package __version__")
    version = "UNKNOWN"
elif not re.fullmatch(r"\d+\.\d+\.\d+", version):
    error(f"package version {version!r} is not a numeric semantic version X.Y.Z")

release_version_path = root / "release/VERSION"
release_version = release_version_path.read_text(encoding="utf-8").strip() if release_version_path.exists() else None
if release_version != version:
    error(f"release/VERSION is {release_version!r}, expected {version!r}")

pyproject_path = root / "pyproject.toml"
pyproject = pyproject_path.read_text(encoding="utf-8") if pyproject_path.exists() else ""
if f'version = "{version}"' not in pyproject:
    error(f"pyproject.toml does not contain project version {version}")

spec_path = root / "WGMapBackporterStudio.spec"
spec = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
for token in [
    f'CFBundleShortVersionString": "{version}',
    f'CFBundleVersion": "{version}',
    "io.github.rhyshopkins04.wg-map-backporter-studio",
]:
    if token not in spec:
        error(f"PyInstaller spec missing expected token: {token}")

version_info_path = root / "packaging/windows/version_info.txt"
version_info = version_info_path.read_text(encoding="utf-8") if version_info_path.exists() else ""
parts = version.split(".") if version != "UNKNOWN" else []
if len(parts) == 3 and all(part.isdigit() for part in parts):
    tuple_token = f"({int(parts[0])}, {int(parts[1])}, {int(parts[2])}, 0)"
    if version_info.count(tuple_token) < 2:
        error(f"Windows version_info.txt does not contain expected file/product tuple {tuple_token} twice")
for token in [f"StringStruct('FileVersion', '{version}')", f"StringStruct('ProductVersion', '{version}')"]:
    if token not in version_info:
        error(f"Windows version_info.txt missing expected token: {token}")

readme_path = root / "README.md"
readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
if f"**Current version:** `{version}`" not in readme:
    error(f"README current version does not match {version}")
for badge in ["ci.yml/badge.svg?branch=main", "build-dev.yml/badge.svg?branch=dev", "release-community.yml/badge.svg?branch=main"]:
    if badge not in readme:
        error(f"README missing expected workflow badge: {badge}")

ci_path = root / ".github/workflows/ci.yml"
ci = ci_path.read_text(encoding="utf-8") if ci_path.exists() else ""
for token in ["- main", "- dev", "name: Repository validation", "permissions:", "contents: read"]:
    if token not in ci:
        error(f"CI workflow missing: {token}")

workflow_path = root / ".github/workflows/release-community.yml"
workflow = workflow_path.read_text(encoding="utf-8") if workflow_path.exists() else ""
required_release_tokens = [
    "actions/upload-artifact@v7",
    "actions/download-artifact@v8",
    "branches:",
    "- main",
    "publish_current_version",
    "release/VERSION",
    "runs-on: macos-15",
    "runs-on: windows-latest",
    "verify_community_dmg.sh",
    "install_smoke_test.ps1",
    "community-validated",
    "Expected exactly 2 validation manifests",
    "bootstrap commits do not auto-publish",
    "gh release create",
    "--target \"$GITHUB_SHA\"",
]
for token in required_release_tokens:
    if token not in workflow:
        error(f"community release workflow missing: {token}")

for forbidden in [
    "macos-15-intel",
    "APPLE_CERTIFICATE_P12_BASE64",
    "WINDOWS_CERTIFICATE_PFX_BASE64",
    "notarytool submit",
    "SignTool=releaseSigner",
]:
    if forbidden in workflow:
        error(f"community release workflow unexpectedly requires signed-release feature: {forbidden}")

# Keep release/VERSION tracked while generated release artifacts remain ignored.
gitignore_path = root / ".gitignore"
gitignore = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
if "release/*" not in gitignore or "!release/VERSION" not in gitignore:
    error(".gitignore must ignore generated release artifacts while explicitly tracking release/VERSION")
if re.search(r"(?m)^release/$", gitignore):
    error(".gitignore must not ignore the entire release directory because release/VERSION is tracked")

# Keep GitHub-maintained action majors within supported floors while allowing
# Dependabot to advance them coherently instead of hard-coding one exact major.
workflow_texts = {
    "ci.yml": ci,
    "release-community.yml": workflow,
}

dev_path = root / ".github/workflows/build-dev.yml"
dev = dev_path.read_text(encoding="utf-8") if dev_path.exists() else ""
workflow_texts["build-dev.yml"] = dev


def action_majors(workflow_text: str, action_name: str) -> set[int]:
    return {int(value) for value in re.findall(rf"{re.escape(action_name)}@v(\d+)", workflow_text)}


for action_name, allowed_majors in [
    ("actions/checkout", {6, 7}),
    ("actions/setup-python", {6, 7}),
]:
    observed_by_workflow: dict[str, set[int]] = {}
    for workflow_name, workflow_text in workflow_texts.items():
        majors = action_majors(workflow_text, action_name)
        observed_by_workflow[workflow_name] = majors
        if not majors:
            error(f"{workflow_name} missing GitHub action: {action_name}@vN")
        elif not majors.issubset(allowed_majors):
            error(
                f"{workflow_name} references unreviewed {action_name} major(s): "
                f"{sorted(majors)}; allowed majors are {sorted(allowed_majors)}"
            )
        elif len(majors) != 1:
            error(f"{workflow_name} mixes {action_name} majors: {sorted(majors)}")
    populated = [next(iter(majors)) for majors in observed_by_workflow.values() if len(majors) == 1]
    if populated and len(set(populated)) != 1:
        error(f"workflows must use one consistent {action_name} major, found {sorted(set(populated))}")

for workflow_name, workflow_text in workflow_texts.items():
    for deprecated_action in [
        "actions/checkout@v4",
        "actions/setup-python@v5",
        "actions/upload-artifact@v4",
        "actions/download-artifact@v4",
    ]:
        if deprecated_action in workflow_text:
            error(f"{workflow_name} still references deprecated Node-20-era action: {deprecated_action}")

for workflow_name, workflow_text in [("build-dev.yml", dev), ("release-community.yml", workflow)]:
    upload_majors = action_majors(workflow_text, "actions/upload-artifact")
    if not upload_majors or min(upload_majors) < 7:
        error(f"{workflow_name} must use actions/upload-artifact@v7 or newer")

download_majors = action_majors(workflow, "actions/download-artifact")
if not download_majors or min(download_majors) < 8:
    error("release-community.yml must use actions/download-artifact@v8 or newer")

for token in [
    "- dev",
    "Development builds",
    "paths:",
    '".github/workflows/build-dev.yml"',
    '"src/**"',
    '"packaging/**"',
    '"resources/**"',
    "retention-days: 7",
    "Retain newest development artifact pair",
    "actions: write",
    'startswith("DEV-")',
    "gh api --method DELETE",
    "dev-validated",
    "create_dmg.sh",
    "install_smoke_test.ps1",
    "WGB_OUTPUT_BASENAME",
]:
    if token not in dev:
        error(f"development build workflow missing artifact-lifecycle invariant: {token}")
if "retention-days: 14" in dev:
    error("development artifacts must not retain the old 14-day stacking policy")

for token in [
    "permissions:\n  contents: read",
    "permissions:\n      contents: write",
    "retention-days: 3",
    "Remove temporary release workflow artifacts",
    "actions: write",
    'startswith("community-")',
    "needs.publish.result == 'success'",
    "gh api --method DELETE",
    "GitHub Release assets remain published",
]:
    if token not in workflow:
        error(f"community release workflow missing artifact-lifecycle invariant: {token}")


# Routine dependency version updates should flow through dev without creating
# unnecessary pip lower-bound churn. Security updates remain repository-managed
# and target the default branch independently of this version-update policy.
dependabot_path = root / ".github/dependabot.yml"
dependabot = dependabot_path.read_text(encoding="utf-8") if dependabot_path.exists() else ""
if re.search(r'package-ecosystem:\s*["\']?pip["\']?', dependabot):
    error("Dependabot routine pip version updates must remain disabled; security updates are managed separately")
if len(re.findall(r'package-ecosystem:\s*["\']?github-actions["\']?', dependabot)) != 1:
    error("Dependabot must contain exactly one github-actions version-update entry")
for token in [
    'target-branch: "dev"',
    'interval: "weekly"',
    'open-pull-requests-limit: 2',
    'github-actions:',
    '- "*"',
]:
    if token not in dependabot:
        error(f"Dependabot GitHub Actions policy missing: {token}")

manifest_path = root / "scripts/make_release_manifest.py"
manifest = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else ""
for token in ['"community-validated"', '"dev-validated"']:
    if token not in manifest:
        error(f"release manifest generator missing allowed status {token}")

mac_path = root / "packaging/macos/verify_community_dmg.sh"
mac = mac_path.read_text(encoding="utf-8") if mac_path.exists() else ""
for token in ["hdiutil attach", "grep -q 'arm64'", "--self-test"]:
    if token not in mac:
        error(f"macOS community verification missing: {token}")

iss_path = root / "packaging/windows/WGMapBackporterStudio.iss"
iss = iss_path.read_text(encoding="utf-8") if iss_path.exists() else ""
for token in [
    '#define Publisher "RhysHopkins04"',
    "SignedUninstaller=no",
    "WizardStyle=modern",
    "WGB_OUTPUT_BASENAME",
    "OutputBaseFilename={#OutputBaseName}",
]:
    if token not in iss:
        error(f"Windows installer missing: {token}")
for forbidden in ["SignTool=releaseSigner", "SignedUninstaller=yes"]:
    if forbidden in iss:
        error(f"Windows community installer still requires signing: {forbidden}")

win_packaged_path = root / "packaging/windows/verify_packaged_exe.ps1"
win_packaged = win_packaged_path.read_text(encoding="utf-8") if win_packaged_path.exists() else ""
for token in ["Start-Process", "-Wait", "-PassThru", "ExitCode", "--self-test"]:
    if token not in win_packaged:
        error(f"Windows packaged executable verifier missing: {token}")

for workflow_name, workflow_text in [("development", dev), ("community release", workflow)]:
    if "verify_packaged_exe.ps1" not in workflow_text:
        error(f"{workflow_name} workflow does not use the synchronous Windows packaged executable verifier")
    if '$LASTEXITCODE -ne 0' in workflow_text:
        error(f"{workflow_name} workflow still performs a direct GUI executable LASTEXITCODE check")

# Keep the packaged desktop UI deterministic across Qt platform styles.
# The Backporter page must scroll instead of crushing its form when vertically
# constrained, and analyzer headers must remain readable *and* user-resizable.
main_window_path = root / "src/wgmap_backporter_studio/ui/main_window.py"
main_window = main_window_path.read_text(encoding="utf-8") if main_window_path.exists() else ""
for token in [
    "QScrollArea",
    "QSizePolicy",
    'scroll.setObjectName("backportScroll")',
    "scroll.setWidgetResizable(True)",
    "scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)",
    'scroll.viewport().setObjectName("backportScrollViewport")',
    'scroll_body.setObjectName("backportScrollBody")',
    "scroll_body.setMinimumHeight(610)",
    "form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)",
    "form.setRowWrapPolicy(QFormLayout.DontWrapRows)",
    "form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)",
    "form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)",
    "form_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)",
    "form_group.setMinimumHeight(195)",
    "self.version.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)",
    "self.version.setMinimumWidth(220)",
    "self.version.setMaximumWidth(320)",
    "self.target_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)",
    "opts.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)",
    "opts.setMinimumHeight(150)",
    "og.setColumnStretch(1, 1)",
    "self.yoff.setMaximumWidth(180)",
    "self.strip.setMaximumWidth(180)",
    "self.log.setMinimumHeight(150)",
    'QCheckBox("Use safe mod architectural block replacements")',
    "self.setMinimumSize(980, 740)",
]:
    if token not in main_window:
        error(f"Map Backporter cross-platform layout invariant missing: {token}")

for token in [
    "def _configure_resizable_columns",
    "header.setSectionResizeMode(QHeaderView.Interactive)",
    "QFontMetrics(header.font())",
    "metrics.horizontalAdvance(label) + 52",
    "header.sectionResized.connect(keep_readable)",
    "header.resizeSection(index, minimums[index])",
    "table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)",
    "table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)",
    'jar_labels = ("Registry hint", "Display name", "Confidence", "Evidence", "Textures", "Models")',
    'modpack_labels = ("Mod", "Mod IDs", "Version", "Loader", "Block candidates", "Source")',
    'catalog_labels = ("Registry", "Display", "Mod", "Confidence", "Evidence", "Texture assets")',
    "(145, 150, 125, 135, 105, 95)",
    "(105, 115, 105, 100, 165, 120)",
    "(120, 120, 95, 125, 120, 150)",
    "splitter.setChildrenCollapsible(False)",
]:
    if token not in main_window:
        error(f"Analyzer table/header layout invariant missing: {token}")

for forbidden in [
    "setSectionResizeMode(0, QHeaderView.Stretch)",
    "setSectionResizeMode(1, QHeaderView.Stretch)",
    "setSectionResizeMode(5, QHeaderView.Stretch)",
]:
    if forbidden in main_window:
        error(f"Analyzer table still locks a user-facing column to Stretch mode: {forbidden}")

theme_path = root / "src/wgmap_backporter_studio/ui/theme.py"
theme = theme_path.read_text(encoding="utf-8") if theme_path.exists() else ""
for token in [
    "padding: 7px 24px 7px 9px",
    "QScrollArea#backportScroll",
    "QWidget#backportScrollViewport",
    "QWidget#backportScrollBody",
    "background: #11151b",
]:
    if token not in theme:
        error(f"Packaged UI theme invariant missing: {token}")

win_smoke_path = root / "packaging/windows/install_smoke_test.ps1"
win_smoke = win_smoke_path.read_text(encoding="utf-8") if win_smoke_path.exists() else ""
for token in ["verify_packaged_exe.ps1", "unins*.exe", "Executable remained after uninstall"]:
    if token not in win_smoke:
        error(f"Windows install smoke test missing: {token}")
if "Get-AuthenticodeSignature" in win_smoke:
    error("Windows community smoke test still requires Authenticode verification")

license_path = root / "LICENSE.md"
license_text = license_path.read_text(encoding="utf-8") if license_path.exists() else ""
for token in ["PolyForm Strict License 1.0.0", "RhysHopkins04", "source-available"]:
    if token not in license_text:
        error(f"license notice missing: {token}")

if errors:
    print("\n".join("ERROR: " + item for item in errors))
    sys.exit(1)

print(f"community repository packaging/static verification passed for version {version}")
