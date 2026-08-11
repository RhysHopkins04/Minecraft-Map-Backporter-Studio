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
    "src/wgmap_backporter_studio/core/mapping_profiles.py",
    "src/wgmap_backporter_studio/core/workspace_store.py",
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
    "scroll_body.setMinimumHeight(700)",
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
    "opts.setMinimumHeight(205)",
    "og.setColumnStretch(1, 1)",
    "self.yoff.setMaximumWidth(180)",
    "self.strip.setMaximumWidth(180)",
    "self.log.setMinimumHeight(150)",
    'QCheckBox("Use enabled catalog/backport block replacements")',
    'self.scan_btn = QPushButton("Preflight conversion")',
    'self.preflight_status = _muted(',
    'self.recommended_btn = QPushButton("Use recommended")',
    'self.convert_btn.setEnabled((not busy) and backend_ready and self._preflight_valid())',
    "self.setMinimumSize(980, 740)",
]:
    if token not in main_window:
        error(f"Map Backporter cross-platform layout invariant missing: {token}")

# Forge 1.7.10 stores blocks/items together in FML/ItemData with hidden
# U+0001/U+0002 discriminator characters. Keep registry parsing and conversion
# preflight fail-closed so a malformed target cannot create thousands of chunk failures.
legacy_path = root / "src/wgmap_backporter_studio/core/legacy1710_engine.py"
legacy = legacy_path.read_text(encoding="utf-8") if legacy_path.exists() else ""
for token in [
    'FML_BLOCK_DISCRIMINATOR = "\\x01"',
    'FML_ITEM_DISCRIMINATOR = "\\x02"',
    'raw_name.startswith(FML_BLOCK_DISCRIMINATOR)',
    'name=raw_name[1:]',
    'ignored_items+=1',
    'source_format="Forge 1.7.10 FML/ItemData"',
    'TARGET_REGISTRY_SENTINELS',
    'def validate_target_registry(',
    'def preflight_source_mappings(',
    'def run_conversion_preflight(',
    'def _conversion_fingerprint(',
    'Reusing the verified read-only preflight',
    'Stored preflight no longer matches the current conversion inputs',
    'Running source/target mapping preflight before output staging...',
    'report["preflight"]=preflight_source_mappings',
    '"failure_counts":collections.Counter()',
    'max_failure_examples=200',
    'Failure summary:',
]:
    if token not in legacy:
        error(f"Forge 1.7.10 registry/preflight invariant missing: {token}")

run_start = legacy.find("def run_conversion(")
run_end = legacy.find("# ---------- Map analyzer", run_start)
run_body = legacy[run_start:run_end] if run_start >= 0 and run_end > run_start else ""
preflight_pos = run_body.find('preflight_source_mappings(')
stage_pos = run_body.find('_prepare_staging_output(template,output)')
if preflight_pos < 0 or stage_pos < 0 or preflight_pos > stage_pos:
    error("run_conversion must finish target/source preflight before creating the staging output")

test_smoke_path = root / "tests/test_smoke.py"
test_smoke = test_smoke_path.read_text(encoding="utf-8") if test_smoke_path.exists() else ""

for token in [
    "def test_backport_provider_mapping_profile():",
    'assert mapped.target == "etfuturum:moss_block"',
    'assert mapped.quality == "backport_exact"',
    "def test_cross_generation_jar_analysis_and_preview():",
    'assert cat.provider_role == "backport_provider"',
    'assert modern.loader_hint == "Fabric"',
    'assert len(modern.block_entities) == 1',
    'spec = build_preview_spec(jar, moss)',
    'assert door_spec["kind"] == "door"',
    'assert campfire_spec["kind"] == "campfire"',
    'assert be_spec["kind"] == "obj"',
    'assert "UV-mapped" in be_spec["note"]',
]:
    if token not in test_smoke:
        error(f"Analyzer/provider/preview regression test missing: {token}")

for token in [
    '"mapping_quality_block_occurrences":dict(quality_occurrences)',
    '"mapping_quality_percent":quality_percent',
    '"top_non_exact_mappings":impact_rows',
    'Preflight mapping impact by placed blocks:',
    'Mapping impact by placed in-range non-air blocks:',
    'quality="backport_exact" if state_exact else "backport_close"',
]:
    if token not in legacy:
        error(f"Patch 015 mapping-impact/provider invariant missing: {token}")

for token in [
    "def test_forge1710_itemdata_registry():",
    '{"K":"\\x01minecraft:stone","V":1}',
    '{"K":"\\x02hbm:some_item","V":5001}',
    'assert reg.resolve("minecraft:stone") == 1',
    'assert reg.ignored_items == 1',
]:
    if token not in test_smoke:
        error(f"Forge 1.7.10 registry regression test missing: {token}")

