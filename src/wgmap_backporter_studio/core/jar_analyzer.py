from __future__ import annotations

import io
import json
import re
import struct
import tomllib
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Iterable

from .catalog import BlockAsset, ModCatalog

_TEXTURE_RE = re.compile(r"^assets/([^/]+)/textures/(?:block|blocks)/(.+)\.png$", re.I)
_MODEL_RE = re.compile(r"^assets/([^/]+)/models/block/(.+)\.json$", re.I)
_ANY_MODEL_RE = re.compile(r"^assets/([^/]+)/models/(.+)\.(json|obj|dae|hmf|tcn)$", re.I)
_BLOCKSTATE_RE = re.compile(r"^assets/([^/]+)/blockstates/(.+)\.json$", re.I)
_LANG_RE = re.compile(r"^assets/([^/]+)/lang/([^/]+)\.(?:lang|json)$", re.I)
_BLOCK_DESCRIPTOR = "Lnet/minecraft/block/Block;"
_BLOCK_BASE = "net/minecraft/block/Block"
_TILE_ENTITY_BASE = "net/minecraft/tileentity/TileEntity"
_ACC_STATIC = 0x0008


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


def _locale_rank(locale: str) -> tuple[int, str]:
    loc = locale.lower().replace("-", "_")
    if loc == "en_us":
        return (0, loc)
    if loc in {"en_gb", "en_ca", "en_au", "en_nz"}:
        return (1, loc)
    if loc.startswith("en_") or loc == "en":
        return (2, loc)
    if loc in {"test", "debug"}:
        return (99, loc)
    return (10, loc)


def _lang_names(zf: zipfile.ZipFile, names: Iterable[str]) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str], list[str]]:
    """Resolve display names deterministically, preferring English locales.

    ZIP member order is not a language preference. Legacy mods often package many
    translations and may put zh_CN.lang before en_US.lang, so locale precedence is
    explicit instead of relying on archive order.
    """
    locale_maps: dict[str, dict[tuple[str, str], str]] = defaultdict(dict)
    seen_locales: set[str] = set()

    for name in names:
        m = _LANG_RE.match(name)
        if not m:
            continue
        ns, locale = m.group(1), m.group(2)
        locale_key = locale.lower().replace("-", "_")
        seen_locales.add(locale_key)
        try:
            raw = zf.read(name)
        except Exception:
            continue
        target = locale_maps[locale_key]

        if name.lower().endswith(".json"):
            obj = _safe_json(raw)
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if not isinstance(value, str):
                        continue
                    mm = re.match(r"block\.([^.]+)\.(.+)$", key)
                    if mm:
                        target[(mm.group(1), mm.group(2))] = value
            continue

        for line in raw.decode("utf-8-sig", "replace").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            mm = re.match(r"tile\.([A-Za-z0-9_.\-/]+)\.name$", key)
            if not mm:
                continue
            token = mm.group(1)
            # Common 1.7.10 forms are tile.block_name.name and
            # tile.modid.block_name.name. Preserve both interpretations.
            target[(ns, token)] = value
            if "." in token:
                prefix, rest = token.split(".", 1)
                if prefix == ns:
                    target[(ns, rest)] = value

    resolved: dict[tuple[str, str], str] = {}
    locale_used: dict[tuple[str, str], str] = {}
    for locale in sorted(locale_maps, key=_locale_rank):
        for key, value in locale_maps[locale].items():
            if key not in resolved:
                resolved[key] = value
                locale_used[key] = locale

    return resolved, locale_used, sorted(seen_locales, key=_locale_rank)


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


def _class_utf8(cp, index: int) -> str:
    if not index or index >= len(cp) or cp[index] is None:
        return ""
    entry = cp[index]
    if entry[0] == "Utf8":
        return entry[1]
    return ""


def _class_name(cp, index: int) -> str:
    if not index or index >= len(cp) or cp[index] is None:
        return ""
    entry = cp[index]
    if entry[0] != "Class":
        return ""
    return _class_utf8(cp, entry[1])


