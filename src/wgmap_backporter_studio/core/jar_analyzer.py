from __future__ import annotations

import io
import json
import re
import struct
import tomllib
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .catalog import BlockAsset, BlockEntityAsset, ModCatalog


_TEXTURE_RE = re.compile(r"^assets/([^/]+)/textures/(?:block|blocks)/(.+)\.png$", re.I)
_MODEL_RE = re.compile(r"^assets/([^/]+)/models/(?:block|blocks)/(.+)\.json$", re.I)
_ANY_MODEL_RE = re.compile(r"^assets/([^/]+)/models/(.+)\.(json|obj|dae|hmf|tcn)$", re.I)
_BLOCKSTATE_RE = re.compile(r"^assets/([^/]+)/blockstates/(.+)\.json$", re.I)
_LANG_RE = re.compile(r"^assets/([^/]+)/lang/([^/]+)\.(?:lang|json)$", re.I)

_BLOCK_BASES = {
    "net/minecraft/block/Block",  # MCP / legacy Forge / older Fabric mappings
    "net/minecraft/world/level/block/Block",  # Mojmap 1.17+
}
_BLOCK_ENTITY_BASES = {
    "net/minecraft/tileentity/TileEntity",  # MCP through 1.16.x
    "net/minecraft/world/level/block/entity/BlockEntity",  # Mojmap 1.17+
    "net/minecraft/block/entity/BlockEntity",  # Yarn named mappings
}
_BLOCK_ENTITY_TYPE_DESCRIPTORS = {
    "Lnet/minecraft/tileentity/TileEntityType;",
    "Lnet/minecraft/world/level/block/entity/BlockEntityType;",
    "Lnet/minecraft/block/entity/BlockEntityType;",
}
_ACC_STATIC = 0x0008
_ACC_ENUM = 0x4000

_KNOWN_BACKPORT_IDS = {
    "etfuturum", "et_futurum", "uptodate", "uptodatemod", "campfirebackport",
    "futuremc", "futureminecraft", "futureversions",
}
_KNOWN_ARCHITECTURAL_IDS = {"hbm"}


@dataclass
class _ClassInfo:
    name: str
    super_name: str
    access_flags: int
    fields: list[tuple[int, str, str]]
    utf8: tuple[str, ...]


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


def _manifest_values(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    current = ""
    for line in data.decode("utf-8", "replace").splitlines():
        if line.startswith(" ") and current:
            out[current] = out.get(current, "") + line[1:]
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current = key.strip()
        out[current] = value.strip()
    return out


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


def _lang_names(
    zf: zipfile.ZipFile,
    names: Iterable[str],
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str], list[str]]:
    """Resolve block display names deterministically, preferring English locales."""
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


def _parse_class_structure(data: bytes) -> _ClassInfo:
    """Parse enough JVM structure for static registry and inheritance analysis.

    This parser never executes mod code. It intentionally records the constant-pool
    UTF-8 strings as additional loader/registration evidence while skipping method
    bytecode and attributes safely.
    """
    view = memoryview(data)
    pos = 0

    def take(n: int) -> bytes:
        nonlocal pos
        if pos + n > len(view):
            raise ValueError("truncated class file")
        out = bytes(view[pos:pos + n])
        pos += n
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
        if tag == 1:
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
        u2()

    fields: list[tuple[int, str, str]] = []
    for _ in range(u2()):
        field_access = u2(); name_index = u2(); desc_index = u2()
        attr_count = u2()
        for _ in range(attr_count):
            u2(); take(u4())
        fields.append((field_access, _class_utf8(cp, name_index), _class_utf8(cp, desc_index)))

    # Methods and class attributes are not needed, but consume them to reject
    # truncated/corrupt class files deterministically.
    for _ in range(u2()):
        u2(); u2(); u2()
        for _ in range(u2()):
            u2(); take(u4())
    for _ in range(u2()):
        u2(); take(u4())

    utf8 = tuple(entry[1] for entry in cp if entry and entry[0] == "Utf8")
    return _ClassInfo(this_name, super_name, access_flags, fields, utf8)


def _class_inventory(zf: zipfile.ZipFile, names: Iterable[str]) -> dict[str, _ClassInfo]:
    classes: dict[str, _ClassInfo] = {}
    for name in names:
        if not name.endswith(".class"):
            continue
        try:
            info = _parse_class_structure(zf.read(name))
        except Exception:
            continue
        if info.name:
            classes[info.name] = info
    return classes


def _looks_like_external_block_class(name: str) -> bool:
    if name in _BLOCK_BASES:
        return True
    # Legacy MCP puts vanilla Block subclasses under net.minecraft.block and
    # names them BlockStairs/BlockSlab/etc. Modern Mojmap uses names such as
    # StairBlock/SlabBlock in net.minecraft.world.level.block. These classes are
    # not packaged inside a mod JAR, so seed them from their stable package/name
    # shape instead of requiring their bytecode to be present.
    if name.startswith("net/minecraft/block/Block"):
        return True
    if name.startswith("net/minecraft/world/level/block/") and name.rsplit("/", 1)[-1].endswith("Block"):
        return True
    return False


