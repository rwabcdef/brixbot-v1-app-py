"""The PC app's own components (models only - no Qt).

Each model holds its state, runs its commands and raises its events. Views
(gui/app_widgets.py) call the user_* methods on user input and observe the
model through add_observer(). Commands never raise events: only user input
and time do, so a function setting a dial cannot loop back into itself.

Commands / events are listed in specs.APP_SPECS.
"""

from __future__ import annotations

import time
from typing import Callable

from . import errors as E
from .runtime import Command, Component
from .specs import ComponentSpec
from .variable import BOOL, CHAR, UINT8, UINT16, Variable

LED_PERIOD_MS = 250


def now_ms() -> int:
    return int(time.monotonic() * 1000)


class AppComponent(Component):
    def __init__(self, spec: ComponentSpec, cid: str, cfg: dict):
        super().__init__(spec, cid, cfg)
        self._observers: list[Callable[[], None]] = []

    def add_observer(self, fn: Callable[[], None]) -> None:
        self._observers.append(fn)

    def notify(self) -> None:
        for fn in list(self._observers):
            fn()


class DialModel(AppComponent):
    def __init__(self, spec, cid, cfg):
        super().__init__(spec, cid, cfg)
        self.min = int(self.init_arg("min", 0))
        self.max = int(self.init_arg("max", 100))
        self.value = max(self.min, min(self.max, int(self.init_arg("value", self.min))))

    def execute(self, cmd: Command) -> int:
        if cmd.command_id == 1:
            self.value = max(self.min, min(self.max, cmd.in_int(0)))
            self.notify()
        elif cmd.command_id == 2:
            cmd.set_ret(self.value, UINT8)
        else:
            return E.BAD_COMMAND
        return E.OK

    def user_change(self, value: int) -> None:
        self.value = max(self.min, min(self.max, int(value)))
        self.notify()
        self.emit(1, [self.id_var(), Variable(UINT8, self.value & 0xFF)])


class SwitchModel(AppComponent):
    def __init__(self, spec, cid, cfg):
        super().__init__(spec, cid, cfg)
        self.on = bool(self.init_arg("value", False))
        self.on_text = str(self.init_arg("onText", "on"))
        self.off_text = str(self.init_arg("offText", "off"))

    def execute(self, cmd: Command) -> int:
        if cmd.command_id == 1:
            self.on = cmd.in_int(0) != 0
            self.notify()
        elif cmd.command_id == 2:
            cmd.set_ret(self.on, BOOL)
        else:
            return E.BAD_COMMAND
        return E.OK

    def user_set(self, on: bool) -> None:
        self.on = bool(on)
        self.notify()
        self.emit(1, [self.id_var(), Variable(BOOL, self.on)])


class ButtonModel(AppComponent):
    def __init__(self, spec, cid, cfg):
        super().__init__(spec, cid, cfg)
        self.text = str(self.init_arg("text", self.name))
        self.pressed = False
        self._press_ms = 0

    def execute(self, cmd: Command) -> int:
        if cmd.command_id == 1:
            cmd.set_ret(self.pressed, BOOL)
            return E.OK
        return E.BAD_COMMAND

    def user_press(self) -> None:
        self.pressed = True
        self._press_ms = now_ms()
        self.notify()
        self.emit(1, [self.id_var()])

    def user_release(self) -> None:
        if not self.pressed:
            return
        self.pressed = False
        duration = min(now_ms() - self._press_ms, 65535)
        self.notify()
        self.emit(2, [self.id_var(), Variable(UINT16, duration)])


