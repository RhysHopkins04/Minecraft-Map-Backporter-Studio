"""Real spawned-process parity, output safety, and Qt timing regressions."""
import gzip
import os
from pathlib import Path

import pytest

from wgmap_backporter_studio.core import legacy1710_engine as e


def compound(tags):
    return e.p_compound(tags)


def chunk(cx, kind="minecraft:stone"):
    palette=compound([e.tag(8,"Name",e.p_string(kind))])
    section=compound([
        e.tag(1,"Y",e.p_byte(0)),
        e.tag(9,"Palette",e.p_list(10,[palette])),
    ])
    pig=compound([e.tag(8,"id",e.p_string("Pig"))])
    level=compound([
        e.tag(3,"xPos",e.p_int(cx)), e.tag(3,"zPos",e.p_int(0)),
        e.tag(9,"Sections",e.p_list(10,[section])),
        e.tag(9,"Entities",e.p_list(10,[pig])),
    ])
    return bytes([10])+e.nbt_name("")+compound([
        e.tag(3,"DataVersion",e.p_int(2730)), e.tag(10,"Level",level),
    ])


def worlds(tmp_path):
    source=tmp_path/"source"; source.mkdir()
    for i in range(5):
        e.write_region(source/("r.%d.0.mca"%i),{0:chunk(i*32)})
    template=tmp_path/"template"; template.mkdir()
    ids={name:i for i,name in enumerate(e.TARGET_REGISTRY_SENTINELS)}
    ids.update({"minecraft:air":0,"minecraft:stone":1})
    rows=[compound([e.tag(8,"K",e.p_string("\x01"+name)),e.tag(3,"V",e.p_int(value))]) for name,value in ids.items()]
    raw=bytes([10])+e.nbt_name("")+compound([
        e.tag(10,"FML",compound([e.tag(9,"ItemData",e.p_list(10,rows))]))
    ])
    (template/"level.dat").write_bytes(gzip.compress(raw))
    return source,template


def test_spawned_preflight_and_conversion_match_serial(tmp_path,monkeypatch):
    source,template=worlds(tmp_path)
    serial=e.run_conversion_preflight(source,template,workers=1,log=lambda s:None)
    parallel=e.run_conversion_preflight(source,template,workers=2,log=lambda s:None)
    assert parallel == serial
    p=parallel["preflight"]
    assert p["chunks"]==5 and p["unique_palette_states"]==1
    assert p["block_occurrences_total"]==5*4096
    assert p["content_audit"]["entities_total"]==5
    # Reuse collected embedded entities without reading terrain a second time.
    monkeypatch.setattr(e,"discover_source_regions",lambda *a:pytest.fail("terrain reread"))
    audit=e.audit_source_entities(source,tmp_path,terrain_audit=p["terrain_entity_audit"],log=lambda s:None)
    assert audit["entities_total"]==5
    monkeypatch.undo()
    reports=[]
    for workers in (1,2):
        out=tmp_path/("out%d"%workers)
        rep=e.run_conversion(source,template,out,verified_preflight=parallel,workers=workers,log=lambda s:None)
        assert rep["output_promoted"] and rep["preflight_reused"]
        assert rep["chunks_converted"]==rep["chunks_verified"]==5
        reports.append(rep)
    for key in reports[0]:
        if key != "output": assert reports[0][key]==reports[1][key],key
    for region in sorted((tmp_path/"out1"/"region").glob("*.mca")):
        assert list(e.RegionReader(region).chunks())==list(e.RegionReader(tmp_path/"out2"/"region"/region.name).chunks())


def test_parallel_parse_failure_creates_no_output(tmp_path):
    source,template=worlds(tmp_path)
    e.write_region(source/"r.0.0.mca",{0:b"not nbt"})
    output=tmp_path/"output"
    with pytest.raises(e.ConversionError,match="could not safely parse"):
        e.run_conversion(source,template,output,workers=2,log=lambda s:None)
    assert not output.exists()
    assert not list(tmp_path.glob(".*wgmbps-staging*"))


