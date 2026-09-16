import collections

import numpy as np

from wgmap_backporter_studio.core import legacy1710_engine as engine


def _props_payload(props):
    return engine.p_compound([
        engine.tag(8, str(k), engine.p_string(str(v))) for k, v in props.items()
    ])


def _palette_entry(name, props=None):
    tags = [engine.tag(8, "Name", engine.p_string(name))]
    if props:
        tags.append(engine.tag(10, "Properties", _props_payload(props)))
    return engine.p_compound(tags)


def _section(y, palette, sky=None, block=None):
    block_states = engine.p_compound([
        engine.tag(9, "palette", engine.p_list(10, [
            _palette_entry(name, props) for name, props in palette
        ])),
    ])
    tags = [
        engine.tag(1, "Y", engine.p_byte(y)),
        engine.tag(10, "block_states", block_states),
    ]
    if sky is not None:
        tags.append(engine.tag(7, "SkyLight", engine.p_byte_array(sky)))
    if block is not None:
        tags.append(engine.tag(7, "BlockLight", engine.p_byte_array(block)))
    return engine.p_compound(tags)


def _modern_chunk(sections, light_on=True):
    tags = [
        engine.tag(3, "DataVersion", engine.p_int(4440)),
        engine.tag(3, "xPos", engine.p_int(0)),
        engine.tag(3, "zPos", engine.p_int(0)),
        engine.tag(4, "LastUpdate", engine.p_long(0)),
        engine.tag(1, "isLightOn", engine.p_byte(1 if light_on else 0)),
        engine.tag(9, "sections", engine.p_list(10, sections)),
        engine.tag(9, "block_entities", engine.p_list(10, [])),
    ]
    return bytes([10]) + engine.nbt_name("") + engine.p_compound(tags)


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


def _registry():
    return engine.TargetRegistry({
        "minecraft:air": 0,
        "minecraft:stone": 1,
        "minecraft:grass": 2,
        "minecraft:dirt": 3,
        "minecraft:cobblestone": 4,
        "minecraft:planks": 5,
        "minecraft:bedrock": 7,
        "minecraft:water": 9,
        "minecraft:ladder": 65,
    })


def _legacy_section(level, y):
    return next(section for section in level["Sections"] if section["Y"] == y)


def _metadata_at(section, flat_index=0):
    return int(engine.unpack_nibbles(section["Data"])[flat_index])


def _light_array(fill=0):
    return engine.pack_nibbles(np.full(4096, fill, dtype=np.uint8))


def test_p018_ladder_all_four_facings_use_legacy_wall_metadata():
    expected = {"north": 2, "south": 3, "west": 4, "east": 5}
    reg = _registry()
    for facing, meta in expected.items():
        mapped = engine.map_modern("minecraft:ladder", {"facing": facing}, reg, True)
        assert mapped.target == "minecraft:ladder"
        assert mapped.meta == meta


def test_p018_ladder_facing_survives_plus_64_y_offset():
    reg = _registry()
    for facing, expected_meta in {"north": 2, "south": 3, "west": 4, "east": 5}.items():
        raw = _modern_chunk([
            _section(0, [("minecraft:ladder", {"facing": facing})], _light_array(15), _light_array(0))
        ])
        (_, _), legacy = engine.convert_chunk(raw, reg, True, 64, 0, _stats())
        _, parsed = engine.parse_nbt(legacy)
        level = parsed["Level"]
        section = _legacy_section(level, 4)
        assert _metadata_at(section) == expected_meta
        assert level["LightPopulated"] == 0
        assert level["TerrainPopulated"] == 1


