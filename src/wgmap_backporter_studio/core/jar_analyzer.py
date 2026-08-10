from __future__ import annotations

import io
import json
import re
import tomllib
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Iterable

from .catalog import BlockAsset, ModCatalog

_TEXTURE_RE = re.compile(r"^assets/([^/]+)/textures/(?:block|blocks)/(.+)\.png$", re.I)
_MODEL_RE = re.compile(r"^assets/([^/]+)/models/block/(.+)\.json$", re.I)
_BLOCKSTATE_RE = re.compile(r"^assets/([^/]+)/blockstates/(.+)\.json$", re.I)
_LANG_RE = re.compile(r"^assets/([^/]+)/lang/([^/]+)\.(?:lang|json)$", re.I)


def _safe_json(data: bytes):
    try:
        return json.loads(data.decode("utf-8-sig", "replace"))
    except Exception:
        return None


def _parse_mcmod(data: bytes) -> dict:
    obj = _safe_json(data)
    if isinstance(obj, list) and obj:
        return obj[0] if isinstance(obj[0], dict) else {}
    if isinstance(obj, dict):
        if isinstance(obj.get("modList"), list) and obj["modList"]:
            return obj["modList"][0] if isinstance(obj["modList"][0], dict) else obj
        return obj
    return {}


def _parse_mods_toml(data: bytes) -> dict:
    try:
        obj = tomllib.loads(data.decode("utf-8", "replace"))
    except Exception:
        return {}
    mods = obj.get("mods") if isinstance(obj, dict) else None
    first = mods[0] if isinstance(mods, list) and mods and isinstance(mods[0], dict) else {}
    return {"mods_toml": obj, "first_mod": first}


def _lang_names(zf: zipfile.ZipFile, names: Iterable[str]) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for name in names:
        m = _LANG_RE.match(name)
        if not m:
            continue
        ns = m.group(1)
        try:
            raw = zf.read(name)
        except Exception:
            continue
        if name.lower().endswith(".json"):
            obj = _safe_json(raw)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if not isinstance(v, str):
                        continue
                    mm = re.match(r"block\.([^.]+)\.(.+)$", k)
                    if mm:
                        found[(mm.group(1), mm.group(2))] = v
        else:
            for line in raw.decode("utf-8", "replace").splitlines():
                if not line or line.lstrip().startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip(); v = v.strip()
                mm = re.match(r"tile\.([A-Za-z0-9_.-]+)\.name$", k)
                if mm:
                    token = mm.group(1)
                    if "." in token:
                        prefix, rest = token.split(".", 1)
                        if prefix == ns:
                            found[(ns, rest)] = v
                    found.setdefault((ns, token), v)
    return found


def _model_texture_refs(zf: zipfile.ZipFile, model_path: str) -> list[str]:
    try:
        obj = _safe_json(zf.read(model_path))
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []
    namespace = model_path.split("/", 2)[1]
    textures = obj.get("textures")
    out: list[str] = []
    if isinstance(textures, dict):
        for value in textures.values():
            if not isinstance(value, str) or value.startswith("#"):
                continue
            if ":" in value:
                ns, rel = value.split(":", 1)
            else:
                ns, rel = namespace, value
            out.append(f"assets/{ns}/textures/{rel}.png")
    return out


def analyze_jar(path: str | Path, log=lambda *_: None) -> ModCatalog:
    path = Path(path)
    with zipfile.ZipFile(path, "r") as zf:
        return analyze_zipfile(zf, str(path), log=log)


def analyze_jar_bytes(data: bytes, source_label: str, log=lambda *_: None) -> ModCatalog:
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        return analyze_zipfile(zf, source_label, log=log)


