"""Reusable form widgets for the project editor.

Each widget has a `changed` signal and a value() method returning JSON.

* ValueEdit   - a typed value in a line edit; unchanged text returns the
                original value exactly (so "1" stays a string, 2 stays int).
* FixedEdit   - a fixed value: bare (type inferred like the hub) or typed.
* LiveCombo   - a combo whose choices come from a provider, refreshed each
                time it opens, so it follows edits elsewhere in the form.
* RefEdit     - an ArgRef: function arg / temp var / cond var / global /
                fixed value (and optionally a command result or nothing).
* ListEditor  - a list of row editors with add / remove / move.
* SizedList   - a fixed-length list whose length and labels are set by code
                (command inArgs, message args, binding args).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from ...engine.specs import APP_SPECS, HUB_SPECS, SIDE_HUB
from ...engine.variable import TYPE_NAMES

VALUE_TYPES = [t for t in TYPE_NAMES if t not in ("none", "component")]
VAR_TYPES = [t for t in TYPE_NAMES if t != "none"]
AUTO = "auto"


# ================================================================== context
def _dicts(v) -> list[dict]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


@dataclass
class EditCtx:
    """What an editor needs to know about the rest of the project.

    data is the committed project (read only). fn() returns the function
    being edited right now (live), for arg / temp / cond var choices.
    index is the edited item's index in its list (None for a new item).
    on_control(action, id, target) is called by a component's editor:
    "connect" / "disconnect" (Controls, id = command id) and "new_handler"
    (Events, id = event id); None hides those buttons.
    """
    data: dict
    side: str = SIDE_HUB
    fn: Callable[[], dict] = field(default=lambda: {})
    index: int | None = None
    on_control: Callable[[str, int, Any], None] | None = None

    @property
    def specs(self):
        return HUB_SPECS if self.side == SIDE_HUB else APP_SPECS

    def section(self, side: str | None = None) -> dict:
        s = self.data.get(side or self.side)
        return s if isinstance(s, dict) else {}

    def components(self, side: str | None = None) -> list[dict]:
        return _dicts(self.section(side).get("components"))

    def functions(self, side: str | None = None) -> list[dict]:
        return _dicts(self.section(side).get("functions"))

    def globals(self, side: str | None = None) -> list[dict]:
        return _dicts(self.section(side).get("globals"))

    def sockets(self) -> list[dict]:
        return _dicts(self.data.get("sockets"))

    def with_side(self, side: str) -> EditCtx:
        return replace(self, side=side)

    def with_fn(self, fn: Callable[[], dict]) -> EditCtx:
        return replace(self, fn=fn)


def var_label(v: dict, i: int, prefix: str) -> str:
    name = v.get("#name") if isinstance(v, dict) else None
    t = v.get("type") if isinstance(v, dict) else None
    base = f"{i}: {name}" if name else f"{i}: {prefix}{i}"
    return f"{base} ({t})" if t else base


def merge(orig: dict, managed: dict) -> dict:
    """orig with managed keys overlaid. A managed None removes the key; an
    empty list is only written when the key was there before."""
    out = dict(orig)
    for k, v in managed.items():
        if v is None or (v == [] and k not in orig):
            out.pop(k, None)
        else:
            out[k] = v
    return out


# ================================================================== layout helpers
def form_widget() -> tuple[QWidget, QFormLayout]:
    w = QWidget()
    f = QFormLayout(w)
    f.setContentsMargins(0, 0, 0, 0)
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return w, f


def hbox(*widgets, stretch_last: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for i, x in enumerate(widgets):
        lay.addWidget(x, 1 if (stretch_last and i == len(widgets) - 1) else 0)
    if not stretch_last:
        lay.addStretch(1)
    return w


def section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-weight: 600; margin-top: 6px;")
    return lbl


def hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #777;")
    return lbl


class TextEdit(QLineEdit):
    """A line edit for an optional string; value() is None when empty."""
    changed = Signal()

    def __init__(self, text=None, placeholder: str = "", max_len: int | None = None):
        super().__init__("" if text is None else str(text))
        self.setPlaceholderText(placeholder)
        if max_len:
            self.setMaxLength(max_len)
        self.textChanged.connect(lambda _: self.changed.emit())

    def value(self):
        return self.text() or None


# ================================================================== values
def _fmt(v, vtype: str | None) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        if vtype in (None, AUTO) and _parse_auto(v) != v:
            return json.dumps(v)          # a string that would read back as a number / bool
        return v
    return str(v)


def _parse_auto(text: str):
    t = text.strip()
    if t in ("true", "false"):
        return t == "true"
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return json.loads(text)
        except ValueError:
            pass
    return text


def parse_value(text: str, vtype: str | None):
    """Text -> JSON value for a type (None / 'auto' = infer). Raises ValueError."""
    if vtype in (None, AUTO):
        if text == "":
            raise ValueError("empty")
        return _parse_auto(text)
    t = text.strip()
    if vtype == "bool":
        if t.lower() in ("true", "1", "on", "yes"):
            return True
        if t.lower() in ("false", "0", "off", "no"):
            return False
        raise ValueError("true / false")
    if vtype in ("uint8", "uint16", "int8", "int16"):
        n = int(t)
        lo, hi = {"uint8": (0, 255), "uint16": (0, 65535), "int8": (-128, 127), "int16": (-32768, 32767)}[vtype]
        if not lo <= n <= hi:
            raise ValueError(f"{lo}..{hi}")
        return n
    if vtype == "float":
        return float(t)
    if vtype == "char":
        if len(text) != 1:
            raise ValueError("one character")
        return text
    if vtype == "str10":
        if len(text) > 10:
            raise ValueError("at most 10 characters")
        return text
    return _parse_auto(text)


class ValueEdit(QLineEdit):
    changed = Signal()

    def __init__(self, value=None, vtype: str | None = None, optional: bool = False):
        super().__init__()
        self._orig = value
        self._vtype = vtype
        self._optional = optional
        self._orig_text = _fmt(value, vtype)
        self.setText(self._orig_text)
        self.textChanged.connect(self._edited)
        self._check()

    def set_type(self, vtype: str | None) -> None:
        self._vtype = vtype
        self._check()

    def _edited(self, _=None) -> None:
        self._check()
        self.changed.emit()

    def _check(self) -> None:
        ok = True
        msg = ""
        if not (self._optional and self.text() == ""):
            try:
                parse_value(self.text(), self._vtype)
            except ValueError as exc:
                ok, msg = False, str(exc)
        self.setStyleSheet("" if ok else "background: #ffdddd;")
        self.setToolTip(msg)

    def is_valid(self) -> bool:
        return not self.styleSheet()

    def value(self):
        if self.text() == self._orig_text:
            return self._orig
        if self._optional and self.text() == "":
            return None
        try:
            return parse_value(self.text(), self._vtype)
        except ValueError:
            return self.text()


class IntEdit(ValueEdit):
    """An optional whole number (blank = not set)."""

    def __init__(self, value=None, placeholder: str = ""):
        super().__init__(value, "int16", optional=True)
        self.setPlaceholderText(placeholder)
        self.setMaximumWidth(70)

    def value(self):
        v = super().value()
        return v if isinstance(v, int) and not isinstance(v, bool) else None


class NumberEdit(ValueEdit):
    """An optional number (blank = not set)."""

    def __init__(self, value=None, placeholder: str = ""):
        super().__init__(value, "float", optional=True)
        self.setPlaceholderText(placeholder)
        self.setMaximumWidth(80)

    def value(self):
        v = super().value()
        if isinstance(v, float) and v.is_integer() and not isinstance(self._orig, float):
            return int(v)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class Combo(QComboBox):
    """A combo of (label, data) with value() = current data."""
    changed = Signal()

    def __init__(self, items: list[tuple[str, Any]], current=None):
        super().__init__()
        for label, data in items:
            self.addItem(label, data)
        self.set_value(current)
        self.currentIndexChanged.connect(lambda _: self.changed.emit())

    def set_value(self, v) -> None:
        for i in range(self.count()):
            if self.itemData(i) == v and type(self.itemData(i)) is type(v):
                self.setCurrentIndex(i)
                return
        if v is not None:
            self.addItem(f"{v} (?)", v)
            self.setCurrentIndex(self.count() - 1)

    def value(self):
        return self.currentData()

    def wheelEvent(self, e):            # noqa: N802 - don't change while the form scrolls
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class TypeCombo(Combo):
    def __init__(self, current: str | None, types: list[str] | None = None, auto: bool = False):
        names = list(types or VALUE_TYPES)
        items = ([("auto", AUTO)] if auto else []) + [(t, t) for t in names]
        super().__init__(items, current if current is not None else (AUTO if auto else names[0]))


class LiveCombo(QComboBox):
    """Choices from provider() -> [(label, data)], refreshed when the popup opens."""
    changed = Signal()

    def __init__(self, provider: Callable[[], list[tuple[str, Any]]], current=None):
        super().__init__()
        self._provider = provider
        self._value = current
        self.refresh()
        self.currentIndexChanged.connect(self._picked)

    def _picked(self, i: int) -> None:
        if i >= 0:
            self._value = self.itemData(i)
            self.changed.emit()

    def refresh(self) -> None:
        self.blockSignals(True)
        self.clear()
        items = list(self._provider())
        found = -1
        for i, (label, data) in enumerate(items):
            self.addItem(label, data)
            if found < 0 and data == self._value and type(data) is type(self._value):
                found = i
        if found < 0 and self._value is not None:
            self.addItem(f"{self._value} (missing)", self._value)
            found = self.count() - 1
        if found < 0 and self.count():
            found = 0
            self._value = self.itemData(0)
        self.setCurrentIndex(found)
        self.blockSignals(False)

    def showPopup(self) -> None:        # noqa: N802 (Qt name)
        self.refresh()
        super().showPopup()

    def set_value(self, v) -> None:
        self._value = v
        self.refresh()

    def wheelEvent(self, e):            # noqa: N802 - don't change while the form scrolls
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()

    def value(self):
        return self._value


class FixedEdit(QWidget):
    """A fixed value: bare (auto type) or { "type", "value" }."""
    changed = Signal()

    def __init__(self, value=None, types: list[str] | None = None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        typed = isinstance(value, dict)
        self.type = TypeCombo(value.get("type") if typed else AUTO, types, auto=True)
        self.type.setMaximumWidth(80)
        self.val = ValueEdit(value.get("value") if typed else (0 if value is None else value),
                             self.type.value())
        lay.addWidget(self.type)
        lay.addWidget(self.val, 1)
        self.type.changed.connect(self._type_changed)
        self.val.changed.connect(self.changed)

    def _type_changed(self) -> None:
        self.val.set_type(self.type.value())
        self.changed.emit()

    def value(self):
        v = self.val.value()
        t = self.type.value()
        if t == AUTO:
            return v
        try:
            v = parse_value(self.val.text(), t)
        except ValueError:
            pass
        return {"type": t, "value": v}


# ================================================================== refs
REF_KEYS = {"arg": "argIndex", "temp": "tempVarIndex", "cond": "condVarIndex", "global": "globalVariableIndex"}
REF_LABELS = {"arg": "arg", "temp": "temp var", "cond": "cond var", "global": "global", "fixed": "fixed",
              "command": "command result", "none": "(none)"}
VAR_KINDS = ("arg", "temp", "cond", "global")


def ref_kind(ref) -> str:
    if ref is None:
        return "none"
    if not isinstance(ref, dict):
        return "fixed"
    for k, key in REF_KEYS.items():
        if ref.get(key) is not None:
            return k
    if ref.get("fixedVal") is not None or ref.get("fixedValue") is not None:
        return "fixed"
    if ref.get("commandId") is not None or ref.get("componentType") is not None:
        return "command"
    return "none"


class RefEdit(QWidget):
    """An ArgRef. value() -> {"argIndex": i} / {"fixedVal": v} / command dict / None.

    command_factory(op) builds the editor used for the "command result" kind.
    """
    changed = Signal()

    def __init__(self, ctx: EditCtx, ref=None, kinds=("arg", "temp", "cond", "global", "fixed"),
                 command_factory: Callable[[dict], QWidget] | None = None):
        super().__init__()
        self.ctx = ctx
        self._kinds = tuple(kinds)
        self._command_factory = command_factory
        kind = ref_kind(ref)
        if kind not in self._kinds:
            kind = "fixed" if "fixed" in self._kinds and ref is not None else self._kinds[0]
        self._ref = ref
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.kind = Combo([(REF_LABELS[k], k) for k in self._kinds], kind)
        self.kind.setMaximumWidth(115)
        lay.addWidget(self.kind)
        self._body: QWidget | None = None
        self._lay = lay
        self._build(kind, ref)
        self.kind.changed.connect(self._kind_changed)

    def _choices(self, kind: str) -> list[tuple[str, int]]:
        if kind == "global":
            items, prefix = self.ctx.globals(), "global"
        else:
            fn = self.ctx.fn() or {}
            key = {"arg": "args", "temp": "tempVars", "cond": "condVars"}[kind]
            items, prefix = _dicts(fn.get(key)), kind
        return [(var_label(v, i, prefix), i) for i, v in enumerate(items)]

    def _build(self, kind: str, ref) -> None:
        if self._body is not None:
            self._lay.removeWidget(self._body)
            self._body.hide()
            self._body.deleteLater()
            self._body = None
        if kind in VAR_KINDS:
            cur = ref.get(REF_KEYS[kind]) if isinstance(ref, dict) and ref_kind(ref) == kind else None
            self._body = LiveCombo(lambda k=kind: self._choices(k), cur)
        elif kind == "fixed":
            fv = ref
            if isinstance(ref, dict):
                fv = ref.get("fixedVal", ref.get("fixedValue"))
            self._body = FixedEdit(fv if ref is not None else 0)
        elif kind == "command" and self._command_factory:
            self._body = self._command_factory(ref if ref_kind(ref) == "command" else {})
        if self._body is not None:
            self._body.changed.connect(self.changed)
            self._lay.addWidget(self._body, 1)

    def _kind_changed(self) -> None:
        self._build(self.kind.value(), None)
        self.changed.emit()

    def kind_value(self) -> str:
        return self.kind.value()

    def fixed_value(self):
        """The bare / typed fixed value (kind == 'fixed')."""
        return self._body.value() if isinstance(self._body, FixedEdit) else None

    def value(self):
        k = self.kind.value()
        if k in VAR_KINDS:
            v = self._body.value()
            return {REF_KEYS[k]: v} if v is not None else {REF_KEYS[k]: 0}
        if k == "fixed":
            return {"fixedVal": self._body.value()}
        if k == "command" and self._body is not None:
            return self._body.value()
        return None


# ================================================================== lists
class _Row(QFrame):
    def __init__(self, owner: ListEditor, widget: QWidget, tag):
        super().__init__()
        self.widget = widget
        self.tag = tag
        self.setObjectName("listrow")
        self.setStyleSheet("#listrow { border: 1px solid #c8c8c8; border-radius: 4px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(2)
        self.title = QLabel()
        self.title.setStyleSheet("color: #555; font-size: 11px;")
        top.addWidget(self.title, 1)
        for text, tip, fn in (("▲", "move up", lambda: owner._move(self, -1)),
                              ("▼", "move down", lambda: owner._move(self, 1)),
                              ("✕", "remove", lambda: owner._remove(self))):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(fn)
            top.addWidget(b)
        lay.addLayout(top)
        lay.addWidget(widget)


class ListEditor(QWidget):
    """Rows built by make_row(item) (a widget with value() / changed).

    add: one factory () -> new item, or [(label, factory)] for an add menu.
    Each row keeps a tag: its original index (None for added rows), so
    callers can map old indices to new ones after a reorder.
    """
    changed = Signal()

    def __init__(self, items: list, make_row: Callable[[Any], QWidget], add, add_text: str = "Add",
                 row_title: Callable[[int, QWidget], str] | None = None, compact: bool = False):
        super().__init__()
        self._make = make_row
        self._row_title = row_title
        self._compact = compact
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._rows_lay = QVBoxLayout()
        self._rows_lay.setSpacing(4)
        lay.addLayout(self._rows_lay)
        self._rows: list[_Row] = []
        btn = QPushButton(f"+ {add_text}")
        btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        if callable(add):
            btn.clicked.connect(lambda: self.add_item(add()))
        else:
            menu = QMenu(btn)
            for label, factory in add:
                menu.addAction(label, lambda f=factory: self.add_item(f()))
            btn.setMenu(menu)
        lay.addWidget(btn)
        self.add_btn = btn
        self._lay = lay
        for i, it in enumerate(items or []):
            self._append(it, i)
        self._retitle()

    def add_note(self, w: QWidget) -> None:
        """A widget above the add button (e.g. why there is nothing to add)."""
        self._lay.insertWidget(self._lay.indexOf(self.add_btn), w)

    def _append(self, item, tag) -> _Row:
        w = self._make(item)
        row = _Row(self, w, tag)
        if hasattr(w, "changed"):
            w.changed.connect(self._row_changed)
        self._rows.append(row)
        self._rows_lay.addWidget(row)
        return row

    def _row_changed(self) -> None:
        self._retitle()
        self.changed.emit()

    def add_item(self, item) -> None:
        self._append(item, None)
        self._retitle()
        self.changed.emit()

    def _remove(self, row: _Row) -> None:
        self._rows.remove(row)
        self._rows_lay.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._retitle()
        self.changed.emit()

    def _move(self, row: _Row, delta: int) -> None:
        i = self._rows.index(row)
        j = i + delta
        if not 0 <= j < len(self._rows):
            return
        self._rows.insert(j, self._rows.pop(i))
        self._rows_lay.removeWidget(row)
        self._rows_lay.insertWidget(j, row)
        self._retitle()
        self.changed.emit()

    def _retitle(self) -> None:
        for i, r in enumerate(self._rows):
            r.title.setText(self._row_title(i, r.widget) if self._row_title else f"#{i}")

    def widgets(self) -> list[QWidget]:
        return [r.widget for r in self._rows]

    def tags(self) -> list:
        return [r.tag for r in self._rows]

    def value(self) -> list:
        return [r.widget.value() for r in self._rows]


class SizedList(QWidget):
    """A fixed-length list of editors, resized by set_labels(); existing values are kept."""
    changed = Signal()

    def __init__(self, items: list, make_row: Callable[[Any], QWidget], labels: list[str]):
        super().__init__()
        self._make = make_row
        self._lay = QFormLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._rows: list[QWidget] = []
        self._pending = list(items or [])
        self.set_labels(labels)

    def set_labels(self, labels: list[str]) -> None:
        values = [w.value() for w in self._rows] + self._pending[len(self._rows):]
        self._pending = values
        while self._lay.rowCount():
            self._lay.removeRow(0)
        self._rows = []
        for i, label in enumerate(labels):
            w = self._make(values[i] if i < len(values) else None)
            if hasattr(w, "changed"):
                w.changed.connect(self.changed)
            self._rows.append(w)
            self._lay.addRow(label, w)
        if not labels:
            self._lay.addRow(hint("(none)"))

    def value(self) -> list:
        return [w.value() for w in self._rows]


def json_dumps(v) -> str:
    return json.dumps(v, indent=2, ensure_ascii=False)


def no_wheel(w: QWidget) -> QWidget:
    """Stop a combo from changing on mouse wheel while the form scrolls."""
    w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return w
