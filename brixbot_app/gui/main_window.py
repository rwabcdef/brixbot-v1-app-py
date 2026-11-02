"""BrixBot main window - the configuration screen of the concept sketch:

    Hardware (Select / Detect)   Configuration (Generate / Verify / Load)   Run / Stop
    [ Hub column ]   [ Link column ]   [ App column (live controls) ]
    [ log ]

Editing happens in the project JSON file for now (File > Reload picks up
changes). All serial / hub work runs on worker threads; results come back
through the Bridge, so engine and widgets are only touched on the GUI thread.
"""

from __future__ import annotations

import html
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QVBoxLayout, QWidget,
)

from .. import hubio
from ..engine import errors as E
from ..engine.app_components import now_ms
from ..engine.builder import BINDING_KEY
from ..engine.project import HARDWARE_TYPES, Project
from ..engine.runtime import Program
from ..engine.specs import APP_SPECS, HUB_SPECS, SIDE_APP, SIDE_HUB
from ..serlink import Frame, Link
from .app_widgets import make_view
from .bridge import Bridge
from .cards import Card, Column

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


class MainWindow(QMainWindow):
    def __init__(self, project_path: str | None = None):
        super().__init__()
        self.setWindowTitle("BrixBot")
        self.resize(1180, 860)
        self.bridge = Bridge(self)
        self.bridge.idle_hooks.append(self._tick)
        self.link = Link()
        self.link.add_any_listener(lambda f: self.bridge.post(lambda: self._on_frame(f)))
        self.link.set_traffic_callback(lambda d, line: self.bridge.post(lambda: self._on_traffic(d, line)))
        self.project = Project()
        self.app_program: Program | None = None
        self.hub_loaded = False
        self.hub_running = False
        self.busy = False
        self._views: list = []

        self._build_menu()
        self._build_ui()
        self._update_buttons()
        if project_path:
            self.open_project(project_path)
        self.log("BrixBot app ready - open a project (File > Open) and connect a hub (Detect).")

    # ================================================================ UI
    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")
        for text, key, slot in (
            ("&Open...", QKeySequence.StandardKey.Open, self.action_open),
            ("&Reload", "Ctrl+R", self.action_reload),
            ("&Save", QKeySequence.StandardKey.Save, self.action_save),
            ("Save &As...", QKeySequence.StandardKey.SaveAs, self.action_save_as),
        ):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(slot)
            m.addAction(a)
        m.addSeparator()
        quit_a = QAction("E&xit", self)
        quit_a.triggered.connect(self.close)
        m.addAction(quit_a)
        h = self.menuBar().addMenu("&Hub")
        st = QAction("&Status", self)
        st.triggered.connect(self.action_status)
        h.addAction(st)
        dis = QAction("&Disconnect", self)
        dis.triggered.connect(self.disconnect)
        h.addAction(dis)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        top = QHBoxLayout()
        self.hw_box = QGroupBox("Hardware")
        hl = QHBoxLayout(self.hw_box)
        self.btn_select = QPushButton("Select")
        self.btn_detect = QPushButton("Detect")
        self.btn_select.clicked.connect(self.action_select)
        self.btn_detect.clicked.connect(self.action_detect)
        hl.addWidget(self.btn_select)
        hl.addWidget(self.btn_detect)
        top.addWidget(self.hw_box)

        cfg = QGroupBox("Configuration")
        cl = QHBoxLayout(cfg)
        self.btn_generate = QPushButton("Generate")
        self.btn_verify = QPushButton("Verify")
        self.btn_load = QPushButton("Load")
        self.btn_generate.clicked.connect(self.action_generate)
        self.btn_verify.clicked.connect(self.action_verify)
        self.btn_load.clicked.connect(self.action_load)
        for b in (self.btn_generate, self.btn_verify, self.btn_load):
            cl.addWidget(b)
        top.addWidget(cfg)

        run = QGroupBox("Run")
        rl = QHBoxLayout(run)
        self.btn_run = QPushButton("Run")
        self.btn_stop = QPushButton("Stop")
        self.btn_run.clicked.connect(self.action_run)
        self.btn_stop.clicked.connect(self.action_stop)
        rl.addWidget(self.btn_run)
        rl.addWidget(self.btn_stop)
        top.addWidget(run)
        top.addStretch(1)
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Vertical)
        cols = QWidget()
        colsl = QHBoxLayout(cols)
        colsl.setContentsMargins(0, 0, 0, 0)
        self.col_hub = Column("Hub")
        self.col_link = Column("Link")
        self.col_app = Column("App")
        for c in (self.col_hub, self.col_link, self.col_app):
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setWidget(c)
            sa.setFrameShape(QScrollArea.Shape.NoFrame)
            colsl.addWidget(sa, 1)
        split.addWidget(cols)

        logw = QWidget()
        ll = QVBoxLayout(logw)
        ll.setContentsMargins(0, 0, 0, 0)
        lh = QHBoxLayout()
        lh.addWidget(QLabel("Log"))
        lh.addStretch(1)
        self.chk_frames = QCheckBox("show frames")
        lh.addWidget(self.chk_frames)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.log_box.clear())
        lh.addWidget(clear)
        ll.addLayout(lh)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(3000)
        self.log_box.setStyleSheet("font-family: Consolas, monospace;")
        ll.addWidget(self.log_box)
        split.addWidget(logw)
        split.setSizes([620, 200])
        root.addWidget(split, 1)
        self.setCentralWidget(central)
        self.status = self.statusBar()
        self._update_hw_title()

    # ================================================================ log
    def log(self, msg: str, kind: str = "info") -> None:
        color = {"info": None, "ok": "#2a9d3a", "error": "#d03030", "warn": "#c07000",
                 "tx": "#3070c0", "rx": "#8040a0"}.get(kind)
        text = f"{time.strftime('%H:%M:%S')}  {html.escape(msg)}"
        self.log_box.appendHtml(f'<span style="color:{color}">{text}</span>' if color else text)

    def _on_traffic(self, direction: str, line: str) -> None:
        if self.chk_frames.isChecked():
            self.log(("-> " if direction == "tx" else "<- ") + line, direction)

    # ================================================================ state
    def _update_hw_title(self) -> None:
        hw = HARDWARE_TYPES.get(self.project.hardware, self.project.hardware)
        port = self.link.port_name if self.link.is_open else "not connected"
        self.hw_box.setTitle(f"Hardware: {hw} - {port}")

    def _update_buttons(self) -> None:
        idle = not self.busy
        connected = self.link.is_open
        running = self.app_program is not None and self.app_program.running
        self.btn_select.setEnabled(idle and not running)
        self.btn_detect.setEnabled(idle and not running)
        self.btn_generate.setEnabled(idle)
        self.btn_verify.setEnabled(idle)
        self.btn_load.setEnabled(idle and connected and not running)
        self.btn_run.setEnabled(idle and not running and self.app_program is not None)
        self.btn_stop.setEnabled(idle and running)
        self._update_hw_title()

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self.busy = busy
        self.status.showMessage(msg)
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()
        self._update_buttons()

    def _background(self, msg, work, done, failed=None) -> None:
        self._set_busy(True, msg)

        def ok(result):
            self._set_busy(False)
            done(result)

        def bad(exc):
            self._set_busy(False)
            if failed:
                failed(exc)
            else:
                self.log(f"{msg.rstrip('.')} failed: {exc}", "error")

        self.bridge.run_in_thread(work, ok, bad)

    # ================================================================ project
    def action_open(self) -> None:
        start = str(self.project.path.parent if self.project.path else EXAMPLES)
        path, _ = QFileDialog.getOpenFileName(self, "Open BrixBot project", start, "BrixBot project (*.json)")
        if path:
            self.open_project(path)

    def action_reload(self) -> None:
        if self.project.path:
            self.open_project(str(self.project.path))

    def action_save(self) -> None:
        if self.project.path is None:
            self.action_save_as()
            return
        self.project.save()
        self.log(f"saved {self.project.path}", "ok")

    def action_save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save BrixBot project", str(self.project.path or ""),
                                              "BrixBot project (*.json)")
        if path:
            self.project.save(path)
            self.setWindowTitle(f"BrixBot - {Path(path).name}")
            self.log(f"saved {path}", "ok")

    def open_project(self, path: str) -> None:
        if self.app_program is not None and self.app_program.running:
            self.action_stop()
        try:
            self.project = Project.load(path)
        except Exception as exc:
            self.log(f"cannot open {path}: {exc}", "error")
            QMessageBox.warning(self, "Open project", f"Cannot open {path}:\n{exc}")
            return
        self.setWindowTitle(f"BrixBot - {Path(path).name}")
        self.hub_loaded = False
        self.app_program = None
        try:
            self.app_program = self.project.build_app()
            self.app_program.sender = self._send
            self.app_program.on_error = lambda m: self.log(m, "warn")
        except E.BuildError as exc:
            self.log(exc.describe("app") + self.project.locate(SIDE_APP, exc), "error")
        self.log(f"opened {path}")
        self._populate()
        self._update_buttons()

    # ================================================================ columns
    def _populate(self) -> None:
        for col in (self.col_hub, self.col_link, self.col_app):
            col.clear()
        self._views = []
        self._populate_side(self.col_hub, SIDE_HUB, self.project.hub, None)
        self._populate_link()
        self._populate_side(self.col_app, SIDE_APP, self.project.app, self.app_program)
        for col in (self.col_hub, self.col_link, self.col_app):
            col.finish()
        self._enable_views(False)

    def _populate_side(self, col: Column, side: str, section: dict, program: Program | None) -> None:
        specs = HUB_SPECS if side == SIDE_HUB else APP_SPECS
        comps = [c for c in section.get("components") or [] if isinstance(c, dict)]
        funcs = {str(f.get("id")): f.get("#name") or f"function {f.get('id')}"
                 for f in section.get("functions") or [] if isinstance(f, dict)}
        handlers = []
        for c in comps:
            spec = specs.get(c.get("type"))
            title = c.get("#name") or f"{c.get('type')} {c.get('id')}"
            if c.get("#name"):
                title += f"  ({c.get('type')} {c.get('id')})"
            card = Card(title, "component")
            if program is not None:
                model = program.resolve(c.get("type"), c.get("id"))
                if model is not None:
                    view = make_view(model)
                    card.add_widget(view)
                    self._views.append(view)
            if spec is None:
                card.add_row("<i>not supported</i>")
            else:
                if spec.commands:
                    card.add_row("controls: " + ", ".join(cmd.name for cmd in spec.commands))
                if spec.events:
                    card.add_row("events: " + ", ".join(e.name for e in spec.events))
                for ev in c.get("events") or []:
                    es = spec.event(ev.get("eventId")) if isinstance(ev, dict) else None
                    targets = [funcs.get(str(b.get("functionId")), f"#{b.get('functionId')}")
                               for b in ev.get("functions") or [] if isinstance(b, dict)]
                    if es and targets:
                        name = ev.get("#name") or f"{c.get('type')}{c.get('id')} {es.name}"
                        handlers.append(f"{name} → {', '.join(targets)}")
            col.add(card)
        # socket messages received by this side
        key = BINDING_KEY[side]
        for s in self.project.sockets:
            for si, sc in enumerate(s.get("schemas") or []):
                targets = [funcs.get(str(b.get("functionId")), f"#{b.get('functionId')}")
                           for b in sc.get(key) or [] if isinstance(b, dict)]
                if targets:
                    handlers.append(f"{s.get('protocol')} {_schema_label(sc, si)} → {', '.join(targets)}")
        if handlers:
            col.add(Card("Event Handlers", "handler", handlers))
        if funcs:
            col.add(Card("Functions", "function", list(funcs.values())))

    def _populate_link(self) -> None:
        for s in self.project.sockets:
            if not isinstance(s, dict):
                continue
            card = Card(f"Socket: {s.get('protocol')}", "socket", tooltip=s.get("#comment", ""))
            common = "".join(f"&lt;{html.escape(_field_label(f))}&gt;" for f in s.get("commonArgs") or [])
            for si, sc in enumerate(s.get("schemas") or []):
                hub_rx = bool(sc.get("functions"))
                app_rx = bool(sc.get("appFunctions"))
                arrow = "app → hub" if hub_rx and not app_rx else ("hub → app" if app_rx and not hub_rx else "↔")
                consts = html.escape("".join(str(c.get("value")) for c in sc.get("constantArgs") or []))
                args = "".join(f"&lt;{html.escape(_field_label(f))}&gt;" for f in sc.get("args") or [])
                label = sc.get("#comment")
                row = f"{arrow}&nbsp;&nbsp; <b>{common}{consts}{args}</b>"
                card.add_row(f"{row}<br><i>{html.escape(label)}</i>" if label else row, rich=True)
            self.col_link.add(card)

    def _enable_views(self, on: bool) -> None:
        for v in getattr(self, "_views", []):
            try:
                v.setEnabled(on)
            except RuntimeError:      # view deleted by a reload
                pass
        self._views = [v for v in getattr(self, "_views", []) if _alive(v)]

    # ================================================================ hardware
    def action_select(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Select hardware")
        form = QFormLayout(dlg)
        hw = QComboBox()
        for key, name in HARDWARE_TYPES.items():
            hw.addItem(name, key)
        hw.setCurrentIndex(max(0, hw.findData(self.project.hardware)))
        port = QComboBox()
        for dev, desc in hubio.list_ports():
            port.addItem(f"{dev}  {desc}", dev)
        if self.link.is_open:
            port.setCurrentIndex(max(0, port.findData(self.link.port_name)))
        form.addRow("Hardware", hw)
        form.addRow("Port", port)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.project.hardware = hw.currentData()
        if port.currentData():
            self.connect_port(port.currentData())
        self._update_buttons()

    def connect_port(self, port: str) -> None:
        def work():
            self.link.close()
            self.link.open_serial(port)
            time.sleep(0.3)
            return hubio.query(self.link)

        def done(status):
            self.hub_loaded = status.state == "K"
            self.hub_running = status.running
            self.log(f"connected to {port}: hub {status.describe()}", "ok")
            self._update_buttons()

        def failed(exc):
            self.log(f"{port}: {exc} - connected, but no BrixBot hub answered", "warn")
            self._update_buttons()

        self._background(f"Connecting to {port}...", work, done, failed)

    def disconnect(self) -> None:
        self.link.close()
        self.log("disconnected")
        self._update_buttons()

    def action_detect(self) -> None:
        self.link.close()

        def done(found):
            if not found:
                self.log("no BrixBot hub found on any serial port", "warn")
                self._update_buttons()
                return
            for port, st in found:
                self.log(f"found hub on {port}: {st.describe()}", "ok")
            self.connect_port(found[0][0])

        self._background("Detecting hubs...", hubio.detect, done)

    def action_status(self) -> None:
        if not self.link.is_open:
            self.log("not connected", "warn")
            return
        self._background("Reading hub status...", lambda: hubio.query(self.link),
                         lambda st: self.log(f"hub: {st.describe()}", "ok"))

    # ================================================================ configuration
    def action_verify(self, quiet: bool = False) -> bool:
        res = self.project.verify()
        if res.ok:
            self.log(f"verify OK - hub config {res.hub_config_len} chars", "ok")
        else:
            for e in res.errors:
                self.log(e, "error")
            if not quiet:
                QMessageBox.warning(self, "Verify", "\n\n".join(res.errors))
        return res.ok

    def action_generate(self) -> None:
        if not self.action_verify(quiet=True):
            QMessageBox.warning(self, "Generate", "The project has errors - see the log.")
            return
        text = self.project.generate()
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Hub config - {len(text)} chars")
        dlg.resize(760, 360)
        lay = QVBoxLayout(dlg)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        box.setStyleSheet("font-family: Consolas, monospace;")
        lay.addWidget(box)
        bb = QDialogButtonBox()
        copy = bb.addButton("Copy", QDialogButtonBox.ButtonRole.ActionRole)
        save = bb.addButton("Save...", QDialogButtonBox.ButtonRole.ActionRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(text))
        save.clicked.connect(lambda: self._save_generated(text))
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def _save_generated(self, text: str) -> None:
        start = str(self.project.path.with_suffix(".hub.json")) if self.project.path else ""
        path, _ = QFileDialog.getSaveFileName(self, "Save hub config", start, "JSON (*.json)")
        if path:
            Path(path).write_text(text, encoding="ascii")
            self.log(f"saved hub config to {path}", "ok")

    def action_load(self) -> None:
        if not self.action_verify(quiet=True):
            QMessageBox.warning(self, "Load", "The project has errors - see the log.")
            return
        text = self.project.generate()

        def progress(n, total):
            self.bridge.post(lambda: self.status.showMessage(f"Loading hub config... {n}/{total}"))

        def done(res):
            self.hub_loaded = res.ok
            self.hub_running = False
            self.log(res.message, "ok" if res.ok else "error")
            if not res.ok:
                QMessageBox.warning(self, "Load", res.message)

        self._background("Loading hub config...", lambda: hubio.load(self.link, text, progress), done)

    # ================================================================ run
    def action_run(self) -> None:
        if self.app_program is None:
            return

        def start_app(_=None):
            self.app_program.start()
            self._enable_views(True)
            self.log("running", "ok")
            self._update_buttons()

        if self.link.is_open and self.hub_loaded:
            # app first: the hub may send messages as soon as it runs
            start_app()

            def done(_):
                self.hub_running = True

            def failed(exc):
                self.log(f"hub would not start: {exc}", "error")
                self.app_program.stop()
                self._enable_views(False)
                self._update_buttons()

            self._background("Starting hub...", lambda: hubio.run(self.link), done, failed)
        else:
            self.log("hub not connected / not loaded - running the app side only", "warn")
            start_app()

    def action_stop(self) -> None:
        if self.app_program is not None:
            self.app_program.stop()
        self._enable_views(False)
        if self.link.is_open and self.hub_running:
            def done(_):
                self.hub_running = False
                self.log("stopped", "ok")
            self._background("Stopping hub...", lambda: hubio.stop(self.link), done)
        else:
            self.log("stopped", "ok")
        self._update_buttons()

    # ================================================================ link <-> app program
    def _send(self, protocol: str, data: str, ack: bool) -> None:
        if not self.link.is_open:
            self.log(f"not connected - dropped {protocol} {data}", "warn")
            return
        fut = self.link.send(protocol, data, ack)
        fut.add_done_callback(lambda f: f.exception() and self.bridge.post(
            lambda: self.log(f"{protocol} {data}: {f.exception()}", "error")))

    def _on_frame(self, frame: Frame) -> None:
        if frame.protocol == hubio.CONFIG_PROTOCOL:
            return
        prog = self.app_program
        if prog is None or not prog.running:
            return
        if frame.protocol not in prog.sockets:
            self.log(f"frame for unknown socket {frame.protocol}: {frame.data}", "warn")
            return
        prog.handle_frame(frame.protocol, frame.data)

    def _tick(self) -> None:
        if self.app_program is not None:
            self.app_program.tick(now_ms())

    def closeEvent(self, event) -> None:      # noqa: N802 (Qt name)
        if self.app_program is not None and self.app_program.running:
            self.app_program.stop()
        self.link.close()
        super().closeEvent(event)


def _field_label(f: dict) -> str:
    name = f.get("#name")
    t = f.get("type", "?")
    return f"{name}:{t}" if name else t


def _schema_label(sc: dict, si: int) -> str:
    consts = "".join(str(c.get("value")) for c in sc.get("constantArgs") or [])
    return sc.get("#comment") or (f"'{consts}'" if consts else f"schema {si}")


def _alive(w) -> bool:
    try:
        w.isEnabled()
        return True
    except RuntimeError:
        return False