for token in [
    "def _minimal_block_holder_class() -> bytes:",
    'z.writestr("assets/demo/lang/zh_CN.lang"',
    'z.writestr("assets/demo/lang/en_US.lang"',
    'z.writestr("assets/demo/models/blocks/demo_brick.obj"',
    'z.writestr("demo/ModBlocks.class"',
    'assert len(cat.blocks) == 1',
    'assert block.display_name == "Demo Brick"',
    'assert block.localization_locale == "en_us"',
    'assert block.model_paths == ["assets/demo/models/blocks/demo_brick.obj"]',
]:
    if token not in test_smoke:
        error(f"Legacy analyzer regression test missing: {token}")

for token in [
    '"Backport not promoted"',
    'if failed or not promoted:',
    '"Backport complete and verified"',
    '"output_promoted"',
    '"chunks_verified"',
]:
    if token not in main_window:
        error(f"Backporter result-state UI invariant missing: {token}")

# Patch 013 makes unsupported world content explicit, stages output until it is
# structurally verified, and leaves legacy chunks marked for target-side relight.
for token in [
    'CONTENT_POLICY = "terrain_blocks_with_loss_manifest"',
    'LIGHTING_STRATEGY = "target_runtime_relight"',
    'HEIGHTMAP_STRATEGY = "bootstrap_highest_non_air"',
    'BLOCK_PROPERTY_STRATEGY = "source_properties_to_legacy_metadata_plus_runtime_neighbors"',
    '"block_entities":[]',
    'elif k == "block_entities" and t == 9:',
    'def discover_source_entity_regions(',
    'def audit_source_entities(',
    'def attach_content_audit(',
    'def validate_legacy_chunk_nbt(',
    'def verify_written_region(',
    'def _prepare_staging_output(',
    'def _promote_staging_output(',
    '"output_promoted":False',
    '"block_entities_omitted":0',
    '"unique_palette_states_with_properties":property_states',
    '"property_keys_seen":dict(property_keys)',
    '"entities_omitted":None',
    'LightPopulated=0',
    'created a hidden staging clone',
    'Conversion was NOT promoted',
    'Staged world passed round-trip verification',
]:
    if token not in legacy:
        error(f"Patch 013 world-content/staging invariant missing: {token}")

for token in [
    'f"{be_count:,} block entities',
    'content-loss manifest',
    'Output chunks will request a target-side relight.',
    '"Backport not promoted"',
    '"Backport complete and verified"',
    'every written region passed round-trip structural verification',
]:
    if token not in main_window:
        error(f"Patch 013 desktop content/verification UI invariant missing: {token}")

for token in [
    "class _AdaptiveHeaderTable(QTableWidget)",
    'previous_width = getattr(self, "_wg_last_outer_width", None)',
    "def _configure_resizable_columns",
    "_HEADER_TEXT_ALLOWANCE = 42",
    "_HEADER_COMFORT_MARGIN = 14",
    "_HEADER_ABSOLUTE_FLOOR = 72",
    "header.setSectionResizeMode(QHeaderView.Interactive)",
    "QFontMetrics(header.font())",
    "metrics.horizontalAdvance(label) + _HEADER_TEXT_ALLOWANCE",
    "preferreds = tuple(width + _HEADER_COMFORT_MARGIN for width in minimums)",
    "table._wg_header_desireds = list(preferreds)",
    "desireds = tuple(max(minimum, desired)",
    "keep_fraction = (available - minimum_total) / shrinkable",
    "table._wg_header_desireds[index] = new_size",
    "header.sectionResized.connect(keep_readable)",
    "table._wg_fit_header_columns = fit_columns_to_view",
    "table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)",
    "table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)",
    'jar_labels = ("Kind", "Registry / class", "Display name", "Confidence", "Evidence", "Textures", "Models")',
    'modpack_labels = ("Mod", "Mod IDs", "Version", "Loader", "Blocks", "Block entities", "Role", "Source")',
    'catalog_labels = ("Kind", "Registry / class", "Display", "Mod", "Confidence", "Evidence", "Assets")',
    "_configure_resizable_columns(self.table, jar_labels)",
    "_configure_resizable_columns(self.table, modpack_labels)",
    "_configure_resizable_columns(self.table, catalog_labels)",
    "splitter.setChildrenCollapsible(False)",
]:
    if token not in main_window:
        error(f"Analyzer table/header layout invariant missing: {token}")

