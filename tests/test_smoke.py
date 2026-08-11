from __future__ import annotations
import json
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wgmap_backporter_studio.core.jar_analyzer import analyze_jar, build_preview_spec
from wgmap_backporter_studio.core.modpack_analyzer import analyze_modpack
from wgmap_backporter_studio.core.version_targets import TARGET_BY_VERSION
from wgmap_backporter_studio.core.mapping_profiles import profile_from_catalog_snapshot
from wgmap_backporter_studio.core.workspace_store import WorkspaceStore
from wgmap_backporter_studio.core import legacy1710_engine
from wgmap_backporter_studio.app import packaged_self_test


def _minimal_block_holder_class() -> bytes:
    """Build a tiny valid class with one public static Minecraft Block field."""
    cp = []

    def utf8(value: str):
        raw = value.encode("utf-8")
        cp.append(b"\x01" + struct.pack(">H", len(raw)) + raw)
        return len(cp)

    def class_ref(name_index: int):
        cp.append(b"\x07" + struct.pack(">H", name_index))
        return len(cp)

    this_name = utf8("demo/ModBlocks")
    this_class = class_ref(this_name)
    super_name = utf8("java/lang/Object")
    super_class = class_ref(super_name)
    field_name = utf8("demo_brick")
    field_desc = utf8("Lnet/minecraft/block/Block;")

    out = bytearray()
    out += struct.pack(">IHHH", 0xCAFEBABE, 0, 52, len(cp) + 1)
    for entry in cp:
        out += entry
    out += struct.pack(">HHH", 0x0021, this_class, super_class)  # public + super
    out += struct.pack(">H", 0)  # interfaces
    out += struct.pack(">H", 1)  # fields
    out += struct.pack(">HHHH", 0x0009, field_name, field_desc, 0)  # public static
    out += struct.pack(">H", 0)  # methods
    out += struct.pack(">H", 0)  # class attributes
    return bytes(out)




def _minimal_enum_block_registry_class() -> bytes:
    """Tiny ModBlocks-style enum with representative backport entries."""
    cp = []

    def utf8(value: str):
        raw = value.encode("utf-8")
        cp.append(b"\x01" + struct.pack(">H", len(raw)) + raw)
        return len(cp)

    def class_ref(name_index: int):
        cp.append(b"\x07" + struct.pack(">H", name_index))
        return len(cp)

    this_name = utf8("future/ModBlocks")
    this_class = class_ref(this_name)
    super_name = utf8("java/lang/Enum")
    super_class = class_ref(super_name)
    field_names = [utf8(name) for name in ("MOSS_BLOCK", "HANGING_ROOTS", "CHERRY_DOOR", "CAMPFIRE")]
    own_desc = utf8("Lfuture/ModBlocks;")
    utf8("GameRegistry")
    utf8("registerBlock")
    utf8("net/minecraft/block/Block")
    utf8("cpw/mods/fml/common/Mod")

    out = bytearray()
    out += struct.pack(">IHHH", 0xCAFEBABE, 0, 52, len(cp) + 1)
    for entry in cp:
        out += entry
    out += struct.pack(">HHH", 0x4021, this_class, super_class)
    out += struct.pack(">H", 0)
    out += struct.pack(">H", len(field_names))
    for field_name in field_names:
        out += struct.pack(">HHHH", 0x4019, field_name, own_desc, 0)
    out += struct.pack(">H", 0)
    out += struct.pack(">H", 0)
    return bytes(out)


def _minimal_tile_entity_class() -> bytes:
    cp = []

    def utf8(value: str):
        raw = value.encode("utf-8")
        cp.append(b"\x01" + struct.pack(">H", len(raw)) + raw)
        return len(cp)

    def class_ref(name_index: int):
        cp.append(b"\x07" + struct.pack(">H", name_index))
        return len(cp)

    this_name = utf8("future/TileEntityFancySign")
    this_class = class_ref(this_name)
    super_name = utf8("net/minecraft/tileentity/TileEntity")
    super_class = class_ref(super_name)
    out = bytearray()
    out += struct.pack(">IHHH", 0xCAFEBABE, 0, 52, len(cp) + 1)
    for entry in cp:
        out += entry
    out += struct.pack(">HHH", 0x0021, this_class, super_class)
    out += struct.pack(">H", 0)
    out += struct.pack(">H", 0)
    out += struct.pack(">H", 0)
    out += struct.pack(">H", 0)
    return bytes(out)


