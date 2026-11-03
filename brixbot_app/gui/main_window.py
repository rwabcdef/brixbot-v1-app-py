"""BrixBot main window - the configuration screen of the concept sketch:

    Hardware (Select / Detect)   Configuration (Generate / Verify / Load)   Run / Stop
    [ Hub column ]   [ Link column ]   [ App column (live controls) ]  | Editor dock
    [ log ]

Editing: click a card or row to edit it in the Editor dock (form or JSON),
use the columns' + Add menus to add items, and right click for duplicate /
move / delete. Every change goes through the Document (undo / redo, dirty
flag) and is verified straight away; the cards of items with errors are
outlined red. Editing is locked while running.

All serial / hub work runs on worker threads; results come back through
the Bridge, so engine and widgets are only touched on the GUI thread.
"""

from __future__ import annotations

import copy
import html
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import hubio
from ..editor import controls, refs, templates
from ..editor.document import Document, get_at
from ..engine import errors as E
from ..engine.app_components import now_ms
from ..engine.builder import BINDING_KEY
from ..engine.project import HARDWARE_TYPES, Project, VerifyResult
from ..engine.runtime import Program
from ..engine.hardware import SLOT_KEY, Hardware, Slot, assign_slots, free_slots, load_hardware
from ..engine.specs import APP_SPECS, HUB_LIMITS, HUB_SPECS, HUB_UNSUPPORTED, SIDE_APP, SIDE_HUB
from ..serlink import Frame, Link
from .app_widgets import make_view
from .bridge import Bridge
from .cards import Card, Column
from .edit.fields import EditCtx
from .edit.panel import EditorPanel

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
SIDE_TITLES = {SIDE_HUB: "Hub", SIDE_APP: "App"}

# verify error section -> where the item lives
_SECTION_LIST = {E.SECTION_COMPONENTS: "components", E.SECTION_EVENTS: "components",
                 E.SECTION_FUNCTIONS: "functions"}


def item_kind(path: tuple) -> str | None:
    if len(path) == 2 and path[1] == "globals":
        return "globals"
    if len(path) == 2 and path[0] == "sockets":
        return "socket"
    if len(path) == 3 and path[1] == "components":
        return "component"
    if len(path) == 3 and path[1] == "functions":
        return "function"
    return None


