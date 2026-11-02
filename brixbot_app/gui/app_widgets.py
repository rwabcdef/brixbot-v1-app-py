"""Qt views of the app component models (engine/app_components.py).

A view forwards user input to its model (user_* methods) and redraws when
the model notifies. Views are only enabled while the app is running.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDial, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..engine import app_components as M


class _View(QWidget):
    def __init__(self, model: M.AppComponent):
        super().__init__()
        self.model = model
        model.add_observer(self.refresh)

    def refresh(self) -> None:
        pass


class DialView(_View):
    def __init__(self, model: M.DialModel):
        super().__init__(model)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.dial = QDial()
        self.dial.setRange(model.min, model.max)
        self.dial.setNotchesVisible(True)
        self.dial.setFixedSize(90, 90)
        self.value = QLabel()
        self.value.setStyleSheet("font-size: 20px; font-weight: 600;")
        lay.addWidget(self.dial)
        lay.addWidget(self.value, 1, Qt.AlignmentFlag.AlignCenter)
        # coalesce fast dial movement: send the latest value at most every 80 ms
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(80)
        self._pending.timeout.connect(lambda: model.user_change(self.dial.value()))
        self.dial.valueChanged.connect(self._moved)
        self.refresh()

    def _moved(self, v: int) -> None:
        self.value.setText(str(v))
        if not self._pending.isActive():
            self._pending.start()

    def refresh(self) -> None:
        self.dial.blockSignals(True)
        self.dial.setValue(self.model.value)
        self.dial.blockSignals(False)
        self.value.setText(str(self.model.value))


class SwitchView(_View):
    def __init__(self, model: M.SwitchModel):
        super().__init__(model)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.btn = QPushButton()
        self.btn.setCheckable(True)
        self.btn.toggled.connect(model.user_set)
        lay.addWidget(self.btn)
        self.refresh()

    def refresh(self) -> None:
        self.btn.blockSignals(True)
        self.btn.setChecked(self.model.on)
        self.btn.blockSignals(False)
        self.btn.setText(self.model.on_text if self.model.on else self.model.off_text)


class ButtonView(_View):
    def __init__(self, model: M.ButtonModel):
        super().__init__(model)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.btn = QPushButton(model.text)
        self.btn.pressed.connect(model.user_press)
        self.btn.released.connect(model.user_release)
        lay.addWidget(self.btn)


class LedView(_View):
    def __init__(self, model: M.LedModel):
        super().__init__(model)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lamp = QLabel()
        self.lamp.setFixedSize(22, 22)
        self.state = QLabel()
        lay.addWidget(self.lamp)
        lay.addWidget(self.state, 1)
        self.refresh()

    def refresh(self) -> None:
        color = self.model.color if self.model.lit else "#555"
        self.lamp.setStyleSheet(f"background: {color}; border: 1px solid #222; border-radius: 11px;")
        self.state.setText(("off", "on", "flashing")[self.model.mode])


class DisplayView(_View):
    def __init__(self, model: M.DisplayModel):
        super().__init__(model)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.text = QLabel()
        self.text.setStyleSheet("font-size: 18px; font-family: Consolas, monospace;")
        lay.addWidget(self.text)
        self.refresh()

    def refresh(self) -> None:
        m = self.model
        self.text.setText(f"{m.prefix}{m.text}{m.suffix}" if m.text else "-")


class LogView(_View):
    def __init__(self, model: M.LogModel):
        super().__init__(model)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.box = QPlainTextEdit()
        self.box.setReadOnly(True)
        self.box.setMinimumHeight(120)
        self.box.setMaximumBlockCount(M.LogModel.MAX_LINES)
        lay.addWidget(self.box)
        self._shown = 0

    def refresh(self) -> None:
        lines = self.model.lines
        if len(lines) < self._shown or not lines:
            self.box.clear()
            self._shown = 0
        for line in lines[self._shown:]:
            self.box.appendPlainText(line)
        self._shown = len(lines)


class TimerView(_View):
    def __init__(self, model: M.TimerModel):
        super().__init__(model)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.text = QLabel()
        lay.addWidget(self.text)
        self.refresh()

    def refresh(self) -> None:
        m = self.model
        self.text.setText(f"{'running' if m.active else 'stopped'}  ({m.timeout_ms} mS{', repeat' if m.repeat else ''})")


VIEWS = {
    "dial": DialView,
    "switch": SwitchView,
    "button": ButtonView,
    "led": LedView,
    "display": DisplayView,
    "log": LogView,
    "timer": TimerView,
}


def make_view(model: M.AppComponent) -> QWidget:
    return VIEWS[model.type_name](model)