def _minimal_modern_block_entity_class() -> bytes:
    cp = []

    def utf8(value: str):
        raw = value.encode("utf-8")
        cp.append(b"\x01" + struct.pack(">H", len(raw)) + raw)
        return len(cp)

    def class_ref(name_index: int):
        cp.append(b"\x07" + struct.pack(">H", name_index))
        return len(cp)

    this_name = utf8("modern/DisplayBlockEntity")
    this_class = class_ref(this_name)
    super_name = utf8("net/minecraft/world/level/block/entity/BlockEntity")
    super_class = class_ref(super_name)
    out = bytearray()
    out += struct.pack(">IHHH", 0xCAFEBABE, 0, 65, len(cp) + 1)
    for entry in cp:
        out += entry
    out += struct.pack(">HHH", 0x0021, this_class, super_class)
    out += struct.pack(">H", 0) + struct.pack(">H", 0) + struct.pack(">H", 0) + struct.pack(">H", 0)
    return bytes(out)


def make_modern_fabric_jar(path: Path):
    png = bytes.fromhex("89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000D49444154789C63606060F80F0001040100F805FF640000000049454E44AE426082")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("fabric.mod.json", json.dumps({
            "schemaVersion": 1, "id": "modern", "name": "Modern Decor",
            "version": "1.0", "depends": {"minecraft": ">=1.20 <=1.21.1"},
        }))
        z.writestr("assets/modern/blockstates/display_block.json", json.dumps({
            "variants": {"": {"model": "modern:block/display_block"}}
        }))
        z.writestr("assets/modern/models/block/display_block.json", json.dumps({
            "parent": "minecraft:block/cube_all",
            "textures": {"all": "modern:block/display_block"},
        }))
        z.writestr("assets/modern/textures/block/display_block.png", png)
        z.writestr("modern/DisplayBlockEntity.class", _minimal_modern_block_entity_class())


def make_enum_backport_jar(path: Path):
    png = bytes.fromhex("89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000D49444154789C63606060F80F0001040100F805FF640000000049454E44AE426082")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # Deliberately no mcmod.info: loader/mod-id inference must still work.
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nFMLCorePluginContainsFMLMod: true\n")
        # Et Futurum-style legacy providers commonly register under their own
        # namespace while packaging modern-vanilla textures under minecraft.
        z.writestr("assets/minecraft/textures/blocks/moss_block.png", png)
        z.writestr("assets/minecraft/textures/blocks/cherry_door_bottom.png", png)
        z.writestr("assets/minecraft/textures/blocks/cherry_door_top.png", png)
        z.writestr("assets/etfuturum/textures/blocks/campfire_log.png", png)
        z.writestr("assets/etfuturum/textures/blocks/campfire_fire.png", png)
        z.writestr("assets/etfuturum/textures/blocks/hanging_roots.png", png)
        z.writestr("assets/etfuturum/models/block/moss_block.json", json.dumps({
            "parent": "block/cube_all",
            "textures": {"all": "minecraft:blocks/moss_block"},
        }))
        z.writestr("assets/etfuturum/blockstates/moss_block.json", json.dumps({
            "variants": {"normal": {"model": "etfuturum:block/moss_block"}}
        }))
        z.writestr("assets/etfuturum/models/trinkets/fancy_sign.obj", "\n".join([
            "v 0 0 0", "v 1 0 0", "v 0 1 0",
            "vt 0 0", "vt 1 0", "vt 0 1",
            "f 1/1 2/2 3/3",
        ]))
        z.writestr("assets/etfuturum/textures/models/trinkets/fancy_sign.png", png)
        z.writestr("future/ModBlocks.class", _minimal_enum_block_registry_class())
        z.writestr("future/TileEntityFancySign.class", _minimal_tile_entity_class())

