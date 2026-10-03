import io
import multiprocessing
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest

from wgmap_backporter_studio.core import legacy1710_engine as e
from test_p020_parallel_regions import worlds,chunk


def test_token_cancel_and_final_commit_gate():
    token=e.CancellationToken();assert token.cancel()
    with pytest.raises(e.ConversionCancelled):token.commit(lambda:pytest.fail("promoted after cancel"))
    done=e.CancellationToken();assert done.commit(lambda:42)==42
    assert not done.cancel() and not done.is_set()


def test_cancel_before_preflight_creates_nothing(tmp_path):
    source,template=worlds(tmp_path)
    token=e.CancellationToken();token.cancel()
    with pytest.raises(e.ConversionCancelled):
        e.run_conversion_preflight(source,template,cancel=token,log=lambda s:None)


def test_copy_checks_cancel_after_read_before_write():
    token=e.CancellationToken()
    class Source(io.BytesIO):
        def read(self,count=-1):
            data=super().read(count);token.cancel();return data
    dest=io.BytesIO()
    with pytest.raises(e.ConversionCancelled):e._copy_stream(Source(b"data"),dest,token)
    assert dest.getvalue()==b""


def test_cancel_zip_preparation_cleans_tempdir(tmp_path,monkeypatch):
    source,template=worlds(tmp_path)
    archive=tmp_path/"world.zip"
    with zipfile.ZipFile(archive,"w") as z:
        for path in source.glob("*.mca"):z.write(path,"world/region/"+path.name)
    before=archive.read_bytes()
    temp=tmp_path/"temporary";temp.mkdir();monkeypatch.setattr(e.tempfile,"tempdir",str(temp))
    token=e.CancellationToken();copy=e._copy_stream
    def cancelled_copy(fi,fo,cancel=None):
        copy(fi,fo,cancel);token.cancel()
    monkeypatch.setattr(e,"_copy_stream",cancelled_copy)
    with pytest.raises(e.ConversionCancelled):
        e.run_conversion_preflight(archive,template,cancel=token,workers=2,log=lambda s:None)
    assert not list(temp.iterdir()) and archive.read_bytes()==before


def test_cancel_template_clone_removes_partial_stage(tmp_path,monkeypatch):
    source,template=worlds(tmp_path)
    token=e.CancellationToken();copy=e._copy_file
    def cancelled_copy(src,dst,cancel=None):
        copy(src,dst,cancel);token.cancel()
        raise e.ConversionCancelled("cancel during template copy")
    monkeypatch.setattr(e,"_copy_file",cancelled_copy)
    output=tmp_path/"clone"
    with pytest.raises(e.ConversionCancelled):e._prepare_staging_output(template,output,token)
    assert not output.exists() and not list(tmp_path.glob(".*wgmbps-staging*"))


def test_cancel_waiting_for_real_spawned_workers_joins_them(tmp_path):
    source,template=worlds(tmp_path)
    reg=e.load_target_registry(template);token=e.CancellationToken()
    children={p.pid for p in multiprocessing.active_children()}
    timer=threading.Timer(0.02,token.cancel);timer.start()
    started=time.monotonic()
    try:
        with pytest.raises(e.ConversionCancelled):
            list(e._region_results("preflight",sorted(source.glob("*.mca")),reg,True,0,0,None,2,cancel=token))
    finally:timer.cancel();timer.join()
    assert time.monotonic()-started<10
    assert {p.pid for p in multiprocessing.active_children()}==children


@pytest.mark.parametrize("workers",[1,2])
def test_cancel_conversion_after_completed_region_cleans_stage(tmp_path,workers):
    source,template=worlds(tmp_path)
    for i,path in enumerate(sorted(source.glob("*.mca"))):
        e.write_region(path,{j:chunk(i*32+j) for j in range(32)})
    ready=e.run_conversion_preflight(source,template,workers=1,log=lambda s:None)
    token=e.CancellationToken();out=tmp_path/"cancelled"
    children={p.pid for p in multiprocessing.active_children()}
    def log(line):
        if line.startswith("Convert [1/"):token.cancel()
    with pytest.raises(e.ConversionCancelled):
        e.run_conversion(source,template,out,verified_preflight=ready,workers=workers,cancel=token,log=log)
    assert not out.exists() and not list(tmp_path.glob(".*wgmbps-staging*"))
    assert {p.pid for p in multiprocessing.active_children()}==children
    assert not list(tmp_path.glob("*.WG_BACKPORT_FAILED_REPORT.*"))
    # A fresh token permits retry using the previously completed preflight.
    report=e.run_conversion(source,template,out,verified_preflight=ready,workers=workers,cancel=e.CancellationToken(),log=lambda s:None)
    assert report["output_promoted"] and report["preflight_reused"]


