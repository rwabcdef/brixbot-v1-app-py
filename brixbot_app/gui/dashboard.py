"""The run dashboard: only the app's components, as large live controls.

Shown in place of the Hub / Link / App columns while running (Run box: "go to
dashboard", or the Dashboard / Editor view button). It is built from the
running Program's models when shown and torn down when hidden, so its views
never outlive the models they observe.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..engine.runtime import Program
from .app_widgets import make_view
from .cards import Card

COLUMNS = 3


class Dashboard(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.views: list = []
        self.cards: list[Card] = []
        self._body: QWidget | None = None

    def build(self, program: Program, components: list, title: str) -> None:
        self.clear()
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 12, 16, 16)
        head = QLabel(title)
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.setStyleSheet("font-size: 14pt; font-weight: 600; padding: 4px;")
        lay.addWidget(head)
        grid = QGridLayout()
        grid.setSpacing(16)
        n = 0
        for c in components:
            if not isinstance(c, dict):
                continue
            model = program.resolve(c.get("type"), c.get("id"))
            if model is None:
                continue
            name = c.get("#name") or f"{c.get('type')} {c.get('id')}"
            card = Card(name, "component", tooltip=c.get("#comment", ""))
            view = make_view(model)
            view.setEnabled(True)
            card.add_widget(view)
            grid.addWidget(card, n // COLUMNS, n % COLUMNS, Qt.AlignmentFlag.AlignTop)
            self.views.append(view)
            self.cards.append(card)
            n += 1
        for col in range(COLUMNS):
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)
        if n == 0:
            lay.addWidget(QLabel("The app has no components. Add some to the App column."))
        lay.addStretch(1)
        self.setWidget(body)
        self._body = body

    def clear(self) -> None:
        for v in self.views:
            v.model.remove_observer(v.refresh)
        self.views = []
        self.cards = []
        if self._body is not None:
            self.takeWidget()
            self._body.hide()
            self._body.deleteLater()
            self._body = None