def make_fake_jar(path: Path):
    mcmod = [{"modid":"demo","name":"Demo Blocks","version":"1.0","mcversion":"1.7.10"}]
    png = bytes.fromhex("89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000D49444154789C63606060F80F0001040100F805FF640000000049454E44AE426082")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mcmod.info", json.dumps(mcmod))
        # Put Chinese first deliberately: analyzer language priority must still
        # choose en_US rather than ZIP member order.
        z.writestr("assets/demo/lang/zh_CN.lang", "tile.demo_brick.name=演示砖\n")
        z.writestr("assets/demo/lang/en_US.lang", "tile.demo_brick.name=Demo Brick\n")
        z.writestr("assets/demo/textures/blocks/demo_brick.png", png)
        z.writestr("assets/demo/textures/blocks/particle/noise.png", png)
        z.writestr("assets/demo/models/blocks/demo_brick.obj", "o DemoBrick\n")
        z.writestr("demo/ModBlocks.class", _minimal_block_holder_class())


def test_forge1710_itemdata_registry():
    fml = {
        "ItemData": [
            {"K":"\x01minecraft:air","V":0},
            {"K":"\x01minecraft:stone","V":1},
            {"K":"\x01minecraft:grass","V":2},
            {"K":"\x01minecraft:dirt","V":3},
            {"K":"\x01minecraft:cobblestone","V":4},
            {"K":"\x01minecraft:planks","V":5},
            {"K":"\x01minecraft:bedrock","V":7},
            {"K":"\x01minecraft:water","V":9},
            {"K":"\x01hbm:concrete","V":901},
            {"K":"\x02hbm:some_item","V":5001},
        ]
    }
    reg = legacy1710_engine._target_registry_from_fml(fml)
    assert reg.source_format == "Forge 1.7.10 FML/ItemData"
    assert reg.resolve("minecraft:stone") == 1
    assert reg.resolve("minecraft:bedrock") == 7
    assert reg.resolve_hbm("concrete") == ("hbm:concrete", 901)
    assert "\x01minecraft:stone" not in reg.ids
    assert "\x02hbm:some_item" not in reg.ids
    assert len(reg.ids) == 9
    assert reg.ignored_items == 1
    legacy1710_engine.validate_target_registry(reg, True, log=lambda _: None)

    try:
        legacy1710_engine._target_registry_from_fml({"ItemData":[{"K":"\x02minecraft:stick","V":280}]})
    except legacy1710_engine.ConversionError as exc:
        assert "U+0001 block entries" in str(exc)
    else:
        raise AssertionError("item-only Forge ItemData must not be accepted as a block registry")



def test_catalog_bound_mapping_profile():
    reg = legacy1710_engine.TargetRegistry({
        "minecraft:air": 0,
        "minecraft:stone": 1,
        "minecraft:grass": 2,
        "minecraft:dirt": 3,
        "minecraft:cobblestone": 4,
        "minecraft:planks": 5,
        "minecraft:bedrock": 7,
        "minecraft:water": 9,
        "minecraft:stained_hardened_clay": 159,
        "hbm:concrete_colored": 901,
    })

    enabled = profile_from_catalog_snapshot({
        "enabled_catalogs": ["HBM test catalog"],
        "enabled_mod_ids": ["hbm"],
        "registry_hints": ["hbm:concrete_colored"],
        "candidate_count": 1,
    }, True)
    assert enabled.catalog_bound
    assert enabled.allows_namespace("hbm")
    mapped = legacy1710_engine.map_modern(
        "minecraft:white_concrete", {}, reg, True, enabled
    )
    assert mapped.target == "hbm:concrete_colored"

    disabled = profile_from_catalog_snapshot({
        "enabled_catalogs": [],
        "enabled_mod_ids": [],
        "registry_hints": [],
        "candidate_count": 0,
    }, True)
    assert disabled.catalog_bound
    assert not disabled.allows_namespace("hbm")
    fallback = legacy1710_engine.map_modern(
        "minecraft:white_concrete", {}, reg, True, disabled
    )
    assert fallback.target == "minecraft:stained_hardened_clay"
    assert "Catalog Workspace" in fallback.note