# Cross-generation JAR analysis must remain static/non-executing while covering
# legacy registry idioms, modern metadata/assets, block entities and previews.
jar_analyzer_path = root / "src/wgmap_backporter_studio/core/jar_analyzer.py"
jar_analyzer = jar_analyzer_path.read_text(encoding="utf-8") if jar_analyzer_path.exists() else ""
for token in [
    'if loc == "en_us":',
    'META-INF/neoforge.mods.toml',
    'META-INF/mods.toml',
    'quilt.mod.json',
    'fabric.mod.json',
    'mcmod.info',
    'litemod.json',
    'def _parse_class_structure(data: bytes) -> _ClassInfo:',
    'def _looks_like_block_registry_enum(',
    'legacy enum block registry entry',
    'legacy static Block field',
    '_BLOCK_ENTITY_BASES = {',
    'packaged TileEntity/BlockEntity subclass',
    'class BlockEntityAsset',
    '_ANY_MODEL_RE = re.compile',
    'def build_preview_spec(',
    'Texture-aware static JSON model preview',
    'UV-mapped static OBJ geometry preview',
    'def _associate_preview_textures(',
    'assets/minecraft',
    'Synthesized full two-block door preview',
    'Synthesized campfire preview',
    'backport_provider',
    'architectural_fallback',
    'mapping_aliases=_candidate_aliases(rel)',
    'Display names prefer en_US',
]:
    if token not in jar_analyzer and token != 'class BlockEntityAsset':
        error(f"Cross-generation JAR analyzer invariant missing: {token}")

for token in [
    'def _preview_images(',
    'image.convertToFormat(QImage.Format.Format_RGBA8888)',
    'path + ".mcmeta" in names',
    'def _draw_textured_quad(',
    'painter.drawImage(bounds, image, QRectF(image.rect()))',
    'def _draw_uv_triangle(',
    'elif kind == "door":',
    'elif kind == "campfire":',
    'elif kind == "lantern":',
]:
    if token not in main_window:
        error(f"Patch 016 high-fidelity static preview invariant missing: {token}")


catalog_model_path = root / "src/wgmap_backporter_studio/core/catalog.py"
catalog_model = catalog_model_path.read_text(encoding="utf-8") if catalog_model_path.exists() else ""
for token in [
    'candidate_kind: str = "block asset candidate"',
    'localization_locale: str = ""',
    'mapping_aliases: list[str] = field(default_factory=list)',
    'class BlockEntityAsset:',
    'block_entities: list[BlockEntityAsset] = field(default_factory=list)',
    'provider_role: str = "general"',
    '"block_entities": [asdict(b) for b in self.block_entities]',
    '"provider_role": self.provider_role',
]:
    if token not in catalog_model:
        error(f"Catalog evidence model invariant missing: {token}")

# Catalog Workspace is additive: individual catalogs and modpack analyses can
# coexist, be toggled independently, and be saved as a reusable workspace.
for token in [
    'self.sources: list[dict] = []',
    'QPushButton("Add catalog(s)…")',
    'QPushButton("Remove selected")',
    'QPushButton("Clear all")',
    'QPushButton("Save workspace copy…")',
    'QPushButton("Storage folder")',
    'QPushButton("Add to Catalog Workspace")',
    'QPushButton("Add catalogs to Workspace")',
    'addCatalogRequested = Signal(object)',
    'addAnalysisRequested = Signal(object)',
    'def add_catalog_document(self, data: object) -> None:',
    'def _restore_default_workspace(self):',
    'def _autosave_workspace(self) -> None:',
    'self._store.save_catalog_snapshot(catalog)',
    'self._store.save_default_workspace(self._workspace_payload())',
    'self.source_list = QListWidget()',
    'item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)',
    'elif kind == "modpack_block_analysis":',
    'elif kind == "catalog_workspace":',
    'def _active_rows(self) -> list[dict]:',
    '"kind": "catalog_workspace"',
    'item.setData(Qt.UserRole, ("block" if kind == "Block" else "block_entity", source_index))',
    'pixmap, detail = _render_static_preview(self.jar.text().strip(), candidate)',
]:
    if token not in main_window:
        error(f"Multi-catalog/analyzer interaction invariant missing: {token}")


workspace_store_path = root / "src/wgmap_backporter_studio/core/workspace_store.py"
workspace_store = workspace_store_path.read_text(encoding="utf-8") if workspace_store_path.exists() else ""
for token in [
    "class WorkspaceStore:",
    'self.catalogs_dir = self.root / "Catalogs"',
    'self.workspaces_dir = self.root / "Workspaces"',
    'self.exports_dir = self.root / "Exports"',
    'self.default_workspace_path = self.workspaces_dir / "default-workspace.json"',
    "def save_default_workspace(",
    "def load_default_workspace(",
    "def save_catalog_snapshot(",
    "def save_workspace_copy(",
    "os.replace(temporary, path)",
]:
    if token not in workspace_store:
        error(f"Persistent workspace storage invariant missing: {token}")