def _descendants_of(classes: dict[str, _ClassInfo], bases: set[str], external_predicate=None) -> set[str]:
    found = set(bases)
    if external_predicate is not None:
        for info in classes.values():
            if external_predicate(info.super_name):
                found.add(info.super_name)
    changed = True
    while changed:
        changed = False
        for cls, info in classes.items():
            if cls not in found and (info.super_name in found or (external_predicate is not None and external_predicate(info.super_name))):
                found.add(cls)
                changed = True
    return found


def _looks_like_block_registry_enum(info: _ClassInfo) -> bool:
    simple = info.name.rsplit("/", 1)[-1].lower()
    name_signal = simple in {"modblocks", "blocks", "blockregistry", "blocklist", "blocktypes"} or simple.endswith("blocks")
    cp = "\n".join(info.utf8)
    registration_signal = (
        "registerBlock" in cp
        or "GameRegistry" in cp
        or "net/minecraft/block/Block" in cp
        or "net/minecraft/world/level/block/Block" in cp
    )
    return bool(info.access_flags & _ACC_ENUM) and name_signal and registration_signal


def _class_evidence(classes: dict[str, _ClassInfo]) -> dict[str, Any]:
    block_classes = _descendants_of(classes, _BLOCK_BASES, _looks_like_external_block_class)
    block_entity_classes = _descendants_of(classes, _BLOCK_ENTITY_BASES)

    # Published jars can use loader/version-specific Minecraft mappings. When the
    # vanilla superclass name is not one of the known MCP/Mojmap/Yarn-named forms,
    # preserve strong mod-class naming evidence rather than silently hiding a
    # likely tile/block entity. This is advisory catalog data only and never grants
    # conversion mapping authority by itself.
    for class_name in classes:
        simple = class_name.rsplit("/", 1)[-1].lower()
        if simple.endswith("tileentity") or simple.endswith("blockentity"):
            block_entity_classes.add(class_name)

    static_block_fields: set[str] = set()
    enum_block_fields: set[str] = set()
    block_entity_type_fields: set[str] = set()

    for owner, info in classes.items():
        if _looks_like_block_registry_enum(info):
            own_desc = f"L{owner};"
            for access, field_name, descriptor in info.fields:
                if field_name and (access & _ACC_STATIC) and (access & _ACC_ENUM) and descriptor == own_desc:
                    enum_block_fields.add(field_name.lower())

        for access, field_name, descriptor in info.fields:
            if not (access & _ACC_STATIC) or not field_name:
                continue
            if descriptor in _BLOCK_ENTITY_TYPE_DESCRIPTORS:
                block_entity_type_fields.add(field_name)
            if descriptor.startswith("L") and descriptor.endswith(";"):
                declared = descriptor[1:-1]
                if declared in block_classes:
                    static_block_fields.add(field_name)

    packaged_block_classes = sorted(c for c in block_classes if c not in _BLOCK_BASES)
    packaged_block_entities = sorted(c for c in block_entity_classes if c not in _BLOCK_ENTITY_BASES)
    return {
        "static_block_fields": static_block_fields,
        "enum_block_fields": enum_block_fields,
        "block_classes": packaged_block_classes,
        "block_entity_classes": packaged_block_entities,
        "block_entity_type_fields": block_entity_type_fields,
    }


def _infer_loader_from_classes(classes: dict[str, _ClassInfo], manifest: dict[str, str]) -> str:
    if manifest.get("FMLCorePlugin") or manifest.get("FMLAT"):
        return "Forge/FML (legacy, inferred)"
    joined = "\n".join(s for info in classes.values() for s in info.utf8)
    if "cpw/mods/fml/common/Mod" in joined or "net/minecraftforge/fml/common/Mod" in joined:
        return "Forge/FML (legacy, inferred)"
    if "net/minecraftforge/fml/common/registry/GameRegistry" in joined or "cpw/mods/fml/common/registry/GameRegistry" in joined:
        return "Forge/FML (legacy, inferred)"
    if "net/minecraftforge/registries/DeferredRegister" in joined or "net/minecraftforge/fml/javafmlmod/FMLJavaModLoadingContext" in joined:
        return "Forge (modern, inferred)"
    if "net/fabricmc/api/ModInitializer" in joined or "net/fabricmc/fabric/api" in joined:
        return "Fabric-style (inferred)"
    return "unknown"