def test_p018_valid_source_light_is_shifted_not_zeroed_and_heightmap_tracks_roof():
    reg = _registry()

    # Source section 0 is an enclosed interior. Section 1 contains a roof at
    # source Y=16: skylight is 0 on local Y=0 and 15 above it. After +64 the
    # roof is at target Y=80, so the legacy HeightMap boundary must be 81.
    sky0_vals = np.zeros(4096, dtype=np.uint8)
    sky1_vals = np.full(4096, 15, dtype=np.uint8)
    sky1_vals.reshape(16, 16, 16)[0, :, :] = 0
    block0_vals = np.zeros(4096, dtype=np.uint8)
    block0_vals.reshape(16, 16, 16)[15, 7, 7] = 12
    block1_vals = np.zeros(4096, dtype=np.uint8)
    block1_vals.reshape(16, 16, 16)[0, 7, 7] = 11

    sky0 = engine.pack_nibbles(sky0_vals)
    sky1 = engine.pack_nibbles(sky1_vals)
    block0 = engine.pack_nibbles(block0_vals)
    block1 = engine.pack_nibbles(block1_vals)

    stats = _stats()
    raw = _modern_chunk([
        _section(0, [("minecraft:stone", {})], sky0, block0),
        _section(1, [("minecraft:stone", {})], sky1, block1),
    ])
    (_, _), legacy = engine.convert_chunk(raw, reg, True, 64, 0, stats)
    _, parsed = engine.parse_nbt(legacy)
    level = parsed["Level"]

    # Existing multiple sections become newly-created target sections 4 and 5.
    sec4 = _legacy_section(level, 4)
    sec5 = _legacy_section(level, 5)
    assert sec4["SkyLight"] == sky0
    assert sec5["SkyLight"] == sky1
    assert sec4["BlockLight"] == block0
    assert sec5["BlockLight"] == block1

    # The explicit values straddle a source section boundary and remain adjacent
    # after translation, demonstrating that section relocation does not scramble
    # nibble order or light data.
    assert engine.unpack_nibbles(sec4["BlockLight"])[15 * 256 + 7 * 16 + 7] == 12
    assert engine.unpack_nibbles(sec5["BlockLight"])[0 * 256 + 7 * 16 + 7] == 11
    assert set(level["HeightMap"]) == {81}
    assert stats["lighting_source_seed_sections"] == 2
    assert stats["lighting_fallback_sections"] == 0


def test_p018_open_sky_and_enclosed_columns_derive_independent_height_boundaries():
    # Column (0,0) remains open sky (15 throughout) and therefore keeps its
    # fallback height 0. Column (1,0) has a roof at target Y=95 and derives 96.
    vals = np.full(4096, 15, dtype=np.uint8).reshape(16, 16, 16)
    vals[15, 0, 1] = 0
    packed = engine.pack_nibbles(vals.reshape(-1))
    fallback = np.zeros((16, 16), dtype=np.int32)
    result = engine.derive_heightmap_from_skylight({5: packed}, fallback)
    assert result[0, 0] == 0
    assert result[0, 1] == 96


def test_p018_untrusted_or_malformed_source_light_is_not_preserved():
    reg = _registry()
    valid_but_untrusted = _light_array(7)
    malformed = b"not-a-2048-byte-light-array"
    stats = _stats()
    raw = _modern_chunk([
        _section(0, [("minecraft:stone", {})], malformed, valid_but_untrusted),
    ], light_on=False)
    (_, _), legacy = engine.convert_chunk(raw, reg, True, 64, 0, stats)
    _, parsed = engine.parse_nbt(legacy)
    level = parsed["Level"]
    sec4 = _legacy_section(level, 4)

    # Unlit source chunks are deliberately reset to the conservative bootstrap;
    # malformed or stale modern arrays are never copied just because they exist.
    assert sec4["BlockLight"] == bytes(2048)
    assert sec4["SkyLight"] != malformed
    assert len(sec4["SkyLight"]) == 2048
    assert stats["lighting_source_seed_sections"] == 0
    assert stats["lighting_fallback_sections"] == 1


def test_p018_strip_below_invalidates_source_light_for_the_whole_chunk():
    reg = _registry()
    stats = _stats()
    raw = _modern_chunk([
        _section(0, [("minecraft:stone", {})], _light_array(8), _light_array(5)),
        _section(1, [("minecraft:stone", {})], _light_array(9), _light_array(6)),
    ])
    (_, _), legacy = engine.convert_chunk(raw, reg, True, 64, 70, stats)
    _, parsed = engine.parse_nbt(legacy)
    sec4 = _legacy_section(parsed["Level"], 4)
    sec5 = _legacy_section(parsed["Level"], 5)

    assert stats["lighting_source_seed_sections"] == 0
    assert stats["lighting_fallback_sections"] == 2
    assert sec4["BlockLight"] == bytes(2048)
    assert sec5["BlockLight"] == bytes(2048)
    assert sec4["SkyLight"] != _light_array(8)
    assert sec5["SkyLight"] != _light_array(9)


