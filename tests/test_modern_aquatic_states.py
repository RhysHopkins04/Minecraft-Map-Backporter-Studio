"""Actual Anvil metadata/NBT round trips for the coordinated EFR state contract."""
import itertools

import pytest

from wgmap_backporter_studio.core import legacy1710_engine as engine
from test_p018_legacy_lighting_and_ladders import _section, _modern_chunk, _stats, _metadata_at


FACING = {"down": 0, "up": 1, "north": 2, "south": 3, "west": 4, "east": 5}


def registry(compact=True):
    names = ["air", "stone", "snow", "water", "stained_glass", "stained_hardened_clay"]
    blocks = {"minecraft:" + name: i for i, name in enumerate(names)}
    parity = ["powder_snow", "amethyst_cluster_1", "amethyst_cluster_2", "pointed_dripstone",
              "kelp", "kelp_plant", "seagrass", "tall_seagrass", "sea_pickle"]
    for family, dead, form in itertools.product(["tube", "brain", "bubble", "fire", "horn"], ["", "dead_"], ["coral", "coral_fan", "coral_wall_fan"]):
        parity.append(dead + family + "_" + form)
    if compact:
        parity.append("kelp_age_16")
    blocks.update({"etfuturum:" + name: 2700 + i for i, name in enumerate(parity)})
    return engine.TargetRegistry(blocks)