for token in [
    "QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)",
    "return base / APP_NAME",
    "WorkspaceStore(_application_storage_root())",
    "CatalogTab(self._store)",
    "JarAnalyzerTab(self._store)",
    "ModpackAnalyzerTab(self._store)",
    "jar_tab.addCatalogRequested.connect(add_to_workspace)",
    "modpack_tab.addAnalysisRequested.connect(add_to_workspace)",
    "tabs.setCurrentWidget(catalog_tab)",
]:
    if token not in main_window:
        error(f"Analyzer/workspace persistence wiring invariant missing: {token}")


mapping_profile_path = root / "src/wgmap_backporter_studio/core/mapping_profiles.py"
mapping_profile = mapping_profile_path.read_text(encoding="utf-8") if mapping_profile_path.exists() else ""
for token in [
    "class MappingProfile:",
    "catalog_bound: bool = False",
    "def allows_namespace(self, namespace: str) -> bool:",
    'return namespace.strip().lower() in self.enabled_mod_ids',
    "def profile_from_catalog_snapshot(",
    '"mode": "enabled_catalogs_provider_rules" if self.catalog_bound else "legacy_safe_rules"',
    'backport_targets: tuple[BackportTarget, ...] = ()',
    'def backport_candidates(self, source_name: str)',
    'snapshot.get("backport_providers", [])',
    'payload["registry_hints"] = sorted(self.registry_hints)',
]:
    if token not in mapping_profile:
        error(f"Catalog-bound mapping-profile invariant missing: {token}")

for token in [
    "workspaceChanged = Signal()",
    "def active_catalog_snapshot(self) -> dict:",
    '"enabled_catalogs": labels',
    '"enabled_mod_ids": sorted(mod_ids)',
    '"registry_hints": sorted(registry_hints)',
    '"backport_providers": backport_providers',
    '"block_entity_count": block_entity_count',
    "BackportTab(catalog_provider=catalog_tab.active_catalog_snapshot)",
    "catalog_tab.workspaceChanged.connect(backport_tab.catalog_workspace_changed)",
    "def _current_input_token(self) -> str:",
    "def _preflight_valid(self) -> bool:",
    "run_conversion_preflight(",
    "catalog_snapshot=snapshot",
    "verified_preflight=verified",
]:
    if token not in main_window:
        error(f"Catalog Workspace → Backporter integration invariant missing: {token}")

version_targets_path = root / "src/wgmap_backporter_studio/core/version_targets.py"
version_targets = version_targets_path.read_text(encoding="utf-8") if version_targets_path.exists() else ""
for token in [
    "recommended_y_offset: int | None = None",
    "recommended_strip_below_y: int | None = None",
    "recommended_y_offset=0, recommended_strip_below_y=0",
]:
    if token not in version_targets:
        error(f"Target-aware recommended-setting invariant missing: {token}")

for token in [
    "def test_catalog_bound_mapping_profile():",
    'assert enabled.allows_namespace("hbm")',
    'assert mapped.target == "hbm:concrete_colored"',
    'assert not disabled.allows_namespace("hbm")',
    'assert fallback.target == "minecraft:stained_hardened_clay"',
    'assert hasattr(legacy1710_engine, "run_conversion_preflight")',
]:
    if token not in test_smoke:
        error(f"Catalog-bound mapping-profile regression test missing: {token}")

for token in [
    "def test_content_audit_and_legacy_roundtrip():",
    'assert parsed["block_entities"][0]["id"] == "minecraft:chest"',
    'assert legacy1710_engine.verify_written_region(region, {0}) == 1',
    'assert audit["entity_types"]["minecraft:cow"] == 1',
    "def test_staged_output_promotion():",
    'assert not output.exists()',
    'legacy1710_engine._promote_staging_output(staging, output)',
]:
    if token not in test_smoke:
        error(f"Patch 013 world-content/staging regression test missing: {token}")

for token in [
    "def test_content_audit_and_legacy_roundtrip():",
    'assert parsed["block_entities"][0]["id"] == "minecraft:chest"',
    'assert legacy1710_engine.verify_written_region(region, {0}) == 1',
    'assert audit["entity_types"]["minecraft:cow"] == 1',
    "def test_staged_output_promotion():",
    'assert not output.exists()',
    'legacy1710_engine._promote_staging_output(staging, output)',
]:
    if token not in test_smoke:
        error(f"Patch 013 world-content/staging regression test missing: {token}")

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