def _detect_provider_role(mod_ids: list[str], mod_name: str, metadata: dict[str, Any]) -> tuple[str, str]:
    ids = {x.strip().lower() for x in mod_ids if x.strip()}
    if ids & _KNOWN_ARCHITECTURAL_IDS:
        return "architectural_fallback", "Known architectural/content mod; use only through reviewed fallback rules."
    if ids & _KNOWN_BACKPORT_IDS:
        return "backport_provider", "Known vanilla-content backport provider."

    description = ""
    if isinstance(metadata, dict):
        description = str(metadata.get("description") or "")
        first = metadata.get("first_mod")
        if isinstance(first, dict):
            description += " " + str(first.get("description") or "")
    hay = f"{mod_name} {description} {' '.join(ids)}".lower()
    if any(token in hay for token in ("backport", "back port", "future to now", "brings the future", "up to date")):
        return "backport_provider", "Mod metadata/name indicates a vanilla-version backport provider."
    return "general", "General mod catalog."


def _camel_to_snake(value: str) -> str:
    value = value.replace("-", "_").replace(".", "_")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"_+", "_", value).strip("_").lower()


def _candidate_aliases(rel: str) -> list[str]:
    aliases: list[str] = []
    for candidate in (rel, _camel_to_snake(rel)):
        candidate = candidate.strip("/").lower()
        if candidate and candidate not in aliases:
            aliases.append(candidate)
    # Common holder-field prefix; deliberately conservative because provider
    # aliases participate in automatic conversion decisions.
    for candidate in list(aliases):
        if candidate.startswith("block_") and candidate[6:]:
            aliases.append(candidate[6:])
    return aliases


def _asset_stem(path: str) -> str:
    return PurePosixPath(path).stem


def _normalized_asset_token(value: str) -> str:
    value = _camel_to_snake(value.rsplit("/", 1)[-1])
    for prefix in ("tile_entity_", "tileentity_", "block_entity_", "blockentity_", "model_"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value


_PREVIEW_GENERIC_WORDS = {
    "block", "blocks", "tile", "tiles", "cfb", "lit", "unlit", "base", "model",
}


def _preview_words(value: str) -> tuple[str, ...]:
    token = _camel_to_snake(value.rsplit("/", 1)[-1]).lower()
    return tuple(word for word in token.split("_") if word and word not in _PREVIEW_GENERIC_WORDS)


def _texture_match_score(candidate: str, texture_rel: str) -> int:
    """Score how likely a packaged texture belongs to a legacy registry candidate.

    Legacy 1.7.10 backports frequently register blocks under their own namespace
    while intentionally packaging Mojang-style block textures under
    ``assets/minecraft``. They also tend to split one logical block into
    ``*_top``, ``*_bottom``, ``*_side`` or renderer-specific textures.
    """
    candidate_token = _camel_to_snake(candidate.rsplit("/", 1)[-1]).lower()
    texture_token = _camel_to_snake(texture_rel.rsplit("/", 1)[-1]).lower()
    aliases = _candidate_aliases(candidate_token)
    if texture_token in aliases:
        return 100
    for alias in aliases:
        if texture_token.startswith(alias + "_"):
            return 92

    cwords = set(_preview_words(candidate_token))
    twords = set(_preview_words(texture_token))
    if not cwords or not twords:
        return 0
    decorative = {"top", "bottom", "side", "front", "back", "end", "open", "overlay", "fire", "log", "inside", "outside"}
    structural_t = twords - decorative
    if cwords == structural_t:
        return 88
    extras = twords - cwords
    if cwords.issubset(twords) and len(cwords) >= 1 and extras.issubset(decorative):
        return 80
    return 0


def _associate_preview_textures(
    rel: str,
    namespace: str,
    textures: dict[tuple[str, str], list[str]],
    provider_role: str,
) -> list[str]:
    """Find texture families for legacy/code-rendered blocks without executing them."""
    allowed_namespaces = {namespace}
    if provider_role == "backport_provider":
        # Et Futurum is the motivating case: its legacy block registry is
        # ``etfuturum:*`` but many faithful modern-vanilla textures live under
        # ``assets/minecraft/textures/blocks``.
        allowed_namespaces.add("minecraft")

    scored: list[tuple[int, str]] = []
    for (asset_ns, asset_rel), paths in textures.items():
        if asset_ns not in allowed_namespaces:
            continue
        score = _texture_match_score(rel, asset_rel)
        if score <= 0:
            continue
        for path in paths:
            scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], item[1]))
    # Keep a bounded family. Doors/barrels/campfires need several related
    # textures, but broad fuzzy matches must not flood the catalog.
    return [path for _score, path in scored[:12]]