class MainWindow(QMainWindow):
    def __init__(self, project_path: str | None = None):
        super().__init__()
        self.resize(1440, 900)
        self.bridge = Bridge(self)
        self.bridge.idle_hooks.append(self._tick)
        self.link = Link()
        self.link.add_any_listener(lambda f: self.bridge.post(lambda: self._on_frame(f)))
        self.link.set_traffic_callback(lambda d, line: self.bridge.post(lambda: self._on_traffic(d, line)))
        self.doc = Document()
        self.app_program: Program | None = None
        self.hub_loaded = False
        self.hub_running = False
        self.loaded_config: str | None = None      # the hub config last loaded into the hub
        self.busy = False
        self.selected: tuple | None = None
        self.verify_result: VerifyResult | None = None
        self._errors: dict[tuple, str] = {}
        self._views: list = []

        self._build_menu()
        self._build_ui()
        self._attach(self.doc)
        if project_path:
            self.open_project(project_path)
        else:
            self._doc_changed()
        self.log("BrixBot app ready - open a project (File > Open) or start a new one (File > New), "
                 "then connect a hub (Detect).")

    @property
    def project(self) -> Project:
        return self.doc.project

    @property
    def running(self) -> bool:
        return self.app_program is not None and self.app_program.running

    # ================================================================ UI
    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")
        for text, key, slot in (
            ("&New", QKeySequence.StandardKey.New, self.action_new),
            ("&Open...", QKeySequence.StandardKey.Open, self.action_open),
            ("&Reload from disk", "Ctrl+R", self.action_reload),
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

        e = self.menuBar().addMenu("&Edit")
        self.act_undo = QAction("&Undo", self)
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.triggered.connect(self.action_undo)
        self.act_redo = QAction("&Redo", self)
        self.act_redo.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Y")])
        self.act_redo.triggered.connect(self.action_redo)
        e.addAction(self.act_undo)
        e.addAction(self.act_redo)
        e.addSeparator()
        self.menu_add = {}
        for side in (SIDE_HUB, SIDE_APP):
            self.menu_add[side] = e.addMenu(f"Add to {SIDE_TITLES[side]}")
        self.menu_add["link"] = e.addMenu("Add to Link")

        h = self.menuBar().addMenu("&Hub")
        st = QAction("&Status", self)
        st.triggered.connect(self.action_status)
        h.addAction(st)
        dis = QAction("&Disconnect", self)
        dis.triggered.connect(self.disconnect)
        h.addAction(dis)
        self.menu_view = self.menuBar().addMenu("&View")

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
        self.btn_verify.clicked.connect(lambda: self.action_verify())
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
            c.on_click = self.select
            c.on_menu = self._item_menu
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setWidget(c)
            sa.setFrameShape(QScrollArea.Shape.NoFrame)
            colsl.addWidget(sa, 1)
        self._build_add_menus()
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
        split.setSizes([640, 180])
        root.addWidget(split, 1)
        self.setCentralWidget(central)

        self.panel = EditorPanel()
        self.panel.applied.connect(self._apply_edit)
        self.panel.delete_requested.connect(lambda: self.selected and self.delete_item(self.selected))
        self.dock = QDockWidget("Editor", self)
        self.dock.setObjectName("editor")
        self.dock.setWidget(self.panel)
        self.dock.setMinimumWidth(420)
        self.dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                              | QDockWidget.DockWidgetFeature.DockWidgetClosable
                              | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.resizeDocks([self.dock], [470], Qt.Orientation.Horizontal)
        self.menu_view.addAction(self.dock.toggleViewAction())

        self.status = self.statusBar()
        self.verify_label = QLabel()
        self.verify_label.setStyleSheet("padding: 0 8px;")
        self.status.addPermanentWidget(self.verify_label)
        self._update_hw_title()

    def _build_add_menus(self) -> None:
        for side, col in ((SIDE_HUB, self.col_hub), (SIDE_APP, self.col_app)):
            specs = HUB_SPECS if side == SIDE_HUB else APP_SPECS
            for menu in (col.add_menu, self.menu_add[side]):
                cm = menu.addMenu("Component")
                if side == SIDE_HUB:
                    cm.aboutToShow.connect(lambda cm=cm: self._fill_hub_component_menu(cm))
                else:
                    for t in specs:
                        cm.addAction(t, lambda s=side, t=t: self.add_component(s, t))
                fm = menu.addMenu("Function")
                fm.addAction("basic (list of operations)", lambda s=side: self.add_function(s, "basic"))
                fm.addAction("conditional (if / elif / else)", lambda s=side: self.add_function(s, "conditional"))
                menu.addAction("Globals...", lambda s=side: self.select((s, "globals")))
        for menu in (self.col_link.add_menu, self.menu_add["link"]):
            menu.addAction("Socket", self.add_socket)
        for menu in (self.col_hub.view_menu, self.menu_view):
            menu.addAction("Available components...", self.show_available)
        self.col_hub.show_view()

    def _fill_hub_component_menu(self, menu: QMenu) -> None:
        """One entry per hardware slot ("led D3"); used slots greyed out."""
        menu.clear()
        hw = self.hardware
        comps = self.doc.get(("hub", "components")) or []
        for t in HUB_SPECS:
            slots = hw.slots.get(t, []) if hw else []
            if not slots:
                menu.addAction(t, lambda t=t: self.add_component(SIDE_HUB, t))
                continue
            free = {s.index for s in free_slots(hw, comps, t)}
            sub = menu.addMenu(t)
            sub.setEnabled(bool(free))
            for s in slots:
                a = sub.addAction(s.label, lambda t=t, s=s: self.add_component(SIDE_HUB, t, s))
                a.setEnabled(s.index in free)
        for t in HUB_UNSUPPORTED:
            menu.addAction(f"{t} (no hub driver yet)").setEnabled(False)

    @property
    def hardware(self) -> Hardware | None:
        return load_hardware(self.project.hardware)

    # ================================================================ log
    def log(self, msg: str, kind: str = "info") -> None:
        color = {"info": None, "ok": "#2a9d3a", "error": "#d03030", "warn": "#c07000",
                 "tx": "#3070c0", "rx": "#8040a0"}.get(kind)
        text = f"{time.strftime('%H:%M:%S')}  {html.escape(msg)}"
        self.log_box.appendHtml(f'<span style="color:{color}">{text}</span>' if color else text)

    def _on_traffic(self, direction: str, line: str) -> None:
        if self.chk_frames.isChecked():
            self.log(("-> " if direction == "tx" else "<- ") + line, direction)

    def ask(self, title: str, text: str, buttons, default):
        """A question box (tests replace this)."""
        return QMessageBox.question(self, title, text, buttons, default)

    # ================================================================ state
    def _update_hw_title(self) -> None:
        hw = HARDWARE_TYPES.get(self.project.hardware, self.project.hardware)
        port = self.link.port_name if self.link.is_open else "not connected"
        self.hw_box.setTitle(f"Hardware: {hw} - {port}")

    def _update_title(self) -> None:
        name = self.project.path.name if self.project.path else "untitled"
        self.setWindowTitle(f"BrixBot - {name}{' *' if self.doc.dirty else ''}")

    def _update_buttons(self) -> None:
        idle = not self.busy
        connected = self.link.is_open
        running = self.running
        self.btn_select.setEnabled(idle and not running)
        self.btn_detect.setEnabled(idle and not running)
        self.btn_generate.setEnabled(idle)
        self.btn_verify.setEnabled(idle)
        self.btn_load.setEnabled(idle and connected and not running)
        self.btn_run.setEnabled(idle and not running and self.app_program is not None)
        self.btn_stop.setEnabled(idle and running)
        editable = not running
        for col in (self.col_hub, self.col_link, self.col_app):
            col.add_btn.setEnabled(editable)
        for menu in self.menu_add.values():
            menu.setEnabled(editable)
        self.act_undo.setEnabled(editable and self.doc.can_undo())
        self.act_redo.setEnabled(editable and self.doc.can_redo())
        self.act_undo.setText(f"&Undo {self.doc.undo_label()}".rstrip())
        self.act_redo.setText(f"&Redo {self.doc.redo_label()}".rstrip())
        self.panel.set_read_only(running)
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

    # ================================================================ document
    def _attach(self, doc: Document) -> None:
        self.doc = doc
        doc.observers.append(self._doc_changed)

    def _doc_changed(self) -> None:
        """After any change (edit, undo, open): rebuild the app side, the columns, verify."""
        if not self.running:
            self._rebuild_app()
        self._live_verify()
        self._populate()
        self._refresh_panel()
        self._update_title()
        self._update_buttons()

    def _rebuild_app(self) -> None:
        self.app_program = None
        try:
            self.app_program = self.project.build_app()
            self.app_program.sender = self._send
            self.app_program.on_error = lambda m: self.log(m, "warn")
        except E.BuildError:
            pass        # reported by verify

    def _live_verify(self) -> None:
        res = self.project.verify()
        self.verify_result = res
        errs: dict[tuple, str] = {}
        for side, exc in res.issues:
            if exc.section in _SECTION_LIST:
                p = (side, _SECTION_LIST[exc.section], exc.index)
            elif exc.section in (E.SECTION_SOCKETS, E.SECTION_BINDINGS):
                p = ("sockets", exc.index)
            elif exc.section == E.SECTION_GLOBALS:
                p = (side, "globals")
            else:
                continue
            errs[p] = exc.describe(side) + self.project.locate(side, exc)
        self._errors = errs
        limit = HUB_LIMITS["config_len"]
        if res.ok:
            self.verify_label.setText(f"✓ verified - hub config {res.hub_config_len} / {limit} chars")
            self.verify_label.setStyleSheet("padding: 0 8px; color: #2a9d3a;")
            self.verify_label.setToolTip("")
        else:
            n = len(res.errors)
            self.verify_label.setText(f"✗ {n} error{'s' if n > 1 else ''}: {res.errors[0][:90]}")
            self.verify_label.setStyleSheet("padding: 0 8px; color: #d03030;")
            self.verify_label.setToolTip("\n\n".join(res.errors))

    def _confirm_discard(self, what: str) -> bool:
        """True when it is fine to drop the current project (saved, or the user agreed)."""
        if self.panel.dirty:
            r = self.ask(what, "Apply the change in the editor first?",
                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                         | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
            if r == QMessageBox.StandardButton.Cancel:
                return False
            if r == QMessageBox.StandardButton.Yes:
                self.panel.apply()
        if not self.doc.dirty:
            return True
        r = self.ask(what, "The project has unsaved changes. Save them first?",
                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                     | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Save:
            return self.action_save()
        return True

    def set_document(self, doc: Document) -> None:
        if self.running:
            self.action_stop()
        self.selected = None
        self.panel.clear()
        self._attach(doc)
        self._doc_changed()

    # ================================================================ file
    def action_new(self) -> None:
        if not self._confirm_discard("New project"):
            return
        self.set_document(Document(Project(templates.new_project())))
        self.log("new project")

    def action_open(self) -> None:
        if not self._confirm_discard("Open project"):
            return
        start = str(self.project.path.parent if self.project.path else EXAMPLES)
        path, _ = QFileDialog.getOpenFileName(self, "Open BrixBot project", start, "BrixBot project (*.json)")
        if path:
            self.open_project(path)

    def action_reload(self) -> None:
        if self.project.path and self._confirm_discard("Reload project"):
            self.open_project(str(self.project.path))

    def action_save(self) -> bool:
        if self.project.path is None:
            return self.action_save_as()
        self.project.save()
        self.doc.mark_saved()
        self._update_title()
        self.log(f"saved {self.project.path}", "ok")
        return True

    def action_save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save BrixBot project", str(self.project.path or ""),
                                              "BrixBot project (*.json)")
        if not path:
            return False
        self.project.save(path)
        self.doc.mark_saved()
        self._update_title()
        self.log(f"saved {path}", "ok")
        return True

    def open_project(self, path: str) -> None:
        try:
            project = Project.load(path)
        except Exception as exc:
            self.log(f"cannot open {path}: {exc}", "error")
            QMessageBox.warning(self, "Open project", f"Cannot open {path}:\n{exc}")
            return
        self.hub_loaded = False
        self.set_document(Document(project))
        self.log(f"opened {path}")
        if self.verify_result and not self.verify_result.ok:
            for e in self.verify_result.errors:
                self.log(e, "error")

    # ================================================================ edit
    def action_undo(self) -> None:
        if not self.running and self.doc.can_undo():
            self.log(f"undo {self.doc.undo()}")

    def action_redo(self) -> None:
        if not self.running and self.doc.can_redo():
            self.log(f"redo {self.doc.redo()}")

    def _ctx(self, side: str | None, path: tuple | None = None) -> EditCtx:
        index = path[-1] if path and isinstance(path[-1], int) else None
        on_control = None
        if path is not None and item_kind(path) == "component":
            on_control = lambda action, cid, target, p=path: self.control_action(p, action, cid, target)  # noqa: E731
        return EditCtx(self.doc.data, side or SIDE_HUB, index=index, on_control=on_control)

    def _title_of(self, path: tuple) -> str:
        kind = item_kind(path)
        v = self.doc.get(path)
        if kind == "globals":
            return f"{SIDE_TITLES[path[0]]} globals"
        if not isinstance(v, dict):
            return str(path)
        if kind == "socket":
            return str(v.get("protocol"))
        if kind == "component":
            name = v.get("#name")
            return f"{SIDE_TITLES[path[0]]} {v.get('type')} {v.get('id')}" + (f" - {name}" if name else "")
        name = v.get("#name")
        return f"{SIDE_TITLES[path[0]]} #{v.get('id')}" + (f" - {name}" if name else "")

    def select(self, path: tuple | None) -> None:
        if path is not None and item_kind(path) is None:
            return
        if path != self.selected and self.panel.dirty:
            r = self.ask("Editor", "Apply the change to the item being edited?",
                         QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Discard
                         | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Apply)
            if r == QMessageBox.StandardButton.Cancel:
                return
            if r == QMessageBox.StandardButton.Apply:
                self.panel.apply()
        self.selected = path
        if path is None:
            self.panel.clear()
        self._refresh_panel()
        self._mark()
        if path is not None and not self.dock.isVisible():
            self.dock.show()

    def _refresh_panel(self) -> None:
        p = self.selected
        if p is None:
            self._mark()
            return
        kind = item_kind(p)
        v = self.doc.get(p)
        if kind == "globals":
            v = v if isinstance(v, list) else []
        elif not isinstance(v, dict):
            self.selected = None
            self.panel.clear()
            self._mark()
            return
        side = None if kind == "socket" else p[0]
        self.panel.set_item(kind, side, p, copy.deepcopy(v), self._ctx(side, p), self._title_of(p))
        self.panel.set_read_only(self.running)
        self._mark()

    def _mark(self) -> None:
        for col in (self.col_hub, self.col_link, self.col_app):
            col.mark(self.selected, self._errors)

    def _apply_edit(self, value) -> None:
        path = self.selected
        kind = self.panel.kind
        if path is None or self.running:
            return
        if kind == "globals":
            if not isinstance(value, list):
                QMessageBox.warning(self, "Apply", "Globals must be a JSON list.")
                return
        elif not isinstance(value, dict):
            QMessageBox.warning(self, "Apply", "The item must be a JSON object.")
            return
        gmap = self.panel.editor.index_map() if kind == "globals" and hasattr(self.panel.editor, "index_map") \
            and self.panel.tabs.currentIndex() == 0 else None
        notes: list[str] = []

        def mutate(d):
            old = get_at(d, path)
            side = path[0]
            if kind == "function" and isinstance(old, dict) and not refs.same_id(old.get("id"), value.get("id")):
                n = refs.rename_function(d, side, old.get("id"), value.get("id"))
                if n:
                    notes.append(f"updated {n} binding(s) to function {value.get('id')}")
            elif kind == "component" and isinstance(old, dict):
                o, nw = (old.get("type"), old.get("id")), (value.get("type"), value.get("id"))
                if o != nw:
                    n = refs.rename_component(d, side, o, nw)
                    if n:
                        notes.append(f"updated {n} command(s) to {nw[0]} {nw[1]}")
            elif kind == "socket" and isinstance(old, dict) and old.get("protocol") != value.get("protocol"):
                n = refs.rename_socket(d, old.get("protocol"), value.get("protocol"))
                if n:
                    notes.append(f"updated {n} message(s) to socket {value.get('protocol')}")
            elif kind == "globals" and gmap and any(k != v for k, v in gmap.items()):
                refs.reindex_globals(d, side, gmap)
                notes.append("updated global references")
            get_at(d, path[:-1])[path[-1]] = value

        self.doc.apply(f"edit {self._title_of(path)}", mutate)
        self.panel._set_dirty(False)
        for n in notes:
            self.log(n)

    # ---------------------------------------------------------------- add / delete / move
    def _can_edit(self) -> bool:
        if self.running:
            self.log("stop running before editing", "warn")
            return False
        return True

    def _insert(self, list_path: tuple, item: dict, label: str, index: int | None = None) -> None:
        if not self._can_edit():
            return
        if self.panel.dirty:
            self.select(self.selected)        # offers to apply the pending edit
            if self.panel.dirty:
                return
        p = self.doc.insert(list_path, item, index, label)
        self.select(p)

    def add_component(self, side: str, ctype: str, slot: Slot | None = None) -> None:
        """Add a component; a hub component takes the given hardware slot, or the first free one."""
        comps = self.doc.get((side, "components")) or []
        hw = self.hardware if side == SIDE_HUB else None
        if hw is not None and ctype in hw.slots:
            free = free_slots(hw, comps, ctype)
            if slot is None:
                slot = free[0] if free else None
            if slot is None or slot.index not in {s.index for s in free}:
                self.log(f"no free {ctype} slot on the hub", "warn")
                return
        self._insert((side, "components"), templates.new_component(side, ctype, comps, slot), f"add {ctype}")

    def add_function(self, side: str, kind: str) -> None:
        fns = self.doc.get((side, "functions")) or []
        self._insert((side, "functions"), templates.new_function(kind, fns), "add function")

    def add_socket(self) -> None:
        self._insert(("sockets",), templates.new_socket(self.doc.get(("sockets",)) or []), "add socket")

    def duplicate_item(self, path: tuple) -> None:
        kind = item_kind(path)
        v = copy.deepcopy(self.doc.get(path))
        if not isinstance(v, dict):
            return
        lst = self.doc.get(path[:-1]) or []
        if kind == "component":
            v["id"] = templates.free_component_id(lst, v.get("type"))
            v.pop("events", None)
            hw = self.hardware if path[0] == SIDE_HUB else None
            if hw is not None and v.get("type") in hw.slots:
                free = free_slots(hw, lst, v.get("type"))
                if not free:
                    self.log(f"no free {v.get('type')} slot on the hub", "warn")
                    return
                v[SLOT_KEY] = free[0].index
        elif kind == "function":
            v["id"] = templates.next_function_id(lst)
        elif kind == "socket":
            v["protocol"] = templates.free_protocol(lst)
            for sc in v.get("schemas") or []:
                if isinstance(sc, dict):
                    sc.pop("functions", None)
                    sc.pop("appFunctions", None)
        if v.get("#name"):
            v["#name"] = f"{v['#name']} copy"
        self._insert(path[:-1], v, f"duplicate {self._title_of(path)}", path[-1] + 1)

    def delete_item(self, path: tuple) -> None:
        if not self._can_edit():
            return
        kind = item_kind(path)
        v = self.doc.get(path)
        if kind not in ("component", "function", "socket") or not isinstance(v, dict):
            return
        d = self.doc.data
        if kind == "function":
            users = refs.function_users(d, path[0], v.get("id"))
        elif kind == "component":
            users = refs.component_users(d, path[0], v.get("type"), v.get("id"))
        else:
            users = refs.socket_users(d, v.get("protocol"))
        text = f"Delete {self._title_of(path)}?"
        if users:
            text += "\n\nStill used by:\n  " + "\n  ".join(users) + "\n\nThose will show as errors until fixed."
        r = self.ask("Delete", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                     QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        label = f"delete {self._title_of(path)}"
        self.selected = None
        self.panel.clear()
        self.doc.remove(path, label)

    def move_item(self, path: tuple, delta: int) -> None:
        if not self._can_edit():
            return
        was = self.selected == path
        new = self.doc.move(path, delta, f"move {self._title_of(path)}")
        if was:
            self.selected = new
            self._refresh_panel()

    # ---------------------------------------------------------------- controls
    def control_action(self, path: tuple, action: str, command_id: int, target) -> None:
        """From a component's editor: connect a socket schema to a command ("connect"), disconnect one
        ("disconnect"), or make a new function for an event ("new_handler", command_id = event id)."""
        if not self._can_edit():
            return
        if self.panel.dirty:
            r = self.ask("Controls", "Apply the change to this component first?",
                         QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel,
                         QMessageBox.StandardButton.Apply)
            if r != QMessageBox.StandardButton.Apply:
                return
            self.panel.apply()
            if self.panel.dirty:
                return
        side = path[0]
        comp = self.doc.get(path)
        spec = (HUB_SPECS if side == SIDE_HUB else APP_SPECS).get(comp.get("type")) if isinstance(comp, dict) else None
        if action == "new_handler":
            es = spec.event(command_id) if spec else None
            if es is None:
                return
            fid, notes = self.doc.apply(f"new handler for {es.name}",
                                        lambda d: controls.new_event_handler(d, side, path[2], es))
            for n in notes:
                self.log(n)
            fns = self.doc.get((side, "functions")) or []
            fi = next((i for i, f in enumerate(fns) if isinstance(f, dict) and refs.same_id(f.get("id"), fid)), None)
            if fi is not None:
                self.select((side, "functions", fi))
            return
        cmd = spec.command(command_id) if spec else None
        if cmd is None:
            return
        notes: list[str] = []
        name = comp.get("#name") or f"{comp.get('type')} {comp.get('id')}"
        if action == "connect":
            picked = self.pick_control_target(f"Connect a socket to {name} {cmd.name}")
            if picked is None:
                return
            sock, schema = picked
            self.doc.apply(f"connect {name} {cmd.name}",
                           lambda d: notes.extend(controls.connect(d, side, comp, cmd, sock, schema)))
        elif action == "disconnect" and isinstance(target, controls.Connection):
            self.doc.apply(f"disconnect {name} {cmd.name}",
                           lambda d: notes.extend(controls.disconnect(d, side, target)))
        for n in notes:
            self.log(n)

    def pick_control_target(self, title: str) -> tuple[int | None, int | None] | None:
        """Ask for (socket index or None = new, schema index or None = new); None = cancelled.
        Tests replace this."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Connect socket")
        form = QFormLayout(dlg)
        form.addRow(QLabel(title))
        sockets = [(i, s) for i, s in enumerate(self.project.sockets) if isinstance(s, dict)]
        sock = QComboBox()
        sock.addItem("new socket", None)
        for i, s in sockets:
            sock.addItem(str(s.get("protocol")) + (f" - {s['#name']}" if s.get("#name") else ""), i)
        schema = QComboBox()

        def fill():
            schema.clear()
            schema.addItem("new schema", None)
            i = sock.currentData()
            if i is None:
                return
            for si, sc in enumerate(self.project.sockets[i].get("schemas") or []):
                if isinstance(sc, dict):
                    schema.addItem(_schema_label(sc, si), si)
        sock.currentIndexChanged.connect(fill)
        fill()
        form.addRow("Socket", sock)
        form.addRow("Schema", schema)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return sock.currentData(), schema.currentData()

    # ---------------------------------------------------------------- available components
    def available_rows(self) -> list[tuple[Slot, str]]:
        """(slot, used by) for every hardware slot; used by is "" when free."""
        hw = self.hardware
        if hw is None:
            return []
        comps = self.doc.get(("hub", "components")) or []
        users = {}
        for i, sl in assign_slots(hw, comps).items():
            if sl is not None:
                c = comps[i]
                users[(sl.type, sl.index)] = c.get("#name") or f"{c.get('type')} {c.get('id')}"
        return [(sl, users.get((t, sl.index), "")) for t, lst in hw.slots.items() for sl in lst]

    def show_available(self) -> None:
        hw = self.hardware
        dlg = QDialog(self)
        dlg.setWindowTitle("Available components")
        dlg.resize(760, 420)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"Components on the hub: {hw.name if hw else self.project.hardware}"))
        rows = self.available_rows()
        table = QTableWidget(len(rows), 5)
        table.setHorizontalHeaderLabels(["Type", "Pin / slot", "Used by", "Controls (commands)", "Events"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        for r, (sl, user) in enumerate(rows):
            spec = HUB_SPECS.get(sl.type)
            cells = [sl.type, sl.pin or f"#{sl.index + 1}", user or "free",
                     ", ".join(c.name for c in spec.commands) if spec else "",
                     ", ".join(e.name for e in spec.events) if spec else ""]
            for c, text in enumerate(cells):
                table.setItem(r, c, QTableWidgetItem(text))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        add = bb.addButton("Add selected", QDialogButtonBox.ButtonRole.ActionRole)
        add.setEnabled(False)

        def sel_changed():
            r = table.currentRow()
            add.setEnabled(0 <= r < len(rows) and not rows[r][1] and not self.running)
        table.itemSelectionChanged.connect(sel_changed)

        def do_add():
            sl = rows[table.currentRow()][0]
            dlg.accept()
            self.add_component(SIDE_HUB, sl.type, sl)
        add.clicked.connect(do_add)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def _item_menu(self, path: tuple, pos) -> None:
        kind = item_kind(path)
        if kind is None:
            return
        menu = QMenu(self)
        menu.addAction("Edit", lambda: self.select(path))
        if kind != "globals":
            editable = not self.running
            for text, fn in (("Duplicate", lambda: self.duplicate_item(path)),
                             ("Move up", lambda: self.move_item(path, -1)),
                             ("Move down", lambda: self.move_item(path, 1)),
                             (None, None),
                             ("Delete", lambda: self.delete_item(path))):
                if text is None:
                    menu.addSeparator()
                else:
                    menu.addAction(text, fn).setEnabled(editable)
        menu.exec(pos)

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
        self._enable_views(self.running)
        self._mark()

    def _populate_side(self, col: Column, side: str, section: dict, program: Program | None) -> None:
        specs = HUB_SPECS if side == SIDE_HUB else APP_SPECS
        fn_list = section.get("functions") if isinstance(section.get("functions"), list) else []
        funcs = {str(f.get("id")): f.get("#name") or f"function {f.get('id')}"
                 for f in fn_list if isinstance(f, dict)}
        globals_ = section.get("globals") if isinstance(section.get("globals"), list) else []
        handlers: list[tuple[str, tuple]] = []
        comps = section.get("components") if isinstance(section.get("components"), list) else []
        hw = self.hardware if side == SIDE_HUB else None
        slots = assign_slots(hw, comps)
        controlled: set[tuple[int, int, int]] = set()      # (socket, schema, binding) shown in a card
        for ci, c in enumerate(comps):
            if not isinstance(c, dict):
                continue
            path = (side, "components", ci)
            spec = specs.get(c.get("type"))
            title = c.get("#name") or f"{c.get('type')} {c.get('id')}"
            if c.get("#name"):
                title += f"  ({c.get('type')} {c.get('id')})"
            sl = slots.get(ci)
            if sl is not None and sl.pin and sl.pin not in title:
                title += f"  [{sl.pin}]"
            card = Card(title, "component", path=path, tooltip=c.get("#comment", ""))
            if program is not None:
                model = program.resolve(c.get("type"), c.get("id"))
                if model is not None:
                    view = make_view(model)
                    card.add_widget(view)
                    self._views.append(view)
            if spec is None:
                card.add_row("not supported")
                col.add(card)
                continue
            rows: list[str] = []
            for ev in c.get("events") or []:
                es = spec.event(ev.get("eventId")) if isinstance(ev, dict) else None
                targets = [funcs.get(str(b.get("functionId")), f"#{b.get('functionId')}")
                           for b in ev.get("functions") or [] if isinstance(b, dict)]
                if es and targets:
                    rows.append(f"on {es.name} → {', '.join(targets)}")
            for cmd, conn in controls.all_connections(self.doc.data, side, c, spec):
                controlled.add((conn.socket, conn.schema, conn.binding))
                rows.append(f"{cmd.name} ← {conn.label}")
            if spec.commands and not any("←" in r for r in rows):
                card.add_row("controls: " + ", ".join(cmd.name for cmd in controls.control_commands(spec))
                             if controls.control_commands(spec) else "no controls")
            if rows:
                sec = card.add_section("handler", "Handlers")
                for r in rows:
                    sec.add_row(r, path=path)
            elif spec.events:
                card.add_row("events: " + ", ".join(e.name for e in spec.events))
            col.add(card)
        # socket messages received by this side, not already shown as a component's control
        key = BINDING_KEY[side]
        for si_, s in enumerate(self.project.sockets):
            if not isinstance(s, dict):
                continue
            for si, sc in enumerate(s.get("schemas") or []):
                if not isinstance(sc, dict):
                    continue
                targets = [funcs.get(str(b.get("functionId")), f"#{b.get('functionId')}")
                           for bi, b in enumerate(sc.get(key) or [])
                           if isinstance(b, dict) and (si_, si, bi) not in controlled]
                if targets:
                    handlers.append((f"{s.get('protocol')} {_schema_label(sc, si)} → {', '.join(targets)}",
                                     ("sockets", si_)))
        if globals_:
            g = Card("Globals", "global", path=(side, "globals"))
            for gi, v in enumerate(globals_):
                if isinstance(v, dict):
                    name = v.get("#name") or f"global {gi}"
                    val = f" = {v.get('value')}" if v.get("value") is not None else ""
                    g.add_row(f"{name}: {v.get('type')}{val}")
            col.add(g)
        if handlers:
            h = Card("Socket handlers", "handler")
            for text, p in handlers:
                h.add_row(text, path=p)
            col.add(h)
        if fn_list:
            f = Card("Functions", "function")
            for fi, fn in enumerate(fn_list):
                if isinstance(fn, dict):
                    f.add_row(fn.get("#name") or f"function {fn.get('id')}", path=(side, "functions", fi),
                              tooltip=refs.function_text(fn, side, globals_))
            col.add(f)
        if not comps and not fn_list and not globals_:
            col.add(Card("(empty)", "global", ["Use + Add to add components and functions."]))

    def _populate_link(self) -> None:
        for i, s in enumerate(self.project.sockets):
            if not isinstance(s, dict):
                continue
            title = f"Socket: {s.get('protocol')}" + (f"  - {s['#name']}" if s.get("#name") else "")
            card = Card(title, "socket", tooltip=s.get("#comment", ""), path=("sockets", i))
            common = "".join(f"&lt;{html.escape(_field_label(f))}&gt;" for f in s.get("commonArgs") or []
                             if isinstance(f, dict))
            for si, sc in enumerate(s.get("schemas") or []):
                if not isinstance(sc, dict):
                    continue
                hub_rx = bool(sc.get("functions"))
                app_rx = bool(sc.get("appFunctions"))
                arrow = "app → hub" if hub_rx and not app_rx else ("hub → app" if app_rx and not hub_rx else "↔")
                consts = html.escape("".join(str(c.get("value")) for c in sc.get("constantArgs") or []
                                             if isinstance(c, dict)))
                args = "".join(f"&lt;{html.escape(_field_label(f))}&gt;" for f in sc.get("args") or []
                               if isinstance(f, dict))
                label = sc.get("#comment")
                row = f"{arrow}&nbsp;&nbsp; <b>{common}{consts}{args}</b>"
                card.add_row(f"{row}<br><i>{html.escape(label)}</i>" if label else row, rich=True)
            self.col_link.add(card)
        if not self.project.sockets:
            self.col_link.add(Card("(no sockets)", "global",
                                   ["Sockets carry messages between hub and app. Use + Add."]))

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
        if hw.currentData() != self.project.hardware:
            self.doc.apply("change hardware", lambda d: d.__setitem__("hardware", hw.currentData()))
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
    def _apply_pending(self) -> None:
        """Generate / Verify / Load / Run work on the committed project: offer to apply an open edit."""
        if self.panel.dirty and not self.running:
            r = self.ask("Editor", "Apply the change in the editor first?",
                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                         QMessageBox.StandardButton.Yes)
            if r == QMessageBox.StandardButton.Yes:
                self.panel.apply()

    def action_verify(self, quiet: bool = False) -> bool:
        if not quiet:
            self._apply_pending()
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
        self._apply_pending()
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
        copy_b = bb.addButton("Copy", QDialogButtonBox.ButtonRole.ActionRole)
        save = bb.addButton("Save...", QDialogButtonBox.ButtonRole.ActionRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        copy_b.clicked.connect(lambda: QGuiApplication.clipboard().setText(text))
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
        self._apply_pending()
        if not self.action_verify(quiet=True):
            QMessageBox.warning(self, "Load", "The project has errors - see the log.")
            return
        text = self.project.generate()

        def progress(n, total):
            self.bridge.post(lambda: self.status.showMessage(f"Loading hub config... {n}/{total}"))

        def done(res):
            self.hub_loaded = res.ok
            self.hub_running = False
            self.loaded_config = text if res.ok else None
            self.log(res.message, "ok" if res.ok else "error")
            if not res.ok:
                QMessageBox.warning(self, "Load", res.message)

        self._background("Loading hub config...", lambda: hubio.load(self.link, text, progress), done)

    # ================================================================ run
    def action_run(self) -> None:
        self._apply_pending()
        if self.app_program is None:
            self.log("the app side has errors - fix them before running", "error")
            return

        def start_app(_=None):
            self.app_program.start()
            self._enable_views(True)
            self.log("running", "ok")
            self._update_buttons()

        if self.link.is_open and self.hub_loaded:
            if self.loaded_config is not None and self.loaded_config != self.project.generate():
                self.log("the hub part changed since Load - Load again to send the changes to the hub", "warn")
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
        if not self._confirm_discard("Exit"):
            event.ignore()
            return
        if self.running:
            self.app_program.stop()
        self.link.close()
        super().closeEvent(event)


def _field_label(f: dict) -> str:
    name = f.get("#name")
    t = f.get("type", "?")
    return f"{name}:{t}" if name else t


def _schema_label(sc: dict, si: int) -> str:
    consts = "".join(str(c.get("value")) for c in sc.get("constantArgs") or [] if isinstance(c, dict))
    return sc.get("#comment") or (f"'{consts}'" if consts else f"schema {si}")


def _alive(w) -> bool:
    try:
        w.isEnabled()
        return True
    except RuntimeError:
        return False