def roundtrip(name, props, reg, offset=64, strip=0):
    raw = _modern_chunk([_section(0, [(name, props)])])
    _, converted = engine.convert_chunk(raw, reg, True, offset, strip, _stats())
    engine.validate_legacy_chunk_nbt(converted)
    _, parsed = engine.parse_nbt(converted)
    level = parsed["Level"]
    section = next(s for s in level["Sections"] if s["Y"] == offset // 16)
    ids = engine.np.frombuffer(section["Blocks"], dtype=engine.np.uint8).astype(engine.np.uint16)
    if "Add" in section:
        ids |= engine.unpack_nibbles(section["Add"]).astype(engine.np.uint16) << 8
    return level, int(ids[0]), _metadata_at(section), converted


@pytest.mark.parametrize("stage,target,extra", [("small_amethyst_bud", "amethyst_cluster_1", 0),
    ("medium_amethyst_bud", "amethyst_cluster_1", 6), ("large_amethyst_bud", "amethyst_cluster_2", 0),
    ("amethyst_cluster", "amethyst_cluster_2", 6)])
@pytest.mark.parametrize("facing,ordinal", FACING.items())
def test_amethyst_serialized_all_stages_and_facings(stage, target, extra, facing, ordinal):
    reg = registry()
    props = {"facing": facing, "waterlogged": "false"}
    mapped = engine.map_modern("minecraft:" + stage, props, reg, True)
    assert mapped.quality == "backport_exact"
    level, block_id, meta, _ = roundtrip("minecraft:" + stage, props, reg)
    assert (block_id, meta) == (reg.resolve("etfuturum:" + target), ordinal + extra)
    assert not level["TileEntities"]
    assert engine.map_modern("minecraft:" + stage, {**props, "waterlogged": "true"}, reg, True).quality == "backport_close"


@pytest.mark.parametrize("shape,meta", [("tip", 0), ("frustum", 1), ("middle", 2), ("base", 3), ("tip_merge", 4)])
@pytest.mark.parametrize("direction,extra", [("down", 0), ("up", 5)])
def test_dripstone_serialized_thickness_direction(shape, meta, direction, extra):
    reg = registry()
    props = {"thickness": shape, "vertical_direction": direction, "waterlogged": "false"}
    _, block_id, actual, _ = roundtrip("minecraft:pointed_dripstone", props, reg)
    assert (block_id, actual) == (reg.resolve("etfuturum:pointed_dripstone"), meta + extra)
    assert engine.map_modern("minecraft:pointed_dripstone", {**props, "waterlogged": "true"}, reg, True).quality == "backport_close"


@pytest.mark.parametrize("age", range(26))
def test_compact_kelp_all_ages_serialized_without_tiles(age):
    reg = registry()
    level, block_id, meta, _ = roundtrip("minecraft:kelp", {"age": str(age)}, reg)
    target = "etfuturum:kelp" if age < 16 else "etfuturum:kelp_age_16"
    assert (block_id, meta) == (reg.resolve(target), age & 15)
    assert not level["TileEntities"]


@pytest.mark.parametrize("age", range(26))
def test_legacy_kelp_byte_age_and_offset_coordinates(age):
    reg = registry(compact=False)
    level, block_id, meta, raw = roundtrip("minecraft:kelp", {"age": str(age)}, reg)
    assert (block_id, meta) == (reg.resolve("etfuturum:kelp"), 0)
    assert len(level["TileEntities"]) == 4096
    tile = level["TileEntities"][0]
    assert (tile["id"], tile["Age"], tile["x"], tile["y"], tile["z"]) == ("etfuturum:modern_parity_kelp_state", age, 0, 64, 0)
    assert (level["TileEntities"][-1]["x"], level["TileEntities"][-1]["y"], level["TileEntities"][-1]["z"]) == (15, 79, 15)
    # Inspect the serialized tag type, not just a parser's integer coercion.
    assert engine.tag(1, "Age", engine.p_byte(age)) in raw
    assert engine.tag(3, "Age", engine.p_int(age)) not in raw


@pytest.mark.parametrize("half,meta", [("lower", 0), ("upper", 1)])
def test_tall_seagrass_halves(half, meta):
    _, block_id, actual, _ = roundtrip("minecraft:tall_seagrass", {"half": half}, registry())
    assert actual == meta


@pytest.mark.parametrize("count", range(1, 5))
@pytest.mark.parametrize("wet", [False, True])
def test_sea_pickle_count_wet_dry(count, wet):
    _, _, actual, _ = roundtrip("minecraft:sea_pickle", {"pickles": str(count), "waterlogged": str(wet).lower()}, registry())
    assert actual == (count - 1) | (4 if wet else 0)


@pytest.mark.parametrize("family,dead,form,wet", list(itertools.product(["tube", "brain", "bubble", "fire", "horn"], ["", "dead_"], ["coral", "coral_fan", "coral_wall_fan"], [False, True])))
def test_coral_shapes_facings_wet_dry(family, dead, form, wet):
    name = "minecraft:" + dead + family + "_" + form
    for facing in ["north", "south", "west", "east"] if form == "coral_wall_fan" else [None]:
        props = {"waterlogged": str(wet).lower()}
        if facing:
            props["facing"] = facing
        _, _, meta, _ = roundtrip(name, props, registry())
        assert meta == ((FACING[facing] if facing else 0) | (8 if wet else 0))
        assert engine.map_modern(name, props, registry(), True).quality == "backport_exact"


def test_narrow_powder_snow_alias_and_vanilla_fallback():
    reg = registry()
    assert engine.map_modern("minecraft:poweder_snow", {}, reg) == engine.map_modern("minecraft:powder_snow", {}, reg)
    _, block_id, meta, _ = roundtrip("minecraft:poweder_snow", {}, reg)
    assert (block_id, meta) == (reg.resolve("etfuturum:powder_snow"), 0)
    assert engine.map_modern("other:poweder_snow", {}, reg).target != "etfuturum:powder_snow"
    assert engine.map_modern("minecraft:poweder_snow", {}, engine.TargetRegistry({"minecraft:air": 0, "minecraft:snow": 80})).target == "minecraft:snow"


def test_aquatic_fallback_and_stripping_no_orphan_tiles():
    reg = engine.TargetRegistry({"minecraft:air": 0, "minecraft:stone": 1, "minecraft:water": 9})
    for name in ["sea_pickle", "tube_coral", "dead_brain_coral_fan", "fire_coral_wall_fan"]:
        for wet in [True, False]:
            m = engine.map_modern("minecraft:" + name, {"waterlogged": str(wet).lower()}, reg)
            assert m.target == ("minecraft:water" if wet else "minecraft:air")
            assert m.quality == "omitted"
    level, block_id, meta, _ = roundtrip("minecraft:kelp", {"age": "25"}, registry(False), strip=80)
    assert not level["TileEntities"]
    assert block_id == registry(False).resolve("minecraft:stone")


def test_anvil_save_reload_roundtrip(tmp_path):
    reg = registry(False)
    _, _, _, raw = roundtrip("minecraft:kelp", {"age": "25"}, reg)
    path = tmp_path / "r.0.0.mca"
    engine.write_region(path, {0: raw})
    assert engine.verify_written_region(path, [0]) == 1
    reloaded = dict(engine.RegionReader(path).chunks())[0]
    assert engine.parse_nbt(reloaded)[1] == engine.parse_nbt(raw)[1]


def test_unsupported_waterlogged_vanilla_and_provider_never_exact():
    reg = registry()
    for name in ["stone_slab", "amethyst_cluster", "pointed_dripstone"]:
        mapped = engine.map_modern("minecraft:" + name, {"waterlogged": "true"}, reg)
        assert mapped.quality not in {"exact", "backport_exact"}


def test_negative_chunk_coordinates_negative_source_y_and_offset():
    reg = registry(False)
    raw = _modern_chunk([_section(-4, [("minecraft:kelp", {"age": "17"})])])
    raw = raw.replace(engine.tag(3, "xPos", engine.p_int(0)), engine.tag(3, "xPos", engine.p_int(-3)))
    raw = raw.replace(engine.tag(3, "zPos", engine.p_int(0)), engine.tag(3, "zPos", engine.p_int(2)))
    _, legacy = engine.convert_chunk(raw, reg, True, 64, 0, _stats())
    level = engine.parse_nbt(legacy)[1]["Level"]
    assert (level["xPos"], level["zPos"], level["Sections"][0]["Y"]) == (-3, 2, 0)
    assert (level["TileEntities"][0]["x"], level["TileEntities"][0]["y"], level["TileEntities"][0]["z"]) == (-48, 0, 32)
    assert (level["TileEntities"][-1]["x"], level["TileEntities"][-1]["y"], level["TileEntities"][-1]["z"]) == (-33, 15, 47)
    assert all(te["Age"] == 17 for te in level["TileEntities"])


def test_cropped_sections_have_no_orphan_age_tiles():
    stats = _stats()
    raw = _modern_chunk([_section(-4, [("minecraft:kelp", {"age": "25"})])])
    _, legacy = engine.convert_chunk(raw, registry(False), True, 0, 0, stats)
    level = engine.parse_nbt(legacy)[1]["Level"]
    assert not level["TileEntities"] and not level["Sections"]
    assert stats["chunks_cropped_below_0"] == 1


def test_compact_kelp_does_not_enter_per_head_tile_synthesis(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("compact kelp still iterates tile synthesis")
    monkeypatch.setattr(engine, "_etfuturum_state_tile_entity", forbidden)
    roundtrip("minecraft:kelp", {"age": "15"}, registry())