def _infer_model_kind(rel: str, model_paths: list[str], blockstate_path: str) -> str:
    token = _camel_to_snake(rel).lower()
    if "campfire" in token:
        return "campfire"
    if token.endswith("_hanging_sign") or "hanging_sign" in token:
        return "hanging_sign"
    if token.endswith("_sign") or token.endswith("_wall_sign"):
        return "sign"
    if token.endswith("_door") or token.startswith("door_"):
        return "door"
    if token.endswith("_trapdoor") or token.startswith("trapdoor_"):
        return "trapdoor"
    if "lantern" in token and "sea_lantern" not in token:
        return "lantern"
    if token.endswith("_carpet"):
        return "carpet"
    if any(x in token for x in ("sapling", "flower", "grass", "fern", "lichen", "roots", "vine", "seagrass", "kelp", "mushroom")):
        return "cross"
    if token.endswith("_slab") or token.startswith("slab_"):
        return "slab"
    if token.endswith("_stairs") or token.startswith("stairs_"):
        return "stairs"
    if token.endswith("_wall") or token.startswith("wall_"):
        return "wall"
    if token.endswith("_fence") or token.endswith("_fence_gate") or token.startswith("fence_"):
        return "fence"
    if token.endswith("_pane") or "bars" in token:
        return "pane"
    if any(p.lower().endswith(".obj") for p in model_paths):
        return "obj"
    if any(p.lower().endswith(".json") for p in model_paths) or blockstate_path:
        return "json"
    if model_paths:
        return "legacy_model"
    return "cube"


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


def _blockstate_model_refs(zf: zipfile.ZipFile, blockstate_path: str) -> list[str]:
    try:
        obj = _safe_json(zf.read(blockstate_path))
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []
    refs: list[str] = []

    def collect(value):
        if isinstance(value, dict):
            model = value.get("model")
            if isinstance(model, str):
                refs.append(model)
        elif isinstance(value, list):
            for part in value:
                collect(part)

    variants = obj.get("variants")
    if isinstance(variants, dict):
        for value in variants.values():
            collect(value)
    multipart = obj.get("multipart")
    if isinstance(multipart, list):
        for part in multipart:
            if isinstance(part, dict):
                collect(part.get("apply"))
    return refs


def _model_ref_to_path(ref: str, default_ns: str) -> str:
    if ":" in ref:
        ns, rel = ref.split(":", 1)
    else:
        ns, rel = default_ns, ref
    return f"assets/{ns}/models/{rel}.json"