def _parse_class_structure(data: bytes):
    """Return (class name, super name, fields) from a JVM class file.

    This intentionally parses only the constant-pool/header/field portion needed
    to identify legacy Forge static Block declarations. It is not a bytecode
    decompiler and does not execute mod code.
    """
    view = memoryview(data)
    pos = 0

    def take(n: int) -> bytes:
        nonlocal pos
        if pos + n > len(view):
            raise ValueError("truncated class file")
        out = bytes(view[pos:pos+n]); pos += n
        return out

    def u1() -> int:
        return take(1)[0]

    def u2() -> int:
        return struct.unpack(">H", take(2))[0]

    def u4() -> int:
        return struct.unpack(">I", take(4))[0]

    if u4() != 0xCAFEBABE:
        raise ValueError("not a class file")
    u2(); u2()  # minor, major
    cp_count = u2()
    cp = [None] * cp_count
    i = 1
    while i < cp_count:
        tag = u1()
        if tag == 1:  # Utf8
            length = u2()
            cp[i] = ("Utf8", take(length).decode("utf-8", "replace"))
        elif tag in (3, 4):
            take(4); cp[i] = ("Number",)
        elif tag in (5, 6):
            take(8); cp[i] = ("WideNumber",); i += 1
        elif tag == 7:
            cp[i] = ("Class", u2())
        elif tag in (8, 16, 19, 20):
            take(2); cp[i] = ("Ref2",)
        elif tag in (9, 10, 11, 12, 17, 18):
            take(4); cp[i] = ("Ref4",)
        elif tag == 15:
            take(3); cp[i] = ("MethodHandle",)
        else:
            raise ValueError(f"unsupported class constant tag {tag}")
        i += 1

    access_flags = u2()
    this_class = u2()
    super_class = u2()
    this_name = _class_name(cp, this_class)
    super_name = _class_name(cp, super_class)

    for _ in range(u2()):
        u2()  # interfaces

    fields = []
    for _ in range(u2()):
        field_access = u2(); name_index = u2(); desc_index = u2()
        attr_count = u2()
        for _ in range(attr_count):
            u2(); take(u4())
        fields.append((field_access, _class_utf8(cp, name_index), _class_utf8(cp, desc_index)))

    return this_name, super_name, access_flags, fields


def _legacy_class_evidence(zf: zipfile.ZipFile, names: Iterable[str]) -> tuple[set[str], int, int]:
    """Find probable legacy block fields without loading or executing the mod.

    We build the inheritance graph from packaged classes, identify classes that
    extend Minecraft Block/TileEntity, then inspect static fields whose declared
    type is Block or a packaged Block subclass. Static block holder fields are a
    much stronger 1.7.10 signal than texture filenames alone.
    """
    classes: dict[str, tuple[str, list[tuple[int, str, str]]]] = {}
    for name in names:
        if not name.endswith(".class"):
            continue
        try:
            this_name, super_name, _access, fields = _parse_class_structure(zf.read(name))
        except Exception:
            continue
        if this_name:
            classes[this_name] = (super_name, fields)

    def descendants_of(base: str) -> set[str]:
        found = {base}
        changed = True
        while changed:
            changed = False
            for cls, (super_name, _fields) in classes.items():
                if cls not in found and super_name in found:
                    found.add(cls); changed = True
        return found

    block_classes = descendants_of(_BLOCK_BASE)
    tile_entity_classes = descendants_of(_TILE_ENTITY_BASE)
    field_names: set[str] = set()
    for _owner, (_super, fields) in classes.items():
        for access, field_name, descriptor in fields:
            if not (access & _ACC_STATIC) or not field_name:
                continue
            if not (descriptor.startswith("L") and descriptor.endswith(";")):
                continue
            declared = descriptor[1:-1]
            if declared in block_classes:
                field_names.add(field_name)

    return field_names, max(0, len(block_classes) - 1), max(0, len(tile_entity_classes) - 1)


