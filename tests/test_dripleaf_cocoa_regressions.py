"""Regression tests for vanilla cocoa and the EFR dripleaf water state layout."""
import pytest
from wgmap_backporter_studio.core import legacy1710_engine as engine
from test_p018_legacy_lighting_and_ladders import _section, _modern_chunk, _stats, _metadata_at


def reg(new=True):
    names={"minecraft:air":0,"minecraft:stone":1,"minecraft:cocoa":127,
           "etfuturum:small_dripleaf":3100,"etfuturum:big_dripleaf":3101,
           "etfuturum:big_dripleaf_stem":3102}
    if new: names["etfuturum:big_dripleaf_wet"]=3103
    return engine.TargetRegistry(names)


def read_block(name, props, registry):
    raw=_modern_chunk([_section(0,[(name,props)])])
    _,result=engine.convert_chunk(raw,registry,True,64,0,_stats())
    engine.validate_legacy_chunk_nbt(result)
    _,parsed=engine.parse_nbt(result)
    section=next(s for s in parsed["Level"]["Sections"] if s["Y"]==4)
    ids=engine.np.frombuffer(section["Blocks"],dtype=engine.np.uint8).astype(engine.np.uint16)
    if "Add" in section: ids |= engine.unpack_nibbles(section["Add"]).astype(engine.np.uint16)<<8
    return int(ids[0]),_metadata_at(section)


@pytest.mark.parametrize("direction,dir_meta", [("north",2),("east",3),("south",0),("west",1)])
@pytest.mark.parametrize("age", [0,1,2])
def test_cocoa_serialized_not_stone(direction,dir_meta,age):
    props={"facing":direction,"age":str(age)}
    m=engine.map_modern("minecraft:cocoa",props,reg())
    assert (m.target,m.meta,m.quality)==("minecraft:cocoa",(age<<2)|dir_meta,"exact")
    assert read_block("minecraft:cocoa",props,reg())==(127,(age<<2)|dir_meta)


@pytest.mark.parametrize("half,b",[("lower",0),("upper",4)])
@pytest.mark.parametrize("facing,f",[("north",0),("east",1),("south",2),("west",3)])
@pytest.mark.parametrize("wet",[False,True])
def test_small_dripleaf_upper_leaves_preserved(half,b,facing,f,wet):
    props={"half":half,"facing":facing,"waterlogged":str(wet).lower()}
    m=engine.map_modern("minecraft:small_dripleaf",props,reg())
    assert (m.target,m.meta,m.quality)==("etfuturum:small_dripleaf",f|b|(8 if wet else 0),"backport_exact")
    assert read_block("minecraft:small_dripleaf",props,reg())==(3100,m.meta)


@pytest.mark.parametrize("facing,f",[("north",0),("east",1),("south",2),("west",3)])
@pytest.mark.parametrize("tilt,t",[("none",0),("unstable",1),("partial",2),("full",3)])
@pytest.mark.parametrize("wet",[False,True])
def test_big_dripleaf_top_full_tilt_facing(facing,f,tilt,t,wet):
    props={"facing":facing,"tilt":tilt,"waterlogged":str(wet).lower()}
    m=engine.map_modern("minecraft:big_dripleaf",props,reg())
    assert (m.target,m.meta,m.quality)==("etfuturum:big_dripleaf_wet" if wet else "etfuturum:big_dripleaf",f|(t<<2),"backport_exact")
    assert read_block("minecraft:big_dripleaf",props,reg())==(3103 if wet else 3101,m.meta)


@pytest.mark.parametrize("wet",[False,True])
def test_big_dripleaf_stem_waterlogged_metadata(wet):
    props={"facing":"west","waterlogged":str(wet).lower()}
    m=engine.map_modern("minecraft:big_dripleaf_stem",props,reg())
    assert (m.target,m.meta,m.quality)==("etfuturum:big_dripleaf_stem",3|(4 if wet else 0),"backport_exact")
    assert read_block("minecraft:big_dripleaf_stem",props,reg())==(3102,m.meta)


def test_old_efr_registry_waterlogging_is_approximate():
    for kind in ("small_dripleaf","big_dripleaf","big_dripleaf_stem"):
        m=engine.map_modern("minecraft:"+kind,{"waterlogged":"true"},reg(False))
        assert m.quality not in {"exact","backport_exact"}