def _metadata_from_mods_toml(parsed: dict[str, Any]) -> tuple[list[str], str, str]:
    first = parsed.get("first_mod", {}) if isinstance(parsed, dict) else {}
    if not isinstance(first, dict):
        return [], "", ""
    ids = [str(first["modId"])] if first.get("modId") else []
    return ids, str(first.get("displayName") or ""), str(first.get("version") or "")


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
    classes = _class_inventory(zf, names)
    manifest = _manifest_values(zf.read("META-INF/MANIFEST.MF")) if "META-INF/MANIFEST.MF" in name_set else {}

    loader = "unknown"
    metadata: dict[str, Any] = {}
    mod_ids: list[str] = []
    mod_name = ""
    mod_version = ""
    minecraft_hint = ""

    if "META-INF/neoforge.mods.toml" in name_set:
        loader = "NeoForge"
        metadata = _parse_mods_toml(zf.read("META-INF/neoforge.mods.toml"))
        mod_ids, mod_name, mod_version = _metadata_from_mods_toml(metadata)
    elif "META-INF/mods.toml" in name_set:
        loader = "Forge (1.13+)"
        metadata = _parse_mods_toml(zf.read("META-INF/mods.toml"))
        mod_ids, mod_name, mod_version = _metadata_from_mods_toml(metadata)
    elif "quilt.mod.json" in name_set:
        loader = "Quilt"
        obj = _safe_json(zf.read("quilt.mod.json")) or {}
        metadata = obj if isinstance(obj, dict) else {}
        ql = obj.get("quilt_loader") if isinstance(obj, dict) else None
        if isinstance(ql, dict):
            if ql.get("id"): mod_ids = [str(ql["id"])]
            meta = ql.get("metadata")
            if isinstance(meta, dict): mod_name = str(meta.get("name") or "")
            mod_version = str(ql.get("version") or "")
        depends = ql.get("depends") if isinstance(ql, dict) else None
        if isinstance(depends, list):
            for dep in depends:
                if isinstance(dep, dict) and dep.get("id") == "minecraft":
                    minecraft_hint = str(dep.get("versions") or dep.get("version") or "")
    elif "fabric.mod.json" in name_set:
        loader = "Fabric"
        obj = _safe_json(zf.read("fabric.mod.json")) or {}
        metadata = obj if isinstance(obj, dict) else {}
        if isinstance(obj, dict):
            if obj.get("id"): mod_ids = [str(obj["id"])]
            mod_name = str(obj.get("name") or "")
            mod_version = str(obj.get("version") or "")
            depends = obj.get("depends")
            if isinstance(depends, dict) and "minecraft" in depends:
                minecraft_hint = str(depends.get("minecraft") or "")
    elif "mcmod.info" in name_set:
        loader = "Forge/FML (legacy)"
        obj = _parse_mcmod(zf.read("mcmod.info"))
        metadata = obj
        if obj.get("modid"): mod_ids = [str(obj["modid"])]
        mod_name = str(obj.get("name") or "")
        mod_version = str(obj.get("version") or "")
        minecraft_hint = str(obj.get("mcversion") or obj.get("acceptedMinecraftVersions") or "")
    elif "litemod.json" in name_set:
        loader = "LiteLoader"
        obj = _safe_json(zf.read("litemod.json")) or {}
        metadata = obj if isinstance(obj, dict) else {}
        if isinstance(obj, dict):
            ident = obj.get("name") or obj.get("id")
            if ident: mod_ids = [str(ident)]
            mod_name = str(obj.get("displayName") or obj.get("name") or "")
            mod_version = str(obj.get("version") or "")
            minecraft_hint = str(obj.get("mcversion") or obj.get("revision") or "")
    else:
        loader = _infer_loader_from_classes(classes, manifest)
        if loader != "unknown":
            metadata = {"inferred_from_bytecode": True, "manifest": manifest}

    assets_namespaces = sorted({n.split("/", 2)[1] for n in names if n.startswith("assets/") and n.count("/") >= 2})
    if not mod_ids:
        mod_ids = [ns for ns in assets_namespaces if ns != "minecraft"][:8]
    primary_ns = mod_ids[0] if mod_ids else (assets_namespaces[0] if assets_namespaces else "minecraft")

    provider_role, provider_reason = _detect_provider_role(mod_ids, mod_name, metadata)
    log(f"Reading assets/classes from {source_label}")

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

    class_ev = _class_evidence(classes)
    static_fields: set[str] = class_ev["static_block_fields"]
    enum_fields: set[str] = class_ev["enum_block_fields"]
    legacy_fields = static_fields | enum_fields
    block_entity_classes: list[str] = class_ev["block_entity_classes"]

    if legacy_fields:
        log(
            "Class registry evidence: %d static Block field(s), %d enum-backed block registration(s), %d block entity class(es)"
            % (len(static_fields), len(enum_fields), len(block_entity_classes))
        )

    # A dedicated enum registry is stronger than miscellaneous static Block
    # caches elsewhere in the same JAR. When one is present (Et Futurum is the
    # motivating example), use the enum entries as the registration candidate
    # set and retain static-field counts only as supporting diagnostics.
    registry_fields = enum_fields if enum_fields else static_fields
    class_backed: set[tuple[str, str]] = {(primary_ns, field) for field in registry_fields}
    if class_backed:
        # Modern blockstates remain authoritative in hybrid jars. With strong legacy
        # class evidence, do not promote every decorative textures/blocks PNG to a block.
        keys = set(blockstates) | class_backed
    else:
        keys = set(blockstates) | set(modern_models) | set(textures)

    blocks: list[BlockAsset] = []
    associated_model_assets: set[str] = set()
    for ns, rel in sorted(keys):
        bs = blockstates.get((ns, rel), "")
        model_list = list(modern_models.get((ns, rel), []))
        tex_list = list(textures.get((ns, rel), []))
        is_static = rel in static_fields and rel in registry_fields and ns == primary_ns
        is_enum = rel in enum_fields and ns == primary_ns
        is_class_backed = is_static or is_enum

        if is_class_backed and provider_role == "backport_provider":
            for texture_path in _associate_preview_textures(rel, ns, textures, provider_role):
                if texture_path not in tex_list:
                    tex_list.append(texture_path)

        if bs:
            for model_ref in _blockstate_model_refs(zf, bs):
                mp = _model_ref_to_path(model_ref, ns)
                if mp in name_set and mp not in model_list:
                    model_list.append(mp)

        if is_class_backed:
            for mp in all_models_by_stem.get((ns, rel), []):
                if mp not in model_list:
                    model_list.append(mp)
                associated_model_assets.add(mp)

        if bs:
            confidence = "high"
            evidence = "blockstate JSON"
            candidate_kind = "registered block candidate"
        elif is_enum:
            confidence = "high" if (model_list or tex_list or display_names.get((ns, rel))) else "medium"
            pieces = ["legacy enum block registry entry"]
            if model_list: pieces.append("packaged model")
            if tex_list: pieces.append("matching block texture")
            if display_names.get((ns, rel)): pieces.append("localization entry")
            evidence = " + ".join(pieces)
            candidate_kind = "registered block candidate"
        elif is_static:
            confidence = "high" if (model_list or tex_list or display_names.get((ns, rel))) else "medium"
            pieces = ["legacy static Block field"]
            if model_list: pieces.append("packaged model")
            if tex_list: pieces.append("matching block texture")
            if display_names.get((ns, rel)): pieces.append("localization entry")
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

        model_list = sorted(set(model_list))
        tex_list = sorted(set(tex_list))
        blocks.append(BlockAsset(
            namespace=ns,
            registry_hint=f"{ns}:{rel}",
            display_name=display,
            confidence=confidence,
            evidence=evidence,
            texture_paths=tex_list,
            model_paths=model_list,
            blockstate_path=bs,
            source_mod=(mod_ids[0] if mod_ids else ns),
            source_file=source_label,
            candidate_kind=candidate_kind,
            localization_locale=locale_used,
            mapping_aliases=_candidate_aliases(rel),
            model_kind=_infer_model_kind(rel, model_list, bs),
        ))

    # Block/tile entity classes do not always expose a stable registry name in
    # bytecode, so class identity is kept distinct from an optional registry hint.
    block_entities: list[BlockEntityAsset] = []
    for class_name in block_entity_classes:
        simple = class_name.rsplit("/", 1)[-1]
        token = _normalized_asset_token(simple)
        model_matches: list[str] = []
        texture_matches: list[str] = []
        for (ns, stem), paths in all_models_by_stem.items():
            if ns == primary_ns and _normalized_asset_token(stem) == token:
                model_matches.extend(paths)
                associated_model_assets.update(paths)
        for (ns, rel), paths in textures.items():
            if ns == primary_ns and _normalized_asset_token(rel) == token:
                texture_matches.extend(paths)
        display = re.sub(r"(?<!^)(?=[A-Z])", " ", simple)
        display = re.sub(r"^(Tile Entity|Block Entity)\s*", "", display, flags=re.I).strip() or simple
        block_entities.append(BlockEntityAsset(
            namespace=primary_ns,
            class_name=class_name.replace("/", "."),
            registry_hint="",
            display_name=display,
            confidence="high" if (model_matches or texture_matches) else "medium",
            evidence="packaged TileEntity/BlockEntity subclass" + (" + matching static model asset" if model_matches else ""),
            texture_paths=sorted(set(texture_matches)),
            model_paths=sorted(set(model_matches)),
            source_mod=(mod_ids[0] if mod_ids else primary_ns),
            source_file=source_label,
        ))

    notes: list[str] = []
    if loader.endswith("inferred)"):
        notes.append("No standard mod metadata file was required: the loader family was inferred from packaged class/manifest evidence.")
    if static_fields:
        notes.append(f"Class analysis found {len(static_fields):,} static Block holder field(s), including fields declared as packaged Block subclasses.")
    if enum_fields:
        notes.append(
            f"Class analysis found {len(enum_fields):,} enum-backed block registry entries. This covers registry styles such as Et Futurum Requiem's ModBlocks enum that older analyzer builds missed."
        )
    if block_entity_classes:
        notes.append(
            f"Detected {len(block_entity_classes):,} packaged TileEntity/BlockEntity subclass(es). They are shown separately from blocks; static analysis does not invent a registry ID when the JAR does not expose one safely."
        )
    notes.append(
        f"Model scan found {len(all_model_assets):,} packaged JSON/OBJ/DAE/HMF/TCN model assets; {len(associated_model_assets):,} were associated directly with a block or block-entity candidate."
    )
    notes.append("Display names prefer en_US, then other English locales, then non-English translations only as a final fallback.")
    notes.append(
        "The desktop preview uses texture-aware JSON geometry, UV-aware OBJ geometry, cross-namespace backport textures, and shape-aware legacy renderer approximations. Runtime TESRs/BERs and arbitrary mod code are never executed by the analyzer."
    )
    if provider_role == "backport_provider":
        notes.append("This catalog is classified as a backport provider. Exact same-name registered blocks can outrank approximate HBM/vanilla fallbacks when the target world registry confirms that block is actually enabled.")

    analysis_stats = {
        "packaged_block_textures": sum(len(v) for v in textures.values()),
        "packaged_model_assets": len(all_model_assets),
        "associated_model_assets": len(associated_model_assets),
        "legacy_static_block_fields": len(static_fields),
        "legacy_enum_block_entries": len(enum_fields),
        "legacy_block_subclasses": len(class_ev["block_classes"]),
        "legacy_tile_entity_subclasses": len(block_entity_classes),
        "packaged_block_entity_classes": len(block_entity_classes),
        "block_entity_type_fields": len(class_ev["block_entity_type_fields"]),
        "locales_found": locales_found,
        "provider_role": provider_role,
    }

    return ModCatalog(
        source=source_label,
        loader_hint=loader,
        minecraft_hint=minecraft_hint,
        mod_ids=mod_ids,
        mod_name=mod_name,
        mod_version=mod_version,
        blocks=blocks,
        block_entities=block_entities,
        notes=notes,
        raw_metadata=metadata if isinstance(metadata, dict) else {},
        analysis_stats=analysis_stats,
        provider_role=provider_role,
        provider_reason=provider_reason,
    )


