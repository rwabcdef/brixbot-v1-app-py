"""The coloured cards of the Hub / Link / App columns (as in the concept sketch):
components blue, sockets purple, event handlers yellow, functions green,
globals grey.

Cards and rows can carry an item path (e.g. ("hub", "functions", 2)): a
click selects that item for editing and a right click opens its context
menu, through the callbacks the window sets on the column. A card or row
can be marked selected, or marked with a verify error.

A card can hold inset sections in another kind's colour: a component card
shows its event handlers and control connections in a yellow inset, so they
sit inside the component they belong to.

The colours come from the theme (theme.py): cards, rows and sections only set
their objectName and `kind` / `selected` / `error` properties.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget

from . import theme

Click = Callable[[tuple], None]


class _Clickable:
    """Mixin: path + click / context menu callbacks."""
    path: tuple | None = None
    on_click: Click | None = None
    on_menu: Callable[[tuple, object], None] | None = None

    def _press(self, event) -> bool:
        if self.path is None:
            return False
        if event.button() == Qt.MouseButton.LeftButton and self.on_click:
            self.on_click(self.path)
            return True
        if event.button() == Qt.MouseButton.RightButton and self.on_menu:
            self.on_menu(self.path, event.globalPosition().toPoint())
            return True
        return False


class Row(QLabel, _Clickable):
    def __init__(self, text: str, kind: str, rich: bool = False, path: tuple | None = None):
        super().__init__(text)
        self.path = path
        self.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
        self.setObjectName("row")
        self.setProperty("kind", kind)
        self.setWordWrap(True)
        if path is None:
            self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_state(False, "")

    def set_state(self, selected: bool, error: str) -> None:
        theme.set_state(self, selected=selected, error=bool(error))
        if error:
            self.setToolTip(error)

    def mousePressEvent(self, e):      # noqa: N802 (Qt name)
        if not self._press(e):
            super().mousePressEvent(e)


class Card(QFrame, _Clickable):
    def __init__(self, title: str, kind: str, rows: list[str] | None = None, tooltip: str = "",
                 path: tuple | None = None):
        super().__init__()
        self.kind = kind
        self.path = path
        self.setObjectName("card")
        self.setProperty("kind", kind)
        self._tooltip = tooltip
        if tooltip:
            self.setToolTip(tooltip)
        if path is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet("font-weight: 600; padding: 5px;")
        t.setWordWrap(True)
        lay.addWidget(t)
        self._lay = lay
        self.rows: list[Row] = []
        self.set_state(False, "")
        for r in rows or []:
            self.add_row(r)

    def set_state(self, selected: bool, error: str) -> None:
        theme.set_state(self, selected=selected, error=bool(error))
        tip = "\n\n".join(x for x in (error, self._tooltip) if x)
        self.setToolTip(tip)

    def add_row(self, text: str, rich: bool = False, path: tuple | None = None, tooltip: str = "") -> Row:
        """A text row; rich=True renders text as HTML (caller escapes it). A row with a
        path is clickable on its own."""
        row = Row(text, self.kind, rich, path)
        if tooltip:
            row.setToolTip(tooltip)
        row.on_click, row.on_menu = self.on_click, self.on_menu
        self._lay.addWidget(row)
        self.rows.append(row)
        return row

    def add_section(self, kind: str, title: str) -> Section:
        """An inset box in another kind's colour (e.g. the handlers of a component)."""
        sec = Section(self, kind, title)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(10, 2, 10, 8)
        hl.addWidget(sec)
        self._lay.addWidget(holder)
        return sec

    def add_widget(self, w: QWidget) -> None:
        holder = QFrame()
        holder.setObjectName("cardWidget")
        holder.setProperty("kind", self.kind)
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(6, 4, 6, 6)
        hl.addWidget(w)
        self._lay.addWidget(holder)

    def mousePressEvent(self, e):      # noqa: N802 (Qt name)
        if not self._press(e):
            super().mousePressEvent(e)


class Section(QFrame):
    """An inset box inside a card; its rows count as the card's rows."""

    def __init__(self, card: Card, kind: str, title: str):
        super().__init__()
        self.card = card
        self.kind = kind
        self.setObjectName("section")
        self.setProperty("kind", kind)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600; padding: 2px 6px; font-size: 9pt;")
        self._lay.addWidget(t)
        self.rows: list[Row] = []

    def add_row(self, text: str, path: tuple | None = None, tooltip: str = "") -> Row:
        row = Row(text, self.kind, False, path)
        if tooltip:
            row.setToolTip(tooltip)
        row.on_click, row.on_menu = self.card.on_click, self.card.on_menu
        self._lay.addWidget(row)
        self.rows.append(row)
        self.card.rows.append(row)
        return row


class Column(QFrame):
    """A titled, rounded column holding cards, with an optional + (add) menu."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("column")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 14)
        lay.setSpacing(12)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet("font-weight: 600;")
        self.add_btn = QToolButton()
        self.add_btn.setText("+ Add")
        self.add_btn.setToolTip(f"Add to {title}")
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_menu = QMenu(self.add_btn)
        self.add_btn.setMenu(self.add_menu)
        self.view_btn = QToolButton()
        self.view_btn.setText("View")
        self.view_btn.setToolTip(f"View {title}")
        self.view_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.view_menu = QMenu(self.view_btn)
        self.view_btn.setMenu(self.view_menu)
        self.view_btn.setVisible(False)          # shown once its menu has entries (show_view)
        hl.addWidget(self.view_btn)
        hl.addWidget(t, 1)
        hl.addWidget(self.add_btn)
        lay.addWidget(head)
        self._lay = lay
        self.on_click: Click | None = None
        self.on_menu = None
        self.items: list[_Clickable] = []

    def show_view(self) -> None:
        self.view_btn.setVisible(not self.view_menu.isEmpty())

    def clear(self) -> None:
        while self._lay.count() > 1:
            item = self._lay.takeAt(1)
            if item.widget():
                item.widget().hide()            # deleteLater runs later; don't paint it meanwhile
                item.widget().deleteLater()
        self.items = []

    def add(self, w: QWidget) -> None:
        if isinstance(w, Card):
            w.on_click, w.on_menu = self.on_click, self.on_menu
            for r in w.rows:
                r.on_click, r.on_menu = self.on_click, self.on_menu
            self.items.append(w)
            self.items += [r for r in w.rows if r.path is not None]
        self._lay.addWidget(w)

    def finish(self) -> None:
        self._lay.addStretch(1)

    def mark(self, selected: tuple | None, errors: dict[tuple, str]) -> None:
        for it in self.items:
            if it.path is not None:
                it.set_state(it.path == selected, errors.get(it.path, ""))
