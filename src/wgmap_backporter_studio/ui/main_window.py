from __future__ import annotations

import json
import os
import tempfile
import traceback
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from .. import APP_NAME, __version__
from ..core.catalog import load_catalog
from ..core.jar_analyzer import analyze_jar, read_texture_bytes
from ..core.legacy1710_engine import analyze_source, run_conversion
from ..core.modpack_analyzer import analyze_modpack
from ..core.version_targets import TARGETS


class FunctionWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    log_line = Signal(str)

    def __init__(self, fn):
        super().__init__(); self.fn = fn

    @Slot()
    def run(self):
        try:
            result = self.fn(self.log_line.emit)
            self.finished.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


def _muted(text: str) -> QLabel:
    x = QLabel(text); x.setObjectName("muted"); x.setWordWrap(True); return x


def _title(text: str, subtitle: str) -> QVBoxLayout:
    box = QVBoxLayout()
    t = QLabel(text); t.setObjectName("pageTitle")
    box.addWidget(t); box.addWidget(_muted(subtitle)); return box



def _configure_resizable_columns(table: QTableWidget, widths: tuple[int, ...]) -> None:
    """Give analyzer tables readable defaults without locking user resizing."""
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setMinimumSectionSize(76)
    header.setStretchLastSection(False)
    for index, width in enumerate(widths):
        table.setColumnWidth(index, width)


def _path_row(parent, label: str, mode: str, target: QLineEdit, file_filter: str = "All files (*)"):
    wrap = QWidget(parent); lay = QHBoxLayout(wrap); lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(target, 1)
    btn = QPushButton("Browse…")
    def browse():
        if mode == "file":
            p, _ = QFileDialog.getOpenFileName(parent, label, target.text() or str(Path.home()), file_filter)
        elif mode == "save":
            p, _ = QFileDialog.getSaveFileName(parent, label, target.text() or str(Path.home()), file_filter)
        else:
            p = QFileDialog.getExistingDirectory(parent, label, target.text() or str(Path.home()))
        if p: target.setText(p)
    btn.clicked.connect(browse); lay.addWidget(btn)
    return wrap