def test_p018_world_edge_crop_invalidates_source_light_seed_for_retained_sections():
    reg = _registry()
    stats = _stats()
    raw = _modern_chunk([
        # This retained section would normally preserve both arrays.
        _section(10, [("minecraft:stone", {})], _light_array(7), _light_array(6)),
        # +64 moves this non-air section above Y=255, changing the chunk geometry.
        _section(12, [("minecraft:stone", {})], _light_array(4), _light_array(3)),
    ])
    (_, _), legacy = engine.convert_chunk(raw, reg, True, 64, 0, stats)
    _, parsed = engine.parse_nbt(legacy)
    sec14 = _legacy_section(parsed["Level"], 14)

    assert stats["chunks_cropped_above_255"] == 1
    assert stats["lighting_source_seed_sections"] == 0
    assert stats["lighting_fallback_sections"] == 1
    assert sec14["BlockLight"] == bytes(2048)
    assert sec14["SkyLight"] != _light_array(7)


def test_p018_legacy_writer_rejects_population_or_lighting_contract_breakage():
    section = engine.make_section_nbt(
        0,
        np.zeros(4096, dtype=np.uint16),
        np.zeros(4096, dtype=np.uint8),
        _light_array(15),
        _light_array(0),
    )
    raw = engine.make_chunk_nbt(0, 0, 0, [section], [0] * 256, [1] * 256)
    info = engine.validate_legacy_chunk_nbt(raw, 0, 0)
    assert info["sections"] == 1

    _, parsed = engine.parse_nbt(raw)
    assert parsed["Level"]["TerrainPopulated"] == 1
    assert parsed["Level"]["LightPopulated"] == 0
    assert len(parsed["Level"]["Sections"][0]["SkyLight"]) == 2048
    assert len(parsed["Level"]["Sections"][0]["BlockLight"]) == 2048


def test_p018b_all_air_sections_are_not_materialized_as_legacy_storage():
    reg = _registry()
    stats = _stats()
    raw = _modern_chunk([
        _section(0, [("minecraft:air", {})], _light_array(15), _light_array(0)),
        _section(1, [("minecraft:stone", {})], _light_array(7), _light_array(0)),
        _section(2, [("minecraft:air", {})], None, None),
    ])
    (_, _), legacy = engine.convert_chunk(raw, reg, True, 64, 0, stats)
    _, parsed = engine.parse_nbt(legacy)
    level = parsed["Level"]

    # Only the target section containing actual blocks is serialized. This is
    # the native 1.7.10 representation of empty vertical space and prevents
    # phantom all-air ExtendedBlockStorage objects from raising topFilledSegment.
    assert [section["Y"] for section in level["Sections"]] == [5]
    assert stats["lighting_empty_sections_omitted"] == 2
    assert stats["lighting_emitted_sections"] == 1
    assert stats["lighting_source_seed_sections"] == 1
    assert stats["lighting_fallback_sections"] == 0
    assert set(level["HeightMap"]) == {96}


def test_p018b_all_air_section_with_light_arrays_is_still_sparse():
    reg = _registry()
    stats = _stats()
    sky_vals = np.full(4096, 12, dtype=np.uint8)
    block_vals = np.full(4096, 3, dtype=np.uint8)
    raw = _modern_chunk([
        _section(0, [("minecraft:stone", {})], _light_array(0), _light_array(0)),
        _section(1, [("minecraft:air", {})], engine.pack_nibbles(sky_vals), engine.pack_nibbles(block_vals)),
    ])
    (_, _), legacy = engine.convert_chunk(raw, reg, True, 64, 0, stats)
    _, parsed = engine.parse_nbt(legacy)

    # Air-only source light is not authoritative for the target runtime because
    # there is no block storage to preserve. Let 1.7.10 infer sky from HeightMap
    # and rebuild block light while LightPopulated remains false.
    assert [section["Y"] for section in parsed["Level"]["Sections"]] == [4]
    assert stats["lighting_empty_sections_omitted"] == 1
    assert stats["lighting_emitted_sections"] == 1
    assert parsed["Level"]["LightPopulated"] == 0


def test_p018b_sparse_sections_keep_non_empty_source_light_and_offset():
    reg = _registry()
    stats = _stats()
    sky = _light_array(9)
    block = _light_array(4)
    raw = _modern_chunk([
        _section(-1, [("minecraft:air", {})], None, None),
        _section(0, [("minecraft:stone", {})], sky, block),
        _section(1, [("minecraft:air", {})], None, None),
    ])
    (_, _), legacy = engine.convert_chunk(raw, reg, True, 64, 0, stats)
    _, parsed = engine.parse_nbt(legacy)
    section = parsed["Level"]["Sections"][0]

    assert section["Y"] == 4
    assert section["SkyLight"] == sky
    assert section["BlockLight"] == block
    assert stats["lighting_empty_sections_omitted"] == 2
    assert stats["lighting_emitted_sections"] == 1