def test_backport_provider_mapping_profile():
    reg = legacy1710_engine.TargetRegistry({
        "minecraft:air": 0, "minecraft:stone": 1, "minecraft:grass": 2,
        "minecraft:dirt": 3, "minecraft:cobblestone": 4, "minecraft:planks": 5,
        "minecraft:bedrock": 7, "minecraft:water": 9,
        "etfuturum:moss_block": 700,
    })
    snapshot = {
        "enabled_catalogs": ["Et Futurum Requiem"],
        "enabled_mod_ids": ["etfuturum"],
        "registry_hints": ["etfuturum:moss_block"],
        "candidate_count": 1,
        "backport_providers": [{
            "label": "Et Futurum Requiem",
            "blocks": [{
                "registry_hint": "etfuturum:moss_block",
                "confidence": "high",
                "candidate_kind": "registered block candidate",
                "mapping_aliases": ["moss_block"],
            }],
        }],
    }
    profile = profile_from_catalog_snapshot(snapshot, True)
    mapped = legacy1710_engine.map_modern("minecraft:moss_block", {}, reg, True, profile)
    assert mapped.target == "etfuturum:moss_block"
    assert mapped.quality == "backport_exact"

    # A provider catalog is advisory until the actual target-world registry confirms it.
    missing_reg = legacy1710_engine.TargetRegistry({
        "minecraft:air": 0, "minecraft:stone": 1, "minecraft:grass": 2,
        "minecraft:dirt": 3, "minecraft:cobblestone": 4, "minecraft:planks": 5,
        "minecraft:bedrock": 7, "minecraft:water": 9,
    })
    fallback = legacy1710_engine.map_modern("minecraft:moss_block", {}, missing_reg, True, profile)
    assert fallback.target == "minecraft:grass"
    assert fallback.quality == "approximate"

    # A provider must never hijack a vanilla 1.7.10 block that already exists.
    stone_snapshot = dict(snapshot)
    stone_snapshot["backport_providers"] = [{
        "label": "Provider",
        "blocks": [{
            "registry_hint": "etfuturum:stone", "confidence": "high",
            "candidate_kind": "registered block candidate", "mapping_aliases": ["stone"],
        }],
    }]
    stone_profile = profile_from_catalog_snapshot(stone_snapshot, True)
    stone = legacy1710_engine.map_modern("minecraft:stone", {}, reg, True, stone_profile)
    assert stone.target == "minecraft:stone"



def test_legacy_directional_metadata_and_provider_registry_diagnostics():
    # 1.7.10 trapdoor metadata is north=0, south=1, west=2, east=3.
    assert legacy1710_engine.trapdoor_meta({"facing": "north", "half": "bottom", "open": "false"}) == 0
    assert legacy1710_engine.trapdoor_meta({"facing": "south", "half": "bottom", "open": "false"}) == 1
    assert legacy1710_engine.trapdoor_meta({"facing": "west", "half": "bottom", "open": "false"}) == 2
    assert legacy1710_engine.trapdoor_meta({"facing": "east", "half": "bottom", "open": "false"}) == 3
    assert legacy1710_engine.trapdoor_meta({"facing": "east", "half": "top", "open": "true"}) == 15

    # Door facing already matched the 1.7.10 encoding; lock that in while also
    # checking lower open state and independent upper hinge/powered state.
    assert legacy1710_engine.door_meta({"half": "lower", "facing": "east", "open": "false"}) == 0
    assert legacy1710_engine.door_meta({"half": "lower", "facing": "south", "open": "false"}) == 1
    assert legacy1710_engine.door_meta({"half": "lower", "facing": "west", "open": "false"}) == 2
    assert legacy1710_engine.door_meta({"half": "lower", "facing": "north", "open": "true"}) == 7
    assert legacy1710_engine.door_meta({"half": "upper", "hinge": "left", "powered": "false"}) == 8
    assert legacy1710_engine.door_meta({"half": "upper", "hinge": "right", "powered": "true"}) == 11

    snapshot = {
        "enabled_catalogs": ["Et Futurum Requiem"],
        "enabled_mod_ids": ["etfuturum"],
        "registry_hints": ["etfuturum:moss_block"],
        "candidate_count": 1,
        "backport_providers": [{
            "label": "Et Futurum Requiem",
            "blocks": [{
                "registry_hint": "etfuturum:moss_block",
                "confidence": "high",
                "candidate_kind": "registered block candidate",
                "mapping_aliases": ["moss_block"],
            }],
        }],
    }
    profile = profile_from_catalog_snapshot(snapshot, True)
    missing = legacy1710_engine.TargetRegistry({
        "minecraft:air": 0, "minecraft:stone": 1, "minecraft:grass": 2,
        "minecraft:dirt": 3, "minecraft:cobblestone": 4, "minecraft:planks": 5,
        "minecraft:bedrock": 7, "minecraft:water": 9,
    })
    logs = []
    summary = legacy1710_engine.validate_target_registry(missing, True, logs.append, profile)
    assert summary["provider_namespace_entries"]["etfuturum"] == 0
    assert summary["backport_provider_targets_catalog"] == 1
    assert summary["backport_provider_targets_registered"] == 0
    assert any("etfuturum=0" in line for line in logs)
    assert any("has no registered blocks" in line for line in logs)