class DashboardTab(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self); root.addLayout(_title(
            "Map Backporter Studio",
            "A reusable desktop workspace for inspecting mod blocks and backporting modern Java Edition maps into older modded worlds."
        ))
        cards = QGridLayout()
        items = [
            ("Map Backporter", "Convert modern Anvil regions into a validated older target format. The 1.7.10 Forge/HBM backend is available now."),
            ("Mod / JAR Analyzer", "Inspect a mod JAR's block textures, blockstate/model assets, metadata and likely registry names without launching Minecraft."),
            ("Modpack Analyzer", "Inspect local modpack instances or ZIP exports and build a reusable target-block catalog from the JARs actually present."),
            ("Catalog Workspace", "Search exported block catalogs while preparing or reviewing mapping profiles for future conversion backends."),
        ]
        for i, (name, desc) in enumerate(items):
            card = QFrame(); card.setObjectName("card"); l = QVBoxLayout(card)
            h = QLabel(name); h.setObjectName("sectionTitle"); l.addWidget(h); l.addWidget(_muted(desc)); l.addStretch()
            cards.addWidget(card, i // 2, i % 2)
        root.addLayout(cards)
        grp = QGroupBox("Target support")
        gl = QGridLayout(grp)
        gl.addWidget(QLabel("Version"), 0, 0); gl.addWidget(QLabel("Status"), 0, 1); gl.addWidget(QLabel("Notes"), 0, 2)
        for r, t in enumerate(TARGETS, 1):
            gl.addWidget(QLabel(t.version), r, 0); gl.addWidget(QLabel(t.status), r, 1); gl.addWidget(_muted(t.notes), r, 2)
        gl.setColumnStretch(2, 1); root.addWidget(grp); root.addStretch()


class AsyncTab(QWidget):
    def __init__(self):
        super().__init__(); self._thread = None; self._worker = None
        self._done_cb = None; self._error_cb = None; self._log_cb = None

    def launch(self, fn, on_done, on_error=None, log=None):
        if self._thread is not None:
            return
        self._done_cb, self._error_cb, self._log_cb = on_done, on_error, log
        self._thread = QThread(self); self._worker = FunctionWorker(fn); self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # These receivers are methods on this QWidget, so Qt queues them back to the GUI thread.
        self._worker.log_line.connect(self._dispatch_log)
        self._worker.finished.connect(self._dispatch_done)
        self._worker.failed.connect(self._dispatch_failed)
        self._thread.start()

    @Slot(str)
    def _dispatch_log(self, line):
        if self._log_cb: self._log_cb(line)

    @Slot(object)
    def _dispatch_done(self, result):
        try:
            if self._done_cb: self._done_cb(result)
        finally:
            self._cleanup_thread()

    @Slot(str)
    def _dispatch_failed(self, tb):
        try:
            if self._error_cb: self._error_cb(tb)
            else: QMessageBox.critical(self, "Operation failed", tb)
        finally:
            self._cleanup_thread()

    def _cleanup_thread(self):
        if self._thread:
            self._thread.quit(); self._thread.wait(1500); self._thread.deleteLater()
        if self._worker: self._worker.deleteLater()
        self._thread = None; self._worker = None
        self._done_cb = None; self._error_cb = None; self._log_cb = None


class BackportTab(AsyncTab):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self); root.addLayout(_title(
            "Map Backporter",
            "Select the modern map, an older target/template world created with the exact destination modpack, and an empty output folder."
        ))
        # Keep the configuration controls at their usable size even when the
        # outer window is vertically constrained. Short windows scroll this
        # page instead of asking Qt to crush form rows into one another.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_body = QWidget(scroll)
        body = QVBoxLayout(scroll_body)
        body.setContentsMargins(0, 0, 0, 0)

        form_group = QGroupBox("Conversion job")
        # QFormLayout defaults are platform-style dependent. On macOS the native
        # defaults keep fields close to their size hints and center the form,
        # which can make this page appear vertically/horizontally collapsed.
        # Pin the layout policy so the same form geometry is used on every OS.
        form_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        form_group.setMinimumHeight(195)
        form = QFormLayout(form_group)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.source = QLineEdit(); self.source.setPlaceholderText("Modern world folder, region folder, region ZIP, or .mca")
        src_wrap = QWidget(self); src_l = QHBoxLayout(src_wrap); src_l.setContentsMargins(0, 0, 0, 0); src_l.addWidget(self.source, 1)
        src_file = QPushButton("File / ZIP…"); src_folder = QPushButton("Folder…")
        def choose_source_file():
            p, _ = QFileDialog.getOpenFileName(self, "Select modern map/region file", self.source.text() or str(Path.home()), "Minecraft map data (*.zip *.mca);;All files (*)")
            if p: self.source.setText(p)
        def choose_source_folder():
            p = QFileDialog.getExistingDirectory(self, "Select modern world/region folder", self.source.text() or str(Path.home()))
            if p: self.source.setText(p)
        src_file.clicked.connect(choose_source_file); src_folder.clicked.connect(choose_source_folder); src_l.addWidget(src_file); src_l.addWidget(src_folder)
        form.addRow("Source map", src_wrap)
        self.version = QComboBox()
        self.version.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.version.setMinimumWidth(220)
        self.version.setMaximumWidth(320)
        for t in TARGETS: self.version.addItem(f"{t.version} — {t.status}", t)
        self.version.currentIndexChanged.connect(self._target_changed); form.addRow("Target version", self.version)
        self.target_status = _muted("")
        self.target_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        form.addRow("Backend", self.target_status)
        self.template = QLineEdit(); self.template.setPlaceholderText("Saved target world opened once with the destination modpack")
        form.addRow("Template world", _path_row(self, "Select target/template world", "dir", self.template))
        self.output = QLineEdit(); self.output.setPlaceholderText("New or empty output world folder")
        form.addRow("Output world", _path_row(self, "Select empty output folder", "dir", self.output))
        body.addWidget(form_group)

        opts = QGroupBox("Surface / compatibility options")
        opts.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        opts.setMinimumHeight(150)
        og = QGridLayout(opts)
        og.setColumnStretch(1, 1)
        og.setHorizontalSpacing(12)
        og.setVerticalSpacing(8)
        self.hbm = QCheckBox("Use safe HBM architectural replacements"); self.hbm.setChecked(True)
        self.hbm.setToolTip("Only architectural/decorative substitutes are selected; machines, ores and valuable resource blocks are intentionally excluded.")
        self.yoff = QSpinBox(); self.yoff.setRange(-192, 192); self.yoff.setSingleStep(16); self.yoff.setValue(0); self.yoff.setMaximumWidth(180)
        self.strip = QSpinBox(); self.strip.setRange(0, 255); self.strip.setValue(0); self.strip.setMaximumWidth(180)
        og.addWidget(self.hbm, 0, 0, 1, 2); og.addWidget(QLabel("Vertical offset"), 1, 0); og.addWidget(self.yoff, 1, 1)
        og.addWidget(QLabel("Strip/fill below target Y"), 2, 0); og.addWidget(self.strip, 2, 1)
        og.addWidget(_muted("For 1.7.10, source blocks below Y=0 or above Y=255 cannot be represented. Offset 0 preserves normal RTG/sea-level alignment."), 3, 0, 1, 2)
        body.addWidget(opts)

        buttons = QHBoxLayout(); self.scan_btn = QPushButton("Scan source"); self.convert_btn = QPushButton("Convert map"); self.convert_btn.setObjectName("primary")
        buttons.addWidget(self.scan_btn); buttons.addStretch(); buttons.addWidget(self.convert_btn); body.addLayout(buttons)
        self.progress = QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0); body.addWidget(self.progress)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMinimumHeight(150); body.addWidget(self.log, 1)
        scroll_body.setMinimumHeight(610)
        scroll.setWidget(scroll_body)
        root.addWidget(scroll, 1)
        self.scan_btn.clicked.connect(self.scan_source); self.convert_btn.clicked.connect(self.convert); self._target_changed()

    def _target_changed(self):
        t = self.version.currentData()
        self.target_status.setText(f"{t.status}: {t.notes}")
        self.convert_btn.setEnabled(t.backend is not None and self._thread is None)
        self.hbm.setEnabled(t.version == "1.7.10")

    def _set_busy(self, busy: bool):
        self.scan_btn.setEnabled(not busy); self.convert_btn.setEnabled((not busy) and self.version.currentData().backend is not None)
        self.progress.setRange(0, 0 if busy else 1)
        if not busy: self.progress.setValue(1)

    def _log(self, s): self.log.appendPlainText(str(s))

    def scan_source(self):
        if not self.source.text().strip():
            QMessageBox.warning(self, "Missing source", "Select a modern world, region folder, or ZIP first."); return
        self.log.clear(); self._set_busy(True)
        def work(log):
            fd, p = tempfile.mkstemp(prefix="wg_backporter_scan_", suffix=".json"); os.close(fd)
            try: return analyze_source(self.source.text().strip(), p, log)
            finally:
                try: Path(p).unlink()
                except Exception: pass
        def done(rep):
            self._set_busy(False)
            self._log("\nScan summary:\n" + json.dumps(rep, indent=2)[:12000])
            QMessageBox.information(self, "Scan complete", f"Found {rep.get('regions', 0)} region files and {rep.get('chunks', 0)} chunks.")
        def err(tb): self._set_busy(False); self._log(tb); QMessageBox.critical(self, "Scan failed", tb)
        self.launch(work, done, err, self._log)

    def convert(self):
        t = self.version.currentData()
        if t.backend != "legacy1710":
            QMessageBox.information(self, "Backend not implemented", f"{t.version} is scaffolded but deliberately not enabled yet."); return
        source, template, output = self.source.text().strip(), self.template.text().strip(), self.output.text().strip()
        if not source or not template or not output:
            QMessageBox.warning(self, "Missing paths", "Select the source map, target/template world and output folder."); return
        if self.yoff.value() % 16:
            QMessageBox.warning(self, "Invalid offset", "The 1.7.10 vertical offset must be a multiple of 16."); return
        if Path(output).exists() and any(Path(output).iterdir()):
            QMessageBox.warning(self, "Output is not empty", "Choose a new or empty output folder. The converter intentionally refuses to overwrite an existing world."); return
        self.log.clear(); self._set_busy(True)
        def work(log):
            return run_conversion(source, template, output, self.hbm.isChecked(), self.yoff.value(), self.strip.value(), log)
        def done(rep):
            self._set_busy(False); self._log("\nFinished.\n" + json.dumps({k: rep.get(k) for k in ("regions_converted","chunks_converted","chunks_failed","chunks_cropped_above_255","chunks_cropped_below_0")}, indent=2))
            QMessageBox.information(self, "Backport complete", "Conversion finished. Review WG_BACKPORT_REPORT.txt in the output world before opening it in Minecraft.")
        def err(tb): self._set_busy(False); self._log(tb); QMessageBox.critical(self, "Conversion failed", tb)
        self.launch(work, done, err, self._log)


