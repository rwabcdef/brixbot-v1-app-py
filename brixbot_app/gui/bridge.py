"""Hand work from background threads (the serial link, hub operations) to the
GUI thread: post() a callable from any thread; the GUI drains the queue on a
QTimer, so all engine and widget code runs on the GUI thread only.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable

from PySide6.QtCore import QObject, QTimer


class Bridge(QObject):
    def __init__(self, parent: QObject | None = None, period_ms: int = 15):
        super().__init__(parent)
        self._q: queue.Queue[Callable[[], None]] = queue.Queue()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.drain)
        self._timer.start(period_ms)
        self.idle_hooks: list[Callable[[], None]] = []

    def post(self, fn: Callable[[], None]) -> None:
        self._q.put(fn)

    def drain(self) -> None:
        for _ in range(200):
            try:
                fn = self._q.get_nowait()
            except queue.Empty:
                break
            fn()
        for hook in self.idle_hooks:
            hook()

    def run_in_thread(self, work: Callable[[], object], done: Callable[[object], None],
                      failed: Callable[[Exception], None]) -> None:
        """Run work() on a worker thread, then done(result) / failed(exc) on the GUI thread."""
        def target():
            try:
                result = work()
            except Exception as exc:     # reported on the GUI thread
                self.post(lambda exc=exc: failed(exc))
            else:
                self.post(lambda: done(result))
        threading.Thread(target=target, daemon=True).start()
