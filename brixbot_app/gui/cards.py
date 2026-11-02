"""The coloured cards of the Hub / Link / App columns (as in the concept sketch):
components blue, sockets purple, event handlers yellow, functions green.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

COLORS = {
    "component": ("#dae8fc", "#6c8ebf"),
    "socket": ("#e1d5e7", "#9673a6"),
    "handler": ("#fff2cc", "#d6b656"),
    "function": ("#d5e8d4", "#82b366"),
}


class Card(QFrame):
    def __init__(self, title: str, kind: str, rows: list[str] | None = None, tooltip: str = ""):
        super().__init__()
        bg, border = COLORS[kind]
        self.setObjectName("card")
        self.setStyleSheet(
            f"#card {{ background: {bg}; border: 1px solid {border}; }}"
            f"#card QLabel {{ color: #111; background: transparent; }}"
            f"#card QLabel#row {{ border-top: 1px solid {border}; padding: 3px 6px; }}"
        )
        if tooltip:
            self.setToolTip(tooltip)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet("font-weight: 600; padding: 5px;")
        t.setWordWrap(True)
        lay.addWidget(t)
        self._lay = lay
        for r in rows or []:
            self.add_row(r)

    def add_row(self, text: str, rich: bool = False) -> QLabel:
        """A text row; rich=True renders text as HTML (caller escapes it)."""
        lbl = QLabel(text)
        lbl.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
        lbl.setObjectName("row")
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._lay.addWidget(lbl)
        return lbl

    def add_widget(self, w: QWidget) -> None:
        holder = QFrame()
        holder.setObjectName("row")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(6, 4, 6, 6)
        hl.addWidget(w)
        self._lay.addWidget(holder)


class Column(QFrame):
    """A titled, rounded column holding cards."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("column")
        self.setStyleSheet("#column { border: 1px solid #888; border-radius: 14px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 14)
        lay.setSpacing(12)
        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet("font-weight: 600;")
        lay.addWidget(t)
        self._lay = lay
        self._end = None

    def clear(self) -> None:
        while self._lay.count() > 1:
            item = self._lay.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

    def add(self, w: QWidget) -> None:
        self._lay.addWidget(w)

    def finish(self) -> None:
        self._lay.addStretch(1)