def test_preview_mode_asset_selection_and_legacy_model_disambiguation():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        jar = td / "preview-assets.jar"
        png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0000004010100c9fe92ef0000000049454e44ae426082")
        obj = "\n".join([
            "v 0 0 0", "v 1 0 0", "v 0 1 0",
            "vt 0 0", "vt 1 0", "vt 0 1",
            "f 1/1 2/2 3/3",
        ])
        with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("assets/demo/models/machines/crucible.obj", obj)
            z.writestr("assets/demo/models/weapons/crucible.obj", obj)
            z.writestr("assets/demo/textures/models/machines/crucible_heat.png", png)
            z.writestr("assets/demo/textures/items/crucible.png", png)
            z.writestr("assets/demo/textures/blocks/widget.png", png)
            z.writestr("assets/demo/textures/items/widget.png", png)

        machine = build_preview_spec(jar, {
            "namespace": "demo",
            "candidate_kind": "block entity",
            "class_name": "demo.machine.TileEntityCrucible",
            "display_name": "Crucible",
            "texture_paths": [
                "assets/demo/textures/models/machines/crucible_heat.png",
                "assets/demo/textures/items/crucible.png",
            ],
            "model_paths": [
                "assets/demo/models/machines/crucible.obj",
                "assets/demo/models/weapons/crucible.obj",
            ],
        })
        assert machine["model_path"].endswith("models/machines/crucible.obj")
        assert machine["render_texture_paths"] == ["assets/demo/textures/models/machines/crucible_heat.png"]
        assert machine["preview_2d_path"].endswith("textures/models/machines/crucible_heat.png")
        assert machine["preview_2d_kind"] == "model texture"

        block = build_preview_spec(jar, {
            "namespace": "demo",
            "candidate_kind": "registered block candidate",
            "registry_hint": "demo:widget",
            "display_name": "Widget",
            "texture_paths": ["assets/demo/textures/blocks/widget.png"],
            "model_paths": [],
            "model_kind": "cube",
        })
        assert block["preview_2d_path"].endswith("textures/items/widget.png")
        assert block["preview_2d_kind"] == "item icon"
        assert block["auto_preview_mode"] == "model"


def test_runtime_only_preview_is_not_invented_as_cube():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        jar = td / "runtime-only.jar"
        png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0000004010100c9fe92ef0000000049454e44ae426082")
        with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("assets/demo/textures/blocks/runtime_icon.png", png)
        card = build_preview_spec(jar, {
            "candidate_kind": "block entity",
            "class_name": "demo.TileEntityRuntime",
            "texture_paths": ["assets/demo/textures/blocks/runtime_icon.png"],
            "model_paths": [],
        })
        assert card["kind"] == "texture_card"
        unresolved = build_preview_spec(jar, {
            "candidate_kind": "block entity",
            "class_name": "demo.TileEntityInvisibleRuntime",
            "texture_paths": [],
            "model_paths": [],
        })
        assert unresolved["kind"] == "runtime_unresolved"

def _nbt_string_payload(value: str) -> bytes:
    return legacy1710_engine.nbt_name(value)


