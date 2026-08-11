from __future__ import annotations

import io
import json
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .jar_analyzer import analyze_jar, analyze_jar_bytes

@dataclass
class PackModSummary:
    source: str
    mod_name: str
    mod_ids: list[str]
    version: str
    loader_hint: str
    block_candidates: int
    block_entities: int = 0
    provider_role: str = "general"
    notes: list[str] = field(default_factory=list)

@dataclass
class PackAnalysis:
    source: str
    pack_kind: str = "folder"
    pack_name: str = ""
    minecraft_version: str = ""
    modloader: str = ""
    manifested_files: int = 0
    local_jars: int = 0
    mods: list[PackModSummary] = field(default_factory=list)
    block_catalogs: list[dict[str, Any]] = field(default_factory=list)
    unresolved_manifest_files: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "schema": 1,
            "kind": "modpack_block_analysis",
            "source": self.source,
            "pack_kind": self.pack_kind,
            "pack_name": self.pack_name,
            "minecraft_version": self.minecraft_version,
            "modloader": self.modloader,
            "manifested_files": self.manifested_files,
            "local_jars": self.local_jars,
            "mods": [asdict(m) for m in self.mods],
            "block_catalogs": self.block_catalogs,
            "unresolved_manifest_files": self.unresolved_manifest_files,
            "notes": self.notes,
        }

    def save(self, path: str | Path):
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def _summary(cat) -> PackModSummary:
    return PackModSummary(
        source=cat.source,
        mod_name=cat.mod_name,
        mod_ids=cat.mod_ids,
        version=cat.mod_version,
        loader_hint=cat.loader_hint,
        block_candidates=len(cat.blocks),
        block_entities=len(cat.block_entities),
        provider_role=cat.provider_role,
        notes=cat.notes,
    )


def _parse_cf_manifest(data: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(data.decode("utf-8-sig", "replace"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def analyze_modpack(path: str | Path, log=lambda *_: None) -> PackAnalysis:
    path = Path(path)
    if path.is_dir():
        result = PackAnalysis(source=str(path), pack_kind="instance folder", pack_name=path.name)
        manifest = path / "manifest.json"
        if manifest.exists():
            _apply_manifest(result, _parse_cf_manifest(manifest.read_bytes()))
        jar_paths = sorted((path / "mods").glob("*.jar")) if (path / "mods").is_dir() else sorted(path.rglob("*.jar"))
        for i, jar in enumerate(jar_paths, 1):
            log(f"[{i}/{len(jar_paths)}] {jar.name}")
            try:
                cat = analyze_jar(jar, log=log)
                result.mods.append(_summary(cat)); result.block_catalogs.append(cat.to_dict())
            except Exception as e:
                result.notes.append(f"Could not analyze {jar.name}: {e}")
        result.local_jars = len(jar_paths)
        return result

    if not zipfile.is_zipfile(path):
        raise ValueError("Select a modpack folder or ZIP archive.")

    result = PackAnalysis(source=str(path), pack_kind="ZIP/modpack export", pack_name=path.stem)
    with zipfile.ZipFile(path, "r") as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        manifest_name = next((n for n in names if n == "manifest.json" or n.endswith("/manifest.json")), None)
        if manifest_name:
            _apply_manifest(result, _parse_cf_manifest(zf.read(manifest_name)))
            result.pack_kind = "CurseForge-style export"
        jar_names = [n for n in names if n.lower().endswith(".jar") and ("/mods/" in f"/{n}" or n.startswith("mods/"))]
        for i, name in enumerate(jar_names, 1):
            log(f"[{i}/{len(jar_names)}] {name}")
            try:
                cat = analyze_jar_bytes(zf.read(name), f"{path.name}!/{name}", log=log)
                result.mods.append(_summary(cat)); result.block_catalogs.append(cat.to_dict())
            except Exception as e:
                result.notes.append(f"Could not analyze {name}: {e}")
        result.local_jars = len(jar_names)

    if result.manifested_files and result.local_jars == 0:
        result.notes.append("This export lists CurseForge project/file IDs but does not contain the mod JARs themselves. Offline analysis cannot inspect their blocks until the JARs are present in a local instance or an API/download integration is configured.")
    return result


def _apply_manifest(result: PackAnalysis, manifest: dict[str, Any]) -> None:
    result.pack_name = str(manifest.get("name") or result.pack_name)
    mc = manifest.get("minecraft")
    if isinstance(mc, dict):
        result.minecraft_version = str(mc.get("version") or "")
        loaders = mc.get("modLoaders")
        if isinstance(loaders, list):
            result.modloader = ", ".join(str(x.get("id") or "") for x in loaders if isinstance(x, dict))
    files = manifest.get("files")
    if isinstance(files, list):
        result.manifested_files = len(files)
        result.unresolved_manifest_files = [
            {"projectID": x.get("projectID"), "fileID": x.get("fileID"), "required": x.get("required", True)}
            for x in files if isinstance(x, dict)
        ]