def analyze_zipfile(zf: zipfile.ZipFile, source_label: str, log=lambda *_: None) -> ModCatalog:
    names = [n for n in zf.namelist() if not n.endswith("/")]
    name_set = set(names)
    loader = "unknown"
    metadata: dict = {}
    mod_ids: list[str] = []
    mod_name = ""
    mod_version = ""
    minecraft_hint = ""

    if "META-INF/mods.toml" in name_set:
        loader = "Forge (1.13+)"
        metadata = _parse_mods_toml(zf.read("META-INF/mods.toml"))
        first = metadata.get("first_mod", {}) if isinstance(metadata, dict) else {}
        if isinstance(first, dict):
            if first.get("modId"): mod_ids = [str(first["modId"])]
            mod_name = str(first.get("displayName") or "")
            mod_version = str(first.get("version") or "")
    elif "fabric.mod.json" in name_set:
        loader = "Fabric/Quilt-style"
        obj = _safe_json(zf.read("fabric.mod.json")) or {}
        metadata = obj if isinstance(obj, dict) else {}
        if isinstance(obj, dict):
            if obj.get("id"): mod_ids = [str(obj["id"])]
            mod_name = str(obj.get("name") or "")
            mod_version = str(obj.get("version") or "")
    elif "mcmod.info" in name_set:
        loader = "Forge/FML (legacy)"
        obj = _parse_mcmod(zf.read("mcmod.info"))
        metadata = obj
        if obj.get("modid"): mod_ids = [str(obj["modid"])]
        mod_name = str(obj.get("name") or "")
        mod_version = str(obj.get("version") or "")
        minecraft_hint = str(obj.get("mcversion") or obj.get("acceptedMinecraftVersions") or "")
    elif any(n.startswith("META-INF/") and "neoforge" in n.lower() for n in names):
        loader = "NeoForge-style"

    assets_namespaces = sorted({n.split("/", 2)[1] for n in names if n.startswith("assets/") and n.count("/") >= 2})
    if not mod_ids:
        mod_ids = [ns for ns in assets_namespaces if ns != "minecraft"][:8]

    log(f"Reading assets from {source_label}")
    display_names = _lang_names(zf, names)
    textures: dict[tuple[str, str], list[str]] = defaultdict(list)
    models: dict[tuple[str, str], list[str]] = defaultdict(list)
    blockstates: dict[tuple[str, str], str] = {}

    for name in names:
        m = _TEXTURE_RE.match(name)
        if m:
            textures[(m.group(1), m.group(2))].append(name)
            continue
        m = _MODEL_RE.match(name)
        if m:
            models[(m.group(1), m.group(2))].append(name)
            continue
        m = _BLOCKSTATE_RE.match(name)
        if m:
            blockstates[(m.group(1), m.group(2))] = name

    keys = set(blockstates) | set(models) | set(textures)
    blocks: list[BlockAsset] = []
    for ns, rel in sorted(keys):
        bs = blockstates.get((ns, rel), "")
        model_list = list(models.get((ns, rel), []))
        tex_list = list(textures.get((ns, rel), []))

        if bs:
            confidence = "high"
            evidence = "blockstate JSON"
            hint = f"{ns}:{rel}"
        elif model_list:
            confidence = "medium"
            evidence = "block model asset"
            hint = f"{ns}:{rel}"
        else:
            confidence = "low"
            evidence = "block texture filename (legacy JARs may use a different registry name)"
            hint = f"{ns}:{rel}"

        for mp in model_list:
            for ref in _model_texture_refs(zf, mp):
                if ref in name_set and ref not in tex_list:
                    tex_list.append(ref)

        display = display_names.get((ns, rel), "")
        if not display:
            display = rel.replace("_", " ").replace("/", " / ").title()

        blocks.append(BlockAsset(
            namespace=ns,
            registry_hint=hint,
            display_name=display,
            confidence=confidence,
            evidence=evidence,
            texture_paths=sorted(set(tex_list)),
            model_paths=sorted(set(model_list)),
            blockstate_path=bs,
            source_mod=(mod_ids[0] if mod_ids else ns),
            source_file=source_label,
        ))

    notes = []
    if loader == "Forge/FML (legacy)" and not blockstates:
        notes.append("Legacy 1.7.10-era JARs do not contain blockstate JSON. Texture/model names are useful visual evidence but are not guaranteed to equal the GameRegistry block name.")
    notes.append("Texture previews show packaged block assets. Full in-game 3D model rendering is not implemented in this version.")

    return ModCatalog(
        source=source_label,
        loader_hint=loader,
        minecraft_hint=minecraft_hint,
        mod_ids=mod_ids,
        mod_name=mod_name,
        mod_version=mod_version,
        blocks=blocks,
        notes=notes,
        raw_metadata=metadata if isinstance(metadata, dict) else {},
    )


def read_texture_bytes(jar_path: str | Path, texture_path: str) -> bytes:
    with zipfile.ZipFile(jar_path, "r") as zf:
        return zf.read(texture_path)