def test_parallel_entity_audit_and_empty_regions(tmp_path):
    source,template=worlds(tmp_path)
    entities=source/"entities"; entities.mkdir()
    pig=compound([e.tag(8,"id",e.p_string("minecraft:pig"))])
    raw=bytes([10])+e.nbt_name("")+compound([e.tag(9,"Entities",e.p_list(10,[pig]))])
    for i in range(3): e.write_region(entities/("r.%d.0.mca"%i),{0:raw})
    a=e.audit_source_entities(source,tmp_path,workers=1,log=lambda s:None)
    b=e.audit_source_entities(source,tmp_path,workers=2,log=lambda s:None)
    assert a==b and a["entities_total"]==3
    e.write_region(source/"r.5.0.mca",{})
    p=e.run_conversion_preflight(source,template,workers=2,log=lambda s:None)
    assert p["regions"]==6 and p["preflight"]["chunks"]==5


def test_worker_bounds():
    assert 1<=e.region_worker_count()<=4
    assert e.region_worker_count(16,2)==2
    for count in (0,17,-1):
        with pytest.raises(e.ConversionError): e.region_worker_count(count)


def test_timing_and_progress_qt(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    from PySide6.QtWidgets import QApplication
    from wgmap_backporter_studio.ui import main_window as ui
    app=QApplication.instance() or QApplication([])
    tab=ui.BackportTab()
    now=[100.0]
    monkeypatch.setattr(ui.time,"monotonic",lambda:now[0])
    tab._set_busy(True)
    tab._log("Preflight using 2 worker process(es)")
    now[0]=110.0
    tab._log("Preflight [5/10] r.4.0.mca")
    assert tab.progress.value()==5 and tab.progress.maximum()==10
    assert "Estimated remaining 00:00:10" in tab.timing_status.text()
    now[0]=115.0; tab._refresh_timing()
    assert "Elapsed 00:00:15" in tab.timing_status.text()
    tab._log("Entity audit using 2 worker process(es)")
    now[0]=120.0; tab._log("Entity audit [1/2] r.0.0.mca")
    assert "Estimated remaining 00:00:05" in tab.timing_status.text()
    tab._set_busy(False)
    assert not tab._timing_timer.isActive()
    now[0]=150.0; tab._refresh_timing()
    assert "Elapsed 00:00:20" in tab.timing_status.text()
    tab.close()


def test_worker_failure_removes_staging(tmp_path,monkeypatch):
    source,template=worlds(tmp_path)
    ready=e.run_conversion_preflight(source,template,workers=1,log=lambda s:None)
    original=e._region_results
    def interrupted(mode,*args,**kwargs):
        if mode=="convert":
            raise RuntimeError("simulated worker exit")
        yield from original(mode,*args,**kwargs)
    monkeypatch.setattr(e,"_region_results",interrupted)
    out=tmp_path/"interrupted"
    with pytest.raises(RuntimeError,match="worker exit"):
        e.run_conversion(source,template,out,workers=2,verified_preflight=ready,log=lambda s:None)
    assert not out.exists()
    assert not list(tmp_path.glob(".*wgmbps-staging*"))


def test_verification_failure_never_promotes(tmp_path,monkeypatch):
    source,template=worlds(tmp_path)
    ready=e.run_conversion_preflight(source,template,workers=1,log=lambda s:None)
    def broken(*args): raise e.ConversionError("simulated verification failure")
    monkeypatch.setattr(e,"verify_written_region",broken)
    out=tmp_path/"unverified"
    report=e.run_conversion(source,template,out,workers=1,verified_preflight=ready,log=lambda s:None)
    assert not report["output_promoted"] and report["chunks_failed"]
    assert not out.exists()
    assert not list(tmp_path.glob(".*wgmbps-staging*"))
    assert Path(report["failure_report_json"]).exists()