def read_asset_bytes(jar_path: str | Path, asset_path: str) -> bytes:
    with zipfile.ZipFile(jar_path, "r") as zf:
        return zf.read(asset_path)


def read_texture_bytes(jar_path: str | Path, texture_path: str) -> bytes:
    return read_asset_bytes(jar_path, texture_path)


def _resolve_json_model(zf: zipfile.ZipFile, model_path: str, max_depth: int = 12) -> dict[str, Any]:
    """Resolve a packaged JSON model's parent chain without needing Minecraft."""
    name_set = set(zf.namelist())
    merged_textures: dict[str, str] = {}
    elements = None
    parent = ""
    current = model_path
    seen: set[str] = set()
    depth = 0
    while current and current not in seen and depth < max_depth:
        seen.add(current); depth += 1
        try:
            obj = _safe_json(zf.read(current))
        except Exception:
            break
        if not isinstance(obj, dict):
            break
        textures = obj.get("textures")
        if isinstance(textures, dict):
            # Child values should win over parent values.
            merged_textures = {**{str(k): str(v) for k, v in textures.items() if isinstance(v, str)}, **merged_textures}
        if elements is None and isinstance(obj.get("elements"), list):
            elements = obj.get("elements")
        p = obj.get("parent")
        if not isinstance(p, str) or not p:
            parent = parent or ""
            break
        parent = parent or p
        ns = current.split("/", 2)[1] if current.startswith("assets/") else "minecraft"
        next_path = _model_ref_to_path(p, ns)
        if next_path not in name_set:
            # Vanilla parent models are normally outside the mod JAR; preserving
            # the parent identifier is enough to infer a static preview shape.
            break
        current = next_path
    return {"textures": merged_textures, "elements": elements or [], "parent": parent}