def test_content_audit_and_legacy_roundtrip():
    be = legacy1710_engine.p_compound([
        legacy1710_engine.tag(8, "id", _nbt_string_payload("minecraft:chest")),
        legacy1710_engine.tag(3, "x", legacy1710_engine.p_int(1)),
        legacy1710_engine.tag(3, "y", legacy1710_engine.p_int(64)),
        legacy1710_engine.tag(3, "z", legacy1710_engine.p_int(2)),
    ])
    modern = bytes([10]) + legacy1710_engine.nbt_name("") + legacy1710_engine.p_compound([
        legacy1710_engine.tag(3, "DataVersion", legacy1710_engine.p_int(3465)),
        legacy1710_engine.tag(3, "xPos", legacy1710_engine.p_int(0)),
        legacy1710_engine.tag(3, "zPos", legacy1710_engine.p_int(0)),
        legacy1710_engine.tag(9, "sections", legacy1710_engine.p_list(10, [])),
        legacy1710_engine.tag(9, "block_entities", legacy1710_engine.p_list(10, [be])),
    ])
    parsed = legacy1710_engine.parse_modern_chunk(modern)
    assert parsed["block_entities"][0]["id"] == "minecraft:chest"

    legacy = legacy1710_engine.make_chunk_nbt(
        0, 0, 0, [], [0] * 256, [1] * 256
    )
    info = legacy1710_engine.validate_legacy_chunk_nbt(legacy, 0, 0)
    assert info["xPos"] == 0 and info["zPos"] == 0

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        region = td / "r.0.0.mca"
        legacy1710_engine.write_region(region, {0: legacy})
        assert legacy1710_engine.verify_written_region(region, {0}) == 1

        world = td / "world"
        (world / "entities").mkdir(parents=True)
        entity = legacy1710_engine.p_compound([
            legacy1710_engine.tag(8, "id", _nbt_string_payload("minecraft:cow")),
        ])
        entity_chunk = bytes([10]) + legacy1710_engine.nbt_name("") + legacy1710_engine.p_compound([
            legacy1710_engine.tag(9, "Entities", legacy1710_engine.p_list(10, [entity])),
        ])
        legacy1710_engine.write_region(world / "entities" / "r.0.0.mca", {0: entity_chunk})
        audit = legacy1710_engine.audit_source_entities(world, td / "scratch", log=lambda _: None)
        assert audit["scan_status"] == "scanned"
        assert audit["entities_total"] == 1
        assert audit["entity_types"]["minecraft:cow"] == 1




def test_cross_generation_jar_analysis_and_preview():
    with tempfile.TemporaryDirectory() as td:
        jar = Path(td) / "future-backport.jar"
        make_enum_backport_jar(jar)
        cat = analyze_jar(jar)
        assert cat.loader_hint == "Forge/FML (legacy, inferred)"
        assert "etfuturum" in cat.mod_ids
        assert cat.provider_role == "backport_provider"
        hints = {block.registry_hint for block in cat.blocks}
        assert "etfuturum:moss_block" in hints
        assert "etfuturum:hanging_roots" in hints
        assert "etfuturum:cherry_door" in hints
        assert "etfuturum:campfire" in hints
        assert len(cat.block_entities) == 1
        assert "TileEntityFancySign" in cat.block_entities[0].class_name
        moss = next(block for block in cat.blocks if block.registry_hint == "etfuturum:moss_block")
        spec = build_preview_spec(jar, moss)
        assert spec["kind"] in {"cube", "elements"}
        assert spec["model_path"].endswith("moss_block.json")
        assert any(path == "assets/minecraft/textures/blocks/moss_block.png" for path in spec["texture_paths"])

        door = next(block for block in cat.blocks if block.registry_hint == "etfuturum:cherry_door")
        door_spec = build_preview_spec(jar, door)
        assert door_spec["kind"] == "door"
        assert door_spec["texture_roles"]["door_bottom"].endswith("cherry_door_bottom.png")
        assert door_spec["texture_roles"]["door_top"].endswith("cherry_door_top.png")

        campfire = next(block for block in cat.blocks if block.registry_hint == "etfuturum:campfire")
        campfire_spec = build_preview_spec(jar, campfire)
        assert campfire_spec["kind"] == "campfire"
        assert campfire_spec["texture_roles"]["log"].endswith("campfire_log.png")
        assert campfire_spec["texture_roles"]["fire"].endswith("campfire_fire.png")

        be_spec = build_preview_spec(jar, cat.block_entities[0])
        assert be_spec["kind"] == "obj"
        assert be_spec["texture_paths"][0] == "assets/etfuturum/textures/models/trinkets/fancy_sign.png"
        assert be_spec["texcoords"] and be_spec["faces"][0]["uvs"] == [0, 1, 2]
        assert "UV-mapped" in be_spec["note"]

        modern_jar = Path(td) / "modern-fabric.jar"
        make_modern_fabric_jar(modern_jar)
        modern = analyze_jar(modern_jar)
        assert modern.loader_hint == "Fabric"
        assert modern.minecraft_hint == ">=1.20 <=1.21.1"
        assert {b.registry_hint for b in modern.blocks} == {"modern:display_block"}
        assert len(modern.block_entities) == 1
        assert "DisplayBlockEntity" in modern.block_entities[0].class_name
        modern_spec = build_preview_spec(modern_jar, modern.blocks[0])
        assert modern_spec["model_path"].endswith("display_block.json")
        assert any(path.endswith("display_block.png") for path in modern_spec["texture_paths"])