def test_cancel_at_end_never_promotes(tmp_path):
    source,template=worlds(tmp_path)
    ready=e.run_conversion_preflight(source,template,workers=1,log=lambda s:None)
    token=e.CancellationToken();out=tmp_path/"late_cancel"
    def log(line):
        if line.startswith("Convert [5/5]"):token.cancel()
    with pytest.raises(e.ConversionCancelled):e.run_conversion(source,template,out,verified_preflight=ready,workers=1,cancel=token,log=log)
    assert not out.exists() and not list(tmp_path.glob(".*wgmbps-staging*"))


def test_serial_cancel_inside_chunk_loop_propagates(tmp_path,monkeypatch):
    source,template=worlds(tmp_path)
    token=e.CancellationToken();parse=e.parse_modern_chunk;calls=[]
    e.write_region(source/"r.0.0.mca",{j:chunk(j) for j in range(16)})
    def cancelled_parse(raw):
        result=parse(raw);calls.append(1);token.cancel();return result
    monkeypatch.setattr(e,"parse_modern_chunk",cancelled_parse)
    with pytest.raises(e.ConversionCancelled):
        e.preflight_source_mappings(sorted(source.glob("*.mca")),e.load_target_registry(template),workers=1,cancel=token,log=lambda s:None)
    assert len(calls)==1


def test_cancel_entity_audit(tmp_path):
    source,template=worlds(tmp_path)
    entities=source/"entities";entities.mkdir()
    for i in range(3):e.write_region(entities/("r.%d.0.mca"%i),{0:chunk(0)})
    token=e.CancellationToken()
    def log(line):
        if line.startswith("Entity audit [1/"):token.cancel()
    with pytest.raises(e.ConversionCancelled):e.audit_source_entities(source,tmp_path,workers=2,cancel=token,log=log)


def test_gui_cancel_and_retry_preflight_and_conversion(tmp_path,monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    from PySide6.QtWidgets import QApplication,QMessageBox
    from wgmap_backporter_studio.ui.main_window import BackportTab
    app=QApplication.instance() or QApplication([])
    errors=[]
    monkeypatch.setattr(QMessageBox,"critical",lambda *args:errors.append(args))
    monkeypatch.setattr(QMessageBox,"information",lambda *args:None)
    monkeypatch.setattr(QMessageBox,"warning",lambda *args:errors.append(args))
    source,template=worlds(tmp_path)
    tab=BackportTab();tab.source.setText(str(source));tab.template.setText(str(template))
    tab.output.setText(str(tmp_path/"gui_output"));tab.region_workers.setValue(1)
    def finish():
        deadline=time.monotonic()+10
        while tab._thread is not None and time.monotonic()<deadline:
            app.processEvents();time.sleep(0.005)
        assert tab._thread is None and not errors
    tab.scan_source();assert tab.cancel_btn.isEnabled()
    tab.cancel_task();assert not tab.cancel_btn.isEnabled()
    assert "Cancelling" in tab.timing_status.text()
    finish()
    assert "Cancelled" in tab.timing_status.text()
    assert not tab.convert_btn.isEnabled() and tab.scan_btn.isEnabled()
    assert not tab._timing_timer.isActive() and tab.progress.value()==0
    tab.scan_source();finish()
    assert tab.convert_btn.isEnabled() and not tab._cancel_token.is_set()
    tab.convert();tab.cancel_task();finish()
    assert tab.convert_btn.isEnabled() and not (tmp_path/"gui_output").exists()
    tab.convert();finish()
    assert (tmp_path/"gui_output").exists()
    assert not tab.cancel_btn.isEnabled()
    tab.close()