def _texture_role_from_path(path: str) -> str:
    stem = _camel_to_snake(PurePosixPath(path).stem).lower()
    words = set(stem.split("_"))
    if "campfire" in stem and "fire" in words:
        return "fire"
    if "campfire" in stem and "log" in words and "lit" in words:
        return "log_lit"
    if "campfire" in stem and "log" in words:
        return "log"
    if "door" in stem and stem.endswith("_top"):
        return "door_top"
    if "door" in stem and stem.endswith("_bottom"):
        return "door_bottom"
    if stem.endswith("_top_open"):
        return "top_open"
    for suffix, role in (
        ("_top", "top"), ("_bottom", "bottom"), ("_side", "side"),
        ("_front", "front"), ("_back", "back"), ("_end", "end"),
        ("_overlay", "overlay"),
    ):
        if stem.endswith(suffix):
            return role
    return "all"


def _texture_roles(paths: list[str]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for path in paths:
        role = _texture_role_from_path(path)
        # Prefer the first/highest-scored analyzer association for each role.
        roles.setdefault(role, path)
    if paths and "all" not in roles:
        roles["all"] = paths[0]
    return roles


def _resolve_texture_variable(value: str, textures: dict[str, str], max_depth: int = 12) -> str:
    current = value
    seen: set[str] = set()
    for _ in range(max_depth):
        if not current.startswith("#"):
            return current
        key = current[1:]
        if key in seen:
            break
        seen.add(key)
        current = str(textures.get(key) or "")
        if not current:
            break
    return ""


def _json_texture_bindings(
    model_path: str,
    resolved: dict[str, Any],
    names: set[str],
) -> dict[str, str]:
    ns = model_path.split("/", 2)[1] if model_path.startswith("assets/") else "minecraft"
    raw = resolved.get("textures") or {}
    bindings: dict[str, str] = {}
    if not isinstance(raw, dict):
        return bindings
    for key, value in raw.items():
        if not isinstance(value, str):
            continue
        resolved_value = _resolve_texture_variable(value, raw)
        if not resolved_value:
            continue
        if ":" in resolved_value:
            tns, rel = resolved_value.split(":", 1)
        else:
            tns, rel = ns, resolved_value
        path = f"assets/{tns}/textures/{rel}.png"
        if path in names:
            bindings[str(key)] = path
    return bindings


def _parse_obj_geometry(raw: bytes) -> tuple[list[list[float]], list[list[float]], list[dict[str, Any]]]:
    """Parse bounded OBJ geometry including UV indexes for safe static previews."""
    vertices: list[list[float]] = []
    texcoords: list[list[float]] = []
    faces: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("v "):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    vertices.append([float(parts[1]), float(parts[2]), float(parts[3])])
                except ValueError:
                    pass
        elif line.startswith("vt "):
            parts = line.split()
            if len(parts) >= 3:
                try:
                    texcoords.append([float(parts[1]), float(parts[2])])
                except ValueError:
                    pass
        elif line.startswith("f "):
            vertex_indexes: list[int] = []
            uv_indexes: list[int | None] = []
            for part in line.split()[1:]:
                fields = part.split("/")
                try:
                    idx = int(fields[0])
                    idx = len(vertices) + idx if idx < 0 else idx - 1
                except (ValueError, IndexError):
                    continue
                if not (0 <= idx < len(vertices)):
                    continue
                uv_idx: int | None = None
                if len(fields) > 1 and fields[1]:
                    try:
                        parsed_uv = int(fields[1])
                        parsed_uv = len(texcoords) + parsed_uv if parsed_uv < 0 else parsed_uv - 1
                        if 0 <= parsed_uv < len(texcoords):
                            uv_idx = parsed_uv
                    except ValueError:
                        pass
                vertex_indexes.append(idx)
                uv_indexes.append(uv_idx)
            if len(vertex_indexes) >= 3:
                faces.append({"vertices": vertex_indexes, "uvs": uv_indexes})
    return vertices, texcoords, faces


def build_preview_spec(jar_path: str | Path, candidate: Any) -> dict[str, Any]:
    """Build a safe, texture-aware static preview description for one row.

    No mod code is loaded or executed. The spec preserves enough material data
    for the Qt side to render recognizable legacy blocks, full two-block doors,
    custom campfire geometry, JSON elements and UV-mapped OBJ models.
    """
    if hasattr(candidate, "__dict__"):
        data = dict(candidate.__dict__)
    elif isinstance(candidate, dict):
        data = dict(candidate)
    else:
        raise TypeError("preview candidate must be a catalog asset")

    textures = [str(x) for x in (data.get("texture_paths") or [])]
    models = [str(x) for x in (data.get("model_paths") or [])]
    blockstate = str(data.get("blockstate_path") or "")
    registry = str(data.get("registry_hint") or data.get("class_name") or "")
    model_kind = str(data.get("model_kind") or "")

    with zipfile.ZipFile(jar_path, "r") as zf:
        names = set(zf.namelist())
        if blockstate and blockstate in names:
            default_ns = blockstate.split("/", 2)[1]
            for ref in _blockstate_model_refs(zf, blockstate):
                mp = _model_ref_to_path(ref, default_ns)
                if mp in names and mp not in models:
                    models.append(mp)

        json_model = next((m for m in models if m.lower().endswith(".json") and m in names), "")
        obj_model = next((m for m in models if m.lower().endswith(".obj") and m in names), "")
        if json_model:
            resolved = _resolve_json_model(zf, json_model)
            bindings = _json_texture_bindings(json_model, resolved, names)
            for path in bindings.values():
                if path not in textures:
                    textures.append(path)
            parent = str(resolved.get("parent") or "").lower()
            shape = model_kind or "json"
            if "cross" in parent:
                shape = "cross"
            elif "slab" in parent:
                shape = "slab"
            elif "stairs" in parent:
                shape = "stairs"
            elif "fence" in parent:
                shape = "fence"
            elif "pane" in parent or "bars" in parent:
                shape = "pane"
            elif resolved.get("elements"):
                shape = "elements"
            elif shape in {"json", ""}:
                shape = "cube"
            return {
                "kind": shape,
                "registry": registry,
                "model_path": json_model,
                "texture_paths": textures,
                "texture_roles": _texture_roles(textures),
                "texture_bindings": bindings,
                "elements": resolved.get("elements") or [],
                "parent": resolved.get("parent") or "",
                "note": "Texture-aware static JSON model preview",
            }

        if obj_model:
            vertices, texcoords, faces = _parse_obj_geometry(zf.read(obj_model))
            if vertices and faces:
                return {
                    "kind": "obj",
                    "registry": registry,
                    "model_path": obj_model,
                    "texture_paths": textures,
                    "texture_roles": _texture_roles(textures),
                    "vertices": vertices,
                    "texcoords": texcoords,
                    "faces": faces,
                    "note": "UV-mapped static OBJ geometry preview" if texcoords else "Static OBJ geometry preview (no UV coordinates packaged)",
                }

    shape = model_kind or "cube"
    if shape in {"json", "legacy_model", "obj", ""}:
        shape = "cube" if textures else "asset"
    note = "Shape-aware static asset preview" if textures or models else "No packaged static model/texture was linked"
    if shape == "door" and textures:
        note = "Synthesized full two-block door preview from packaged top/bottom textures"
    elif shape == "campfire" and textures:
        note = "Synthesized campfire preview from packaged legacy renderer textures"
    return {
        "kind": shape,
        "registry": registry,
        "model_path": models[0] if models else "",
        "texture_paths": textures,
        "texture_roles": _texture_roles(textures),
        "note": note,
    }