def test_workspace_store_persistence():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "Documents" / "WG Map Backporter Studio"
        store = WorkspaceStore(root)
        store.ensure_layout()
        assert store.catalogs_dir.is_dir()
        assert store.workspaces_dir.is_dir()
        assert store.exports_dir.is_dir()

        catalog = {
            "schema": 1,
            "kind": "mod_block_catalog",
            "source": "/mods/demo.jar",
            "mod_ids": ["demo"],
            "mod_name": "Demo Blocks",
            "mod_version": "1.0",
            "blocks": [],
        }
        catalog_path = store.save_catalog_snapshot(catalog)
        assert catalog_path.parent == store.catalogs_dir
        assert json.loads(catalog_path.read_text(encoding="utf-8"))["mod_ids"] == ["demo"]

        workspace = {
            "schema": 1,
            "kind": "catalog_workspace",
            "sources": [{"enabled": True, "label": "Demo Blocks", "catalog": catalog}],
        }
        autosave = store.save_default_workspace(workspace)
        assert autosave == store.default_workspace_path
        restored = store.load_default_workspace()
        assert restored is not None
        assert restored["sources"][0]["enabled"] is True

        copy = store.save_workspace_copy(store.workspaces_dir / "copy.json", workspace)
        assert copy.is_file()

def test_staged_output_promotion():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        template = td / "template"
        template.mkdir()
        (template / "level.dat").write_bytes(b"template")
        output = td / "output"
        output.mkdir()
        staging = legacy1710_engine._prepare_staging_output(template, output)
        assert not output.exists()
        assert (staging / "level.dat").read_bytes() == b"template"
        (staging / "marker.txt").write_text("verified", encoding="utf-8")
        legacy1710_engine._promote_staging_output(staging, output)
        assert (output / "marker.txt").read_text(encoding="utf-8") == "verified"

def main():
    assert TARGET_BY_VERSION["1.7.10"].backend == "legacy1710"
    assert TARGET_BY_VERSION["1.7.10"].recommended_y_offset == 0
    assert TARGET_BY_VERSION["1.7.10"].recommended_strip_below_y == 0
    assert hasattr(legacy1710_engine, "run_conversion")
    assert hasattr(legacy1710_engine, "run_conversion_preflight")
    test_forge1710_itemdata_registry()
    test_catalog_bound_mapping_profile()
    test_backport_provider_mapping_profile()
    test_legacy_directional_metadata_and_provider_registry_diagnostics()
    test_preview_mode_asset_selection_and_legacy_model_disambiguation()
    test_runtime_only_preview_is_not_invented_as_cube()
    test_content_audit_and_legacy_roundtrip()
    test_cross_generation_jar_analysis_and_preview()
    test_staged_output_promotion()
    test_workspace_store_persistence()
    assert packaged_self_test() == 0
    with tempfile.TemporaryDirectory() as td:
        td = Path(td); jar = td / "demo.jar"; make_fake_jar(jar)
        cat = analyze_jar(jar)
        assert cat.mod_ids == ["demo"]
        assert len(cat.blocks) == 1, "legacy class evidence should exclude unrelated textures/blocks assets"
        block = cat.blocks[0]
        assert block.registry_hint == "demo:demo_brick"
        assert block.display_name == "Demo Brick"
        assert block.localization_locale == "en_us"
        assert block.confidence == "high"
        assert block.candidate_kind == "registered block candidate"
        assert block.model_paths == ["assets/demo/models/blocks/demo_brick.obj"]
        assert cat.analysis_stats["legacy_static_block_fields"] == 1
        assert cat.analysis_stats["packaged_model_assets"] == 1
        pack = td / "pack"; (pack / "mods").mkdir(parents=True); (pack / "mods" / "demo.jar").write_bytes(jar.read_bytes())
        rep = analyze_modpack(pack)
        assert rep.local_jars == 1 and rep.mods[0].block_candidates >= 1
        assert rep.mods[0].block_entities >= 0
        assert rep.mods[0].provider_role == "general"
    print("core smoke tests passed")

if __name__ == "__main__": main()