class JarAnalyzerTab(AsyncTab):
    def __init__(self):
        super().__init__(); self.catalog = None
        root = QVBoxLayout(self); root.addLayout(_title(
            "Mod / JAR Analyzer",
            "Build a visual block-asset catalog from a mod JAR. This is useful when deciding what an older modpack can substitute during a map backport."
        ))
        top = QHBoxLayout(); self.jar = QLineEdit(); self.jar.setPlaceholderText("Select a mod .jar")
        top.addWidget(self.jar, 1); browse = QPushButton("Browse…"); analyze = QPushButton("Analyze JAR"); analyze.setObjectName("primary")
        top.addWidget(browse); top.addWidget(analyze); root.addLayout(top)
        self.summary = _muted("No JAR analyzed yet."); root.addWidget(self.summary)
        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 6); self.table.setHorizontalHeaderLabels(["Registry hint", "Display name", "Confidence", "Evidence", "Textures", "Models"])
        _configure_resizable_columns(self.table, (170, 180, 110, 210, 90, 90))
        self.table.setSelectionBehavior(QTableWidget.SelectRows); self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.setSortingEnabled(True)
        splitter.addWidget(self.table)
        side = QWidget(); sl = QVBoxLayout(side); self.preview = QLabel("Select a block to preview its first packaged texture."); self.preview.setAlignment(Qt.AlignCenter); self.preview.setMinimumSize(250, 250); self.preview.setWordWrap(True)
        self.preview.setStyleSheet("background:#0b0f14;border:1px solid #303b48;border-radius:8px;")
        sl.addWidget(self.preview, 1); self.notes = QPlainTextEdit(); self.notes.setReadOnly(True); self.notes.setMaximumHeight(150); sl.addWidget(self.notes); splitter.addWidget(side); splitter.setChildrenCollapsible(False); splitter.setSizes([800, 320]); root.addWidget(splitter, 1)
        bottom = QHBoxLayout(); self.export = QPushButton("Export catalog JSON…"); self.export.setEnabled(False); bottom.addStretch(); bottom.addWidget(self.export); root.addLayout(bottom)
        browse.clicked.connect(self._browse); analyze.clicked.connect(self._analyze); self.export.clicked.connect(self._export); self.table.itemSelectionChanged.connect(self._preview_selected)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select mod JAR", self.jar.text() or str(Path.home()), "Java archives (*.jar);;All files (*)")
        if p: self.jar.setText(p)

    def _analyze(self):
        p = self.jar.text().strip()
        if not p: QMessageBox.warning(self, "Missing JAR", "Select a mod JAR first."); return
        self.summary.setText("Analyzing…"); self.table.setRowCount(0); self.preview.setText("Analyzing…")
        def work(log): return analyze_jar(p, log=log)
        def done(cat): self.catalog = cat; self._show_catalog(cat)
        def err(tb): self.summary.setText("Analysis failed."); QMessageBox.critical(self, "JAR analysis failed", tb)
        self.launch(work, done, err)

    def _show_catalog(self, cat):
        self.summary.setText(f"{cat.mod_name or Path(cat.source).name} • {cat.loader_hint} • {cat.mod_version or 'version unknown'} • {len(cat.blocks):,} block asset candidates")
        self.table.setSortingEnabled(False); self.table.setRowCount(len(cat.blocks))
        for r, b in enumerate(cat.blocks):
            vals = [b.registry_hint, b.display_name, b.confidence, b.evidence, str(len(b.texture_paths)), str(len(b.model_paths))]
            for c, v in enumerate(vals): self.table.setItem(r, c, QTableWidgetItem(v))
        self.table.setSortingEnabled(True)
        self.notes.setPlainText("\n".join(cat.notes)); self.export.setEnabled(True); self.preview.setText("Select a block to preview its first packaged texture.")

    def _preview_selected(self):
        if not self.catalog: return
        rows = self.table.selectionModel().selectedRows()
        if not rows: return
        b = self.catalog.blocks[rows[0].row()]
        if not b.texture_paths:
            self.preview.setPixmap(QPixmap()); self.preview.setText("No direct PNG texture was linked to this candidate."); return
        try:
            raw = read_texture_bytes(self.jar.text().strip(), b.texture_paths[0])
            px = QPixmap(); px.loadFromData(raw); px = px.scaled(240, 240, Qt.KeepAspectRatio, Qt.FastTransformation)
            self.preview.setText(""); self.preview.setPixmap(px); self.preview.setToolTip(b.texture_paths[0])
        except Exception as e:
            self.preview.setPixmap(QPixmap()); self.preview.setText(f"Preview failed:\n{e}")

    def _export(self):
        if not self.catalog: return
        suggested = (self.catalog.mod_ids[0] if self.catalog.mod_ids else "mod") + "-block-catalog.json"
        p, _ = QFileDialog.getSaveFileName(self, "Export block catalog", suggested, "JSON (*.json)")
        if p: self.catalog.save(p)


