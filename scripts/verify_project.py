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

dev_path = root / ".github/workflows/build-dev.yml"
dev = dev_path.read_text(encoding="utf-8") if dev_path.exists() else ""
for token in [
    "- dev",
    "Development builds",
    "retention-days: 14",
    "dev-validated",
    "create_dmg.sh",
    "install_smoke_test.ps1",
    "WGB_OUTPUT_BASENAME",
]:
    if token not in dev:
        error(f"development build workflow missing: {token}")

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

win_smoke_path = root / "packaging/windows/install_smoke_test.ps1"
win_smoke = win_smoke_path.read_text(encoding="utf-8") if win_smoke_path.exists() else ""
for token in ["--self-test", "unins*.exe", "Executable remained after uninstall"]:
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