def _asset_stem(path: str) -> str:
    return PurePosixPath(path).stem


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
    primary_ns = mod_ids[0] if mod_ids else (assets_namespaces[0] if assets_namespaces else "minecraft")

    log(f"Reading assets from {source_label}")
    display_names, display_locales, locales_found = _lang_names(zf, names)
    textures: dict[tuple[str, str], list[str]] = defaultdict(list)
    modern_models: dict[tuple[str, str], list[str]] = defaultdict(list)
    all_models_by_stem: dict[tuple[str, str], list[str]] = defaultdict(list)
    blockstates: dict[tuple[str, str], str] = {}
    all_model_assets: list[str] = []

    for name in names:
        m = _TEXTURE_RE.match(name)
        if m:
            textures[(m.group(1), m.group(2))].append(name)
            continue
        m = _MODEL_RE.match(name)
        if m:
            modern_models[(m.group(1), m.group(2))].append(name)
        m = _ANY_MODEL_RE.match(name)
        if m:
            ns = m.group(1)
            all_model_assets.append(name)
            all_models_by_stem[(ns, _asset_stem(name))].append(name)
            continue
        m = _BLOCKSTATE_RE.match(name)
        if m:
            blockstates[(m.group(1), m.group(2))] = name

    legacy_fields: set[str] = set()
    block_subclass_count = 0
    tile_entity_class_count = 0
    if loader == "Forge/FML (legacy)":
        legacy_fields, block_subclass_count, tile_entity_class_count = _legacy_class_evidence(zf, names)
        if legacy_fields:
            log(f"Legacy class evidence: {len(legacy_fields):,} static Block fields; {len(all_model_assets):,} packaged model assets")

    keys: set[tuple[str, str]]
    class_backed: set[tuple[str, str]] = set()
    if legacy_fields:
        class_backed = {(primary_ns, field) for field in legacy_fields}
        # Keep any modern-style blockstates as authoritative if a hybrid legacy
        # mod happens to package them, but do not promote every texture file to a
        # registered block candidate when class evidence is available.
        keys = set(blockstates) | class_backed
    else:
        keys = set(blockstates) | set(modern_models) | set(textures)

    blocks: list[BlockAsset] = []
    associated_model_assets: set[str] = set()
    for ns, rel in sorted(keys):
        bs = blockstates.get((ns, rel), "")
        model_list = list(modern_models.get((ns, rel), []))
        tex_list = list(textures.get((ns, rel), []))
        is_class_backed = (ns, rel) in class_backed

        # Legacy OBJ/DAE/HMF models are not under the modern models/block/*.json
        # convention. Associate an exact-stem model with a class-backed block.
        if is_class_backed:
            for mp in all_models_by_stem.get((ns, rel), []):
                if mp not in model_list:
                    model_list.append(mp)
                associated_model_assets.add(mp)

        if bs:
            confidence = "high"
            evidence = "blockstate JSON"
            candidate_kind = "registered block candidate"
        elif is_class_backed:
            has_supporting_asset = bool(model_list or tex_list or display_names.get((ns, rel)))
            confidence = "high" if has_supporting_asset else "medium"
            pieces = ["legacy static Block field"]
            if model_list:
                pieces.append("packaged legacy model")
            if tex_list:
                pieces.append("matching block texture")
            if display_names.get((ns, rel)):
                pieces.append("localization entry")
            evidence = " + ".join(pieces)
            candidate_kind = "registered block candidate"
        elif model_list:
            confidence = "medium"
            evidence = "block model asset"
            candidate_kind = "block asset candidate"
        else:
            confidence = "low"
            evidence = "block texture filename (registry name unverified)"
            candidate_kind = "texture-only candidate"

        for mp in model_list:
            if mp.lower().endswith(".json"):
                for ref in _model_texture_refs(zf, mp):
                    if ref in name_set and ref not in tex_list:
                        tex_list.append(ref)

        display = display_names.get((ns, rel), "")
        locale_used = display_locales.get((ns, rel), "")
        if not display:
            display = rel.replace("_", " ").replace("/", " / ").title()

        blocks.append(BlockAsset(
            namespace=ns,
            registry_hint=f"{ns}:{rel}",
            display_name=display,
            confidence=confidence,
            evidence=evidence,
            texture_paths=sorted(set(tex_list)),
            model_paths=sorted(set(model_list)),
            blockstate_path=bs,
            source_mod=(mod_ids[0] if mod_ids else ns),
            source_file=source_label,
            candidate_kind=candidate_kind,
            localization_locale=locale_used,
        ))

    notes = []
    if loader == "Forge/FML (legacy)":
        if legacy_fields:
            notes.append(
                f"Legacy class-file analysis found {len(legacy_fields):,} static Block fields. "
                "These are used as stronger block candidates instead of treating every textures/blocks PNG as a registered block."
            )
        else:
            notes.append(
                "No static legacy Block-field evidence could be recovered, so texture/model names remain heuristic candidates and may not equal GameRegistry names."
            )
        notes.append(
            f"Legacy model scan found {len(all_model_assets):,} packaged model assets across JSON/OBJ/DAE/HMF/TCN formats; "
            f"{len(associated_model_assets):,} were directly associated with block candidates by exact asset name."
        )
        if tile_entity_class_count:
            notes.append(
                f"Detected {tile_entity_class_count:,} packaged TileEntity subclasses. Their presence is recorded as legacy rendering/data evidence; "
                "full tile-entity model binding and in-game 3D rendering are not implemented yet."
            )
    notes.append("Display names prefer en_US, then other English locales, then non-English translations only as a final fallback.")
    notes.append("Texture previews show packaged block assets. Full in-game 3D model rendering is not implemented in this version.")

    analysis_stats = {
        "packaged_block_textures": sum(len(v) for v in textures.values()),
        "packaged_model_assets": len(all_model_assets),
        "associated_model_assets": len(associated_model_assets),
        "legacy_static_block_fields": len(legacy_fields),
        "legacy_block_subclasses": block_subclass_count,
        "legacy_tile_entity_subclasses": tile_entity_class_count,
        "locales_found": locales_found,
    }

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
        analysis_stats=analysis_stats,
    )


def read_texture_bytes(jar_path: str | Path, texture_path: str) -> bytes:
    with zipfile.ZipFile(jar_path, "r") as zf:
        return zf.read(texture_path)