class LedModel(AppComponent):
    """Same commands and timing as the hub's Led (250 ms periods)."""

    def __init__(self, spec, cid, cfg):
        super().__init__(spec, cid, cfg)
        self.color = str(self.init_arg("color", "#2ecc40"))
        self.mode = 0          # 0 off, 1 on, 2 flashing
        self.lit = False
        self._on_p = self._off_p = 1
        self._num = 0
        self._count = 0
        self._next_ms = 0

    def execute(self, cmd: Command) -> int:
        cid = cmd.command_id
        if cid == 1:
            self._set(1, True)
        elif cid == 2:
            self._set(0, False)
        elif cid == 3:
            self._on_p = max(1, cmd.in_int(0))
            self._off_p = max(1, cmd.in_int(1))
            self._num = cmd.in_int(2)
            self._count = 0
            self._set(2, True)
            self._next_ms = now_ms() + self._on_p * LED_PERIOD_MS
        elif cid == 4:
            cmd.set_ret(self.mode, UINT8)
        else:
            return E.BAD_COMMAND
        return E.OK

    def _set(self, mode: int, lit: bool) -> None:
        self.mode, self.lit = mode, lit
        self.notify()

    def tick(self, now: int) -> None:
        if self.mode != 2 or now < self._next_ms:
            return
        if self.lit:
            self.lit = False
            self._count += 1
            self._next_ms = now + self._off_p * LED_PERIOD_MS
            if self._num and self._count >= self._num:
                self.mode = 0
                self.notify()
                self.emit(1)
                return
        else:
            self.lit = True
            self._next_ms = now + self._on_p * LED_PERIOD_MS
        self.notify()

    def stop(self) -> None:
        super().stop()
        self._set(0, False)


class DisplayModel(AppComponent):
    def __init__(self, spec, cid, cfg):
        super().__init__(spec, cid, cfg)
        self.prefix = str(self.init_arg("prefix", ""))
        self.suffix = str(self.init_arg("suffix", ""))
        self.text = ""

    def execute(self, cmd: Command) -> int:
        if cmd.command_id == 1:
            self.text = cmd.in_args[0].display() if cmd.in_args else ""
        elif cmd.command_id == 2:
            self.text = ""
        else:
            return E.BAD_COMMAND
        self.notify()
        return E.OK


class LogModel(AppComponent):
    MAX_LINES = 500

    def __init__(self, spec, cid, cfg):
        super().__init__(spec, cid, cfg)
        self.lines: list[str] = []

    def execute(self, cmd: Command) -> int:
        if cmd.command_id == 1:
            stamp = time.strftime("%H:%M:%S")
            self.lines.append(f"{stamp}  " + " ".join(v.display() for v in cmd.in_args))
            del self.lines[:-self.MAX_LINES]
        elif cmd.command_id == 2:
            self.lines.clear()
        else:
            return E.BAD_COMMAND
        self.notify()
        return E.OK


class TimerModel(AppComponent):
    def __init__(self, spec, cid, cfg):
        super().__init__(spec, cid, cfg)
        self.timeout_ms = int(self.init_arg("timeout_mS", 1000))
        self.repeat = bool(self.init_arg("repeat", False))
        self.auto_start = bool(self.init_arg("autoStart", False))
        self.active = False
        self._due = 0

    def execute(self, cmd: Command) -> int:
        cid = cmd.command_id
        if cid == 1:
            self.active, self._due = True, now_ms() + self.timeout_ms
        elif cid == 2:
            self.active = False
        elif cid == 3:
            self.timeout_ms = cmd.in_int(0)
        elif cid == 4:
            cmd.set_ret(self.active, BOOL)
        else:
            return E.BAD_COMMAND
        self.notify()
        return E.OK

    def start(self) -> None:
        super().start()
        if self.auto_start:
            self.active, self._due = True, now_ms() + self.timeout_ms
            self.notify()

    def stop(self) -> None:
        super().stop()
        self.active = False
        self.notify()

    def tick(self, now: int) -> None:
        if not self.active or now < self._due:
            return
        if self.repeat:
            self._due += self.timeout_ms
        else:
            self.active = False
        self.notify()
        self.emit(1, [Variable(CHAR, self.id)])


MODELS = {
    "dial": DialModel,
    "switch": SwitchModel,
    "button": ButtonModel,
    "led": LedModel,
    "display": DisplayModel,
    "log": LogModel,
    "timer": TimerModel,
}


def app_factory(spec: ComponentSpec, cid: str, cfg: dict) -> AppComponent:
    return MODELS[spec.type_name](spec, cid, cfg)
