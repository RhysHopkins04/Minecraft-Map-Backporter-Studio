import collections
import tempfile
import zipfile
from pathlib import Path

import numpy as np

from wgmap_backporter_studio.core import legacy1710_engine as engine
from wgmap_backporter_studio.ui.theme import APP_STYLESHEET


def _stats():
    return {
        "data_versions": collections.Counter(),
        "block_entities_omitted": 0,
        "block_entity_types_omitted": collections.Counter(),
        "block_entities_synthesized": 0,
        "block_entity_types_synthesized": collections.Counter(),
        "palette_seen": collections.Counter(),
        "mapping_quality": collections.defaultdict(set),
        "mapping_notes": {},
        "chunks_cropped_above_255": 0,
        "chunks_cropped_below_0": 0,
        "lighting_source_seed_sections": 0,
        "lighting_fallback_sections": 0,
        "lighting_empty_sections_omitted": 0,
        "lighting_emitted_sections": 0,
    }


def _palette_entry(name, props=None):
    tags = [engine.tag(8, "Name", engine.p_string(name))]
    if props:
        tags.append(engine.tag(10, "Properties", engine.p_compound([
            engine.tag(8, str(k), engine.p_string(str(v))) for k, v in props.items()
        ])))
    return engine.p_compound(tags)


def _level_wrapped_117_chunk():
    section = engine.p_compound([
        engine.tag(1, "Y", engine.p_byte(0)),
        engine.tag(9, "Palette", engine.p_list(10, [_palette_entry("minecraft:stone")])),
        engine.tag(7, "SkyLight", engine.p_byte_array(engine.pack_nibbles(np.full(4096, 15, dtype=np.uint8)))),
        engine.tag(7, "BlockLight", engine.p_byte_array(bytes(2048))),
    ])
    level = engine.p_compound([
        engine.tag(3, "xPos", engine.p_int(3)),
        engine.tag(3, "zPos", engine.p_int(-2)),
        engine.tag(4, "LastUpdate", engine.p_long(7)),
        engine.tag(1, "isLightOn", engine.p_byte(1)),
        engine.tag(11, "Biomes", engine.p_int_array([1] * 1024)),
        engine.tag(9, "Sections", engine.p_list(10, [section])),
        engine.tag(9, "TileEntities", engine.p_list(10, [])),
    ])
    return bytes([10]) + engine.nbt_name("") + engine.p_compound([
        engine.tag(10, "Level", level),
        engine.tag(3, "DataVersion", engine.p_int(2730)),
    ])


def _numeric_112_chunk():
    blocks = bytearray(4096)
    blocks[0] = 1      # stone: already exists in 1.7.10 and should copy exactly
    blocks[1] = 165    # slime block: 1.8+ block, routed through EFR when available
    data = bytes(2048)
    section = engine.p_compound([
        engine.tag(1, "Y", engine.p_byte(0)),
        engine.tag(7, "Blocks", engine.p_byte_array(bytes(blocks))),
        engine.tag(7, "Data", engine.p_byte_array(data)),
        engine.tag(7, "SkyLight", engine.p_byte_array(engine.pack_nibbles(np.full(4096, 15, dtype=np.uint8)))),
        engine.tag(7, "BlockLight", engine.p_byte_array(bytes(2048))),
    ])
    level = engine.p_compound([
        engine.tag(3, "xPos", engine.p_int(0)),
        engine.tag(3, "zPos", engine.p_int(0)),
        engine.tag(4, "LastUpdate", engine.p_long(0)),
        engine.tag(7, "Biomes", engine.p_byte_array(bytes([1] * 256))),
        engine.tag(9, "Sections", engine.p_list(10, [section])),
        engine.tag(9, "TileEntities", engine.p_list(10, [])),
    ])
    return bytes([10]) + engine.nbt_name("") + engine.p_compound([
        engine.tag(10, "Level", level),
        engine.tag(3, "DataVersion", engine.p_int(1343)),
    ])


def test_p019_reads_117_level_wrapped_palette_chunks_and_converts_them():
    raw = _level_wrapped_117_chunk()
    parsed = engine.parse_modern_chunk(raw)
    assert parsed["xPos"] == 3 and parsed["zPos"] == -2
    assert parsed["DataVersion"] == 2730
    assert parsed["source_layout"] == "level_wrapped"
    assert parsed["source_section_format"] == "level_palette"
    assert parsed["sections"][0]["palette"] == [("minecraft:stone", {})]

    reg = engine.TargetRegistry({"minecraft:air": 0, "minecraft:stone": 1})
    (cx, cz), legacy = engine.convert_chunk(raw, reg, True, 0, 0, _stats())
    assert (cx, cz) == (3, -2)
    _, out = engine.parse_nbt(legacy)
    assert out["Level"]["xPos"] == 3
    assert out["Level"]["zPos"] == -2
    assert out["Level"]["Sections"][0]["Blocks"][0] == 1


def test_p019_reads_18_to_112_numeric_sections_and_preserves_or_backports_states():
    raw = _numeric_112_chunk()
    parsed = engine.parse_modern_chunk(raw)
    assert parsed["source_section_format"] == "numeric_preflattening"
    assert parsed["xPos"] == 0 and parsed["zPos"] == 0

    reg = engine.TargetRegistry({
        "minecraft:air": 0,
        "minecraft:stone": 1,
        "etfuturum:slime": 300,
    })
    (_, _), legacy = engine.convert_chunk(raw, reg, True, 0, 0, _stats())
    _, out = engine.parse_nbt(legacy)
    section = out["Level"]["Sections"][0]
    blocks = np.frombuffer(section["Blocks"], dtype=np.uint8).astype(np.uint16)
    if "Add" in section:
        blocks |= engine.unpack_nibbles(section["Add"]).astype(np.uint16) << 8
    assert int(blocks[0]) == 1
    assert int(blocks[1]) == 300


def _pack_continuous_palette_indices(values, bits):
    values = [int(v) for v in values]
    total_bits = len(values) * bits
    longs = [0] * ((total_bits + 63) // 64)
    mask = (1 << bits) - 1
    for i, value in enumerate(values):
        bit = i * bits
        li = bit >> 6
        off = bit & 63
        value &= mask
        longs[li] |= (value << off) & 0xFFFFFFFFFFFFFFFF
        spill = off + bits - 64
        if spill > 0:
            longs[li + 1] |= value >> (bits - spill)
    return [v if v < (1 << 63) else v - (1 << 64) for v in longs]


def test_p019_113_to_115_continuous_palette_long_packing():
    palette_size = 17  # forces five bits and boundary-spanning entries
    expected = np.arange(4096, dtype=np.int32) % palette_size
    packed = _pack_continuous_palette_indices(expected, 5)
    got = engine.unpack_palette_indices(packed, palette_size, 4096, 5, padded=False)
    assert np.array_equal(got, expected)


def test_p019_zip_source_uses_terrain_region_not_entities_or_poi_with_same_name():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        source = td / "world.zip"
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("World/region/r.0.0.mca", b"terrain")
            z.writestr("World/entities/r.0.0.mca", b"entities")
            z.writestr("World/poi/r.0.0.mca", b"poi")
        out = engine.discover_source_regions(source, td / "scratch")
        assert (out / "r.0.0.mca").read_bytes() == b"terrain"


def test_p019_message_boxes_have_explicit_dark_background_and_contrasting_text():
    assert "QDialog, QMessageBox { background: #171d25; color: #e7edf5; }" in APP_STYLESHEET
    assert "QMessageBox QLabel { background: transparent; color: #e7edf5; }" in APP_STYLESHEET
