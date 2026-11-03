"""EditorPanel - the editor dock: the selected item as a form and as JSON.

The panel edits a copy. Apply (Ctrl+Enter) hands the edited JSON to the
window, which commits it to the Document as one undoable step; Revert
rebuilds the form from the committed item. The JSON tab edits the same
item as text (for anything the forms do not cover).
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from .fields import EditCtx, json_dumps
from .items import make_editor

KIND_TITLES = {"component": "Component", "function": "Function", "socket": "Socket", "globals": "Globals"}


class EditorPanel(QWidget):
    applied = Signal(object)          # the edited item JSON
    delete_requested = Signal()

    def __init__(self):
        super().__init__()
        self.kind: str | None = None
        self.side: str | None = None
        self.path: tuple | None = None
        self._orig = None
        self._ctx: EditCtx | None = None
        self.editor: QWidget | None = None
        self.dirty = False
        self._json_edited = False
        self._read_only = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600; font-size: 14px;")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)

        self.tabs = QTabWidget()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.tabs.addTab(self.scroll, "Form")
        self.json = QPlainTextEdit()
        self.json.setStyleSheet("font-family: Consolas, monospace;")
        self.json.textChanged.connect(self._json_changed)
        self.tabs.addTab(self.json, "JSON")
        self.tabs.currentChanged.connect(self._tab_changed)
        lay.addWidget(self.tabs, 1)

        bl = QHBoxLayout()
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setToolTip("Commit the changes (Ctrl+Enter)")
        self.btn_revert = QPushButton("Revert")
        self.btn_delete = QPushButton("Delete")
        self.btn_apply.clicked.connect(self.apply)
        self.btn_revert.clicked.connect(self.revert)
        self.btn_delete.clicked.connect(self.delete_requested)
        bl.addWidget(self.btn_apply)
        bl.addWidget(self.btn_revert)
        bl.addStretch(1)
        bl.addWidget(self.btn_delete)
        lay.addLayout(bl)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(self.apply)
        self.clear()

    # ---------------------------------------------------------------- content
    def clear(self) -> None:
        self.kind = self.side = self.path = None
        self._orig = None
        self._set_editor(None)
        self.title.setText("Nothing selected")
        hint = QLabel("Click a card or a row in the Hub, Link or App column to edit it.\n\n"
                      "Use the + buttons above the columns to add components, functions, sockets and globals.")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignTop)
        hint.setStyleSheet("color: #666; padding: 8px;")
        self.scroll.setWidget(hint)
        self._set_json("")
        self._set_dirty(False)
        self._update_buttons()

    def set_item(self, kind: str, side: str | None, path: tuple, value, ctx: EditCtx, title: str) -> None:
        """Show an item. Showing the same item again (after a commit / undo) keeps the
        tab and scroll position."""
        same = (kind, path) == (self.kind, self.path)
        bar = self.scroll.verticalScrollBar()
        pos, tab = bar.value(), self.tabs.currentIndex()
        self.kind, self.side, self.path = kind, side, path
        self._orig = value
        self._ctx = ctx
        self.title.setText(f"{KIND_TITLES.get(kind, kind)}: {title}")
        self._build(value)
        if same:
            self.tabs.setCurrentIndex(tab)
            QTimer.singleShot(0, lambda: bar.setValue(pos))     # after the new form is laid out
        else:
            self.tabs.setCurrentIndex(0)

    def _build(self, value) -> None:
        self._set_editor(make_editor(self.kind, self._ctx, value))
        self._set_json(json_dumps(value))
        self._set_dirty(False)
        self._update_buttons()

    def _set_editor(self, ed: QWidget | None) -> None:
        self.editor = ed
        if ed is not None:
            holder = QWidget()
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(2, 2, 8, 2)
            hl.addWidget(ed)
            hl.addStretch(1)
            ed.changed.connect(lambda: self._set_dirty(True))
            ed.setEnabled(not self._read_only)
            self.scroll.setWidget(holder)

    def _set_json(self, text: str) -> None:
        self.json.blockSignals(True)
        self.json.setPlainText(text)
        self.json.blockSignals(False)
        self._json_edited = False

    def _set_dirty(self, d: bool) -> None:
        self.dirty = d
        self.btn_apply.setEnabled(d and not self._read_only)
        self.btn_revert.setEnabled(d)
        name = self.title.text().removesuffix("  •")
        self.title.setText(name + ("  •" if d else ""))

    def _update_buttons(self) -> None:
        has = self.kind is not None
        self.tabs.setTabEnabled(1, has)
        self.btn_delete.setVisible(has and self.kind != "globals")
        self.btn_delete.setEnabled(has and not self._read_only)
        self.json.setReadOnly(self._read_only or not has)
        self.btn_apply.setEnabled(self.dirty and not self._read_only)

    def set_read_only(self, ro: bool) -> None:
        self._read_only = ro
        if self.editor is not None:
            self.editor.setEnabled(not ro)
        self._update_buttons()

    # ---------------------------------------------------------------- tabs
    def _json_changed(self) -> None:
        self._json_edited = True
        self._set_dirty(True)

    def _tab_changed(self, index: int) -> None:
        if self.editor is None:
            return
        if index == 1 and not self._json_edited:
            self._set_json(json_dumps(self.editor.value()))
        elif index == 0 and self._json_edited:
            try:
                v = json.loads(self.json.toPlainText())
            except ValueError as exc:
                QMessageBox.warning(self, "JSON", f"The JSON is not valid, so the form was not updated:\n{exc}")
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(1)
                self.tabs.blockSignals(False)
                return
            dirty = self.dirty
            self._set_editor(make_editor(self.kind, self._ctx, v))
            self._json_edited = False
            self._set_dirty(dirty)

    # ---------------------------------------------------------------- actions
    def current_value(self):
        """The edited item; raises ValueError for invalid JSON."""
        if self.tabs.currentIndex() == 1 and self._json_edited:
            return json.loads(self.json.toPlainText())
        return self.editor.value() if self.editor is not None else None

    def apply(self) -> None:
        if self.kind is None or self._read_only or not self.dirty:
            return
        try:
            v = self.current_value()
        except ValueError as exc:
            QMessageBox.warning(self, "Apply", f"The JSON is not valid:\n{exc}")
            return
        self.applied.emit(v)

    def revert(self) -> None:
        if self.kind is not None:
            self._build(self._orig)
