import collections

import pytest

from wgmap_backporter_studio.core import legacy1710_engine as engine
from test_p018_legacy_lighting_and_ladders import _modern_chunk, _section, _stats


def _registry(with_efr=True):
    ids={
        "minecraft:air":0,
        "minecraft:stone":1,
        "minecraft:grass":2,
        "minecraft:dirt":3,
        "minecraft:cobblestone":4,
        "minecraft:planks":5,
        "minecraft:bedrock":7,
        "minecraft:water":9,
    }
    if with_efr:
        ids["etfuturum:powder_snow"]=2700
    return engine.TargetRegistry(ids)


def _extended_stats():
    stats=_stats()
    stats["chunks_cropped_above_target_y"]=0
    return stats


def test_efr_extended_height_preserves_modern_minus64_through_319():
    raw=_modern_chunk([
        _section(-4, [("minecraft:stone", {})]),
        _section(19, [("minecraft:stone", {})]),
    ])
    stats=_extended_stats()
    (_, _), legacy=engine.convert_chunk(
        raw,_registry(),True,64,0,stats,target_max_y_value=383
    )
    _, parsed=engine.parse_nbt(legacy)
    level=parsed["Level"]
    assert [section["Y"] for section in level["Sections"]] == [0,23]
    assert max(level["HeightMap"]) == 384
    assert stats["chunks_cropped_above_target_y"] == 0
    assert stats["chunks_cropped_below_0"] == 0
    engine.validate_legacy_chunk_nbt(legacy,0,0,383)


def test_extended_height_crops_only_above_physical_383():
    raw=_modern_chunk([
        _section(19, [("minecraft:stone", {})]),
        _section(20, [("minecraft:stone", {})]),
    ])
    stats=_extended_stats()
    (_, _), legacy=engine.convert_chunk(
        raw,_registry(),True,64,0,stats,target_max_y_value=383
    )
    level=engine.parse_nbt(legacy)[1]["Level"]
    assert [section["Y"] for section in level["Sections"]] == [23]
    assert stats["chunks_cropped_above_target_y"] == 1
    # The compatibility field must not imply that representable Y256..383 was cropped.
    assert stats["chunks_cropped_above_255"] == 0


def test_legacy_height_profile_still_crops_shifted_sections_above_255():
    raw=_modern_chunk([_section(19, [("minecraft:stone", {})])])
    stats=_extended_stats()
    (_, _), legacy=engine.convert_chunk(
        raw,_registry(),True,64,0,stats,target_max_y_value=255
    )
    level=engine.parse_nbt(legacy)[1]["Level"]
    assert level["Sections"] == []
    assert stats["chunks_cropped_above_target_y"] == 1
    assert stats["chunks_cropped_above_255"] == 1


def test_extended_height_mode_requires_plus_registry_and_fixed_offset():
    with pytest.raises(engine.ConversionError, match="fixed \\+64"):
        engine._validate_height_mode(_registry(),0,0,True,lambda _s:None)
    with pytest.raises(engine.ConversionError, match="no etfuturum blocks"):
        engine._validate_height_mode(_registry(False),64,0,True,lambda _s:None)
    assert engine._validate_height_mode(_registry(),64,0,True,lambda _s:None) == 383
    with pytest.raises(engine.ConversionError, match="between 0 and 383"):
        engine._validate_height_mode(_registry(),64,384,True,lambda _s:None)


def test_extended_roundtrip_region_accepts_section_23(tmp_path):
    raw=_modern_chunk([_section(19, [("minecraft:stone", {})])])
    (_, _), legacy=engine.convert_chunk(
        raw,_registry(),True,64,0,_extended_stats(),target_max_y_value=383
    )
    path=tmp_path/"r.0.0.mca"
    engine.write_region(path,{0:legacy})
    assert engine.verify_written_region(path,[0],target_max_y_value=383) == 1
    with pytest.raises(engine.ConversionError, match="HeightMap|invalid/duplicate section"):
        engine.verify_written_region(path,[0],target_max_y_value=255)


def _template_world(path):
    import gzip
    path.mkdir()
    ids={name:i for i,name in enumerate(engine.TARGET_REGISTRY_SENTINELS)}
    ids.update({"minecraft:air":0,"minecraft:stone":1,"etfuturum:powder_snow":2700})
    rows=[engine.p_compound([
        engine.tag(8,"K",engine.p_string("\x01"+name)),
        engine.tag(3,"V",engine.p_int(value)),
    ]) for name,value in ids.items()]
    root=bytes([10])+engine.nbt_name("")+engine.p_compound([
        engine.tag(10,"FML",engine.p_compound([
            engine.tag(9,"ItemData",engine.p_list(10,rows)),
        ])),
    ])
    (path/"level.dat").write_bytes(gzip.compress(root))
    return path


def test_end_to_end_extended_preflight_and_conversion(tmp_path):
    source=tmp_path/"source"; source.mkdir()
    raw=_modern_chunk([
        _section(-4, [("minecraft:stone", {})]),
        _section(19, [("minecraft:stone", {})]),
    ])
    engine.write_region(source/"r.0.0.mca",{0:raw})
    template=_template_world(tmp_path/"template")

    ready=engine.run_conversion_preflight(
        source,template,y_offset=64,efr_extended_height=True,workers=1,log=lambda _s:None
    )
    p=ready["preflight"]
    assert ready["settings"]["target_max_y"] == 383
    assert p["target_section_count"] == 24
    assert p["potential_chunks_cropped_above_target_y"] == 0
    assert p["potential_chunks_cropped_below_0"] == 0

    out=tmp_path/"out"
    report=engine.run_conversion(
        source,template,out,y_offset=64,efr_extended_height=True,
        verified_preflight=ready,workers=1,log=lambda _s:None,
    )
    assert report["output_promoted"]
    assert report["preflight_reused"]
    assert report["settings"]["target_max_y"] == 383
    assert report["chunks_cropped_above_target_y"] == 0
    converted=dict(engine.RegionReader(out/"region"/"r.0.0.mca").chunks())[0]
    level=engine.parse_nbt(converted)[1]["Level"]
    assert [section["Y"] for section in level["Sections"]] == [0,23]
    assert max(level["HeightMap"]) == 384