class ModpackAnalyzerTab(AsyncTab):
    def __init__(self):
        super().__init__(); self.analysis = None
        root = QVBoxLayout(self); root.addLayout(_title(
            "Modpack Analyzer",
            "Inspect the mods physically present in an instance or export. CurseForge manifests are recognized; missing JARs remain explicitly unresolved."
        ))
        top = QHBoxLayout(); self.path = QLineEdit(); self.path.setPlaceholderText("Modpack instance folder or ZIP export")
        top.addWidget(self.path, 1); browse_folder = QPushButton("Folder…"); browse_zip = QPushButton("ZIP…"); run = QPushButton("Analyze modpack"); run.setObjectName("primary")
        top.addWidget(browse_folder); top.addWidget(browse_zip); top.addWidget(run); root.addLayout(top)
        self.summary = _muted("No modpack analyzed yet."); root.addWidget(self.summary)
        self.table = QTableWidget(0, 6); self.table.setHorizontalHeaderLabels(["Mod", "Mod IDs", "Version", "Loader", "Block candidates", "Source"])
        _configure_resizable_columns(self.table, (210, 150, 110, 100, 150, 300))
        self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.setSortingEnabled(True); root.addWidget(self.table, 1)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(170); root.addWidget(self.log)
        bottom = QHBoxLayout(); self.export = QPushButton("Export combined analysis…"); self.export.setEnabled(False); bottom.addStretch(); bottom.addWidget(self.export); root.addLayout(bottom)
        browse_folder.clicked.connect(self._folder); browse_zip.clicked.connect(self._zip); run.clicked.connect(self._run); self.export.clicked.connect(self._export)

    def _folder(self):
        p = QFileDialog.getExistingDirectory(self, "Select modpack instance", self.path.text() or str(Path.home()))
        if p: self.path.setText(p)
    def _zip(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select modpack ZIP", self.path.text() or str(Path.home()), "ZIP archives (*.zip);;All files (*)")
        if p: self.path.setText(p)
    def _run(self):
        p = self.path.text().strip()
        if not p: QMessageBox.warning(self, "Missing modpack", "Select an instance folder or ZIP first."); return
        self.log.clear(); self.summary.setText("Analyzing…")
        def work(log): return analyze_modpack(p, log=log)
        def done(rep):
            self.analysis = rep; self.summary.setText(f"{rep.pack_name or Path(rep.source).name} • Minecraft {rep.minecraft_version or 'unknown'} • {rep.local_jars} local JARs • {rep.manifested_files} manifest entries")
            self.table.setSortingEnabled(False); self.table.setRowCount(len(rep.mods))
            for r, m in enumerate(rep.mods):
                vals = [m.mod_name or Path(m.source).name, ", ".join(m.mod_ids), m.version, m.loader_hint, str(m.block_candidates), m.source]
                for c, v in enumerate(vals): self.table.setItem(r, c, QTableWidgetItem(v))
            self.table.setSortingEnabled(True)
            if rep.notes: self.log.appendPlainText("\n".join(rep.notes))
            self.export.setEnabled(True)
        def err(tb): self.summary.setText("Analysis failed."); self.log.appendPlainText(tb); QMessageBox.critical(self, "Modpack analysis failed", tb)
        self.launch(work, done, err, self.log.appendPlainText)
    def _export(self):
        if not self.analysis: return
        p, _ = QFileDialog.getSaveFileName(self, "Export modpack analysis", "modpack-block-analysis.json", "JSON (*.json)")
        if p: self.analysis.save(p)


class CatalogTab(QWidget):
    def __init__(self):
        super().__init__(); self.rows = []
        root = QVBoxLayout(self); root.addLayout(_title(
            "Catalog Workspace",
            "Load a JAR or modpack analysis JSON and search the candidate target blocks while planning mapping profiles."
        ))
        top = QHBoxLayout(); self.path = QLineEdit(); self.path.setReadOnly(True); load = QPushButton("Load catalog…"); self.search = QLineEdit(); self.search.setPlaceholderText("Search registry, display name, mod or evidence…")
        top.addWidget(self.path, 1); top.addWidget(load); top.addWidget(self.search, 1); root.addLayout(top)
        self.table = QTableWidget(0, 6); self.table.setHorizontalHeaderLabels(["Registry", "Display", "Mod", "Confidence", "Evidence", "Texture assets"])
        _configure_resizable_columns(self.table, (220, 220, 150, 110, 260, 120)); self.table.setSortingEnabled(True); root.addWidget(self.table, 1)
        load.clicked.connect(self._load); self.search.textChanged.connect(self._filter)
    def _load(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load block catalog", str(Path.home()), "JSON (*.json);;All files (*)")
        if not p: return
        try:
            data = load_catalog(p); self.path.setText(p); rows = []
            if data.get("kind") == "mod_block_catalog": rows = data.get("blocks", [])
            elif data.get("kind") == "modpack_block_analysis":
                for cat in data.get("block_catalogs", []): rows.extend(cat.get("blocks", []))
            else: raise ValueError("This JSON is not a WG block catalog or modpack analysis.")
            self.rows = rows; self._filter()
        except Exception as e: QMessageBox.critical(self, "Catalog load failed", str(e))
    def _filter(self):
        q = self.search.text().strip().lower(); rows = []
        for b in self.rows:
            hay = " ".join(str(b.get(k, "")) for k in ("registry_hint","display_name","source_mod","confidence","evidence")).lower()
            if not q or q in hay: rows.append(b)
        self.table.setSortingEnabled(False); self.table.setRowCount(len(rows))
        for r, b in enumerate(rows):
            vals = [b.get("registry_hint",""), b.get("display_name",""), b.get("source_mod",""), b.get("confidence",""), b.get("evidence",""), str(len(b.get("texture_paths",[]) or []))]
            for c, v in enumerate(vals): self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self.table.setSortingEnabled(True)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle(f"{APP_NAME} {__version__}"); self.resize(1260, 820); self.setMinimumSize(980, 660)
        root = QWidget(); root.setObjectName("rootWindow"); layout = QVBoxLayout(root); layout.setContentsMargins(18, 16, 18, 12)
        header = QHBoxLayout(); brand = QLabel(APP_NAME); brand.setObjectName("sectionTitle"); header.addWidget(brand); header.addStretch(); header.addWidget(_muted(f"v{__version__}")); layout.addLayout(header)
        tabs = QTabWidget(); tabs.setDocumentMode(True); tabs.addTab(DashboardTab(), "Overview"); tabs.addTab(BackportTab(), "Map Backporter"); tabs.addTab(JarAnalyzerTab(), "Mod / JAR Analyzer"); tabs.addTab(ModpackAnalyzerTab(), "Modpack Analyzer"); tabs.addTab(CatalogTab(), "Catalog Workspace"); layout.addWidget(tabs, 1)
        self.setCentralWidget(root); self.statusBar().showMessage("Ready — conversions never modify the selected source map or template world in place.")
