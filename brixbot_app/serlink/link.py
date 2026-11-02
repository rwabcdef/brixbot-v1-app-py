"""SerLink link: one serial port, many protocols (the PC end of the hub's
Reader / Writer / Socket stack).

Behaviour matches the hub:
  * outgoing frames go one at a time; a 'T' frame blocks the queue until its
    ack arrives (protocol and roll code must match) or ACK_TIMEOUT_S passes,
  * a received 'T' frame is acked at once,
  * consecutive writes are spaced by MIN_GAP_S - the hub's Reader takes about
    20 ms per received frame and holds only one.

All I/O runs on one background thread. Listeners are called on that thread,
so GUI code must hand the frames over to its own thread (see gui.bridge).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import Future
from typing import Callable, Protocol

from .frame import (
    Frame, TYPE_ACK, TYPE_TRANSMISSION, TYPE_UNIDIRECTION,
    check_data, check_protocol, inc_roll_code,
)

ACK_TIMEOUT_S = 1.0
MIN_GAP_S = 0.03


class Transport(Protocol):
    """What Link needs from a port (pyserial's Serial fits)."""
    def read(self, size: int) -> bytes: ...
    def write(self, data: bytes) -> int | None: ...
    def close(self) -> None: ...


class AckTimeout(Exception):
    """A 'T' frame was not acked in time."""


Listener = Callable[[Frame], None]
TrafficCallback = Callable[[str, str], None]     # (direction 'tx'|'rx', line)


class _TxItem:
    __slots__ = ("frame", "future")

    def __init__(self, frame: Frame):
        self.frame = frame
        self.future: Future = Future()


class Link:
    def __init__(self) -> None:
        self._transport: Transport | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._txq: deque[_TxItem] = deque()
        self._rolls: dict[str, int] = {}
        self._listeners: dict[str, list[Listener]] = {}
        self._any_listeners: list[Listener] = []
        self._traffic: TrafficCallback | None = None
        self.port_name = ""

    # ------------------------------------------------------------------ open / close
    def open_serial(self, port: str, baudrate: int = 115200) -> None:
        import serial   # pyserial, imported here so tests need not have it
        self.open(serial.Serial(port, baudrate, timeout=0.01, write_timeout=1.0), port)

    def open(self, transport: Transport, name: str = "") -> None:
        self.close()
        self._transport = transport
        self.port_name = name
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="serlink", daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None
        with self._lock:
            pending = list(self._txq)
            self._txq.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_exception(ConnectionError("link closed"))
        self.port_name = ""

    @property
    def is_open(self) -> bool:
        return self._transport is not None

    # ------------------------------------------------------------------ listeners
    def add_listener(self, protocol: str, fn: Listener) -> None:
        """Called (on the link thread) for every non-ack frame of protocol."""
        with self._lock:
            self._listeners.setdefault(protocol, []).append(fn)

    def remove_listener(self, protocol: str, fn: Listener) -> None:
        with self._lock:
            if fn in self._listeners.get(protocol, []):
                self._listeners[protocol].remove(fn)

    def add_any_listener(self, fn: Listener) -> None:
        """Called for every non-ack frame, whatever its protocol."""
        with self._lock:
            self._any_listeners.append(fn)

    def set_traffic_callback(self, fn: TrafficCallback | None) -> None:
        self._traffic = fn

    # ------------------------------------------------------------------ send
    def send(self, protocol: str, data: str, ack: bool = False) -> Future:
        """Queue a frame. The future gives the ack Frame ('T') or None ('U');
        it fails with AckTimeout when a 'T' frame is not acked."""
        check_protocol(protocol)
        check_data(data)
        if not self.is_open:
            f: Future = Future()
            f.set_exception(ConnectionError("link not open"))
            return f
        with self._lock:
            roll = self._rolls.get(protocol, 0)
            self._rolls[protocol] = inc_roll_code(roll)
            item = _TxItem(Frame(protocol, TYPE_TRANSMISSION if ack else TYPE_UNIDIRECTION, roll, data))
            self._txq.append(item)
        return item.future

    def request(self, protocol: str, data: str, timeout: float = 3.0) -> Frame:
        """Send a 'T' frame and wait for its ack (blocking - not on the GUI thread)."""
        return self.send(protocol, data, ack=True).result(timeout=timeout)

    def pending(self) -> int:
        with self._lock:
            return len(self._txq)

    # ------------------------------------------------------------------ thread
    def _write(self, frame: Frame) -> None:
        line = frame.to_string()
        assert self._transport is not None
        self._transport.write((line + "\n").encode("ascii"))
        if self._traffic:
            self._traffic("tx", line)

    def _run(self) -> None:
        buf = b""
        waiting: _TxItem | None = None
        wait_start = 0.0
        last_write = 0.0
        while not self._stop.is_set():
            transport = self._transport
            if transport is None:
                break
            # ---- receive
            try:
                chunk = transport.read(256)
            except Exception as exc:   # port unplugged
                self._fail_all(ConnectionError(str(exc)))
                break
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("ascii", errors="replace").rstrip("\r")
                    if not line:
                        continue
                    if self._traffic:
                        self._traffic("rx", line)
                    frame = Frame.from_string(line)
                    if frame is None:
                        continue
                    if frame.type == TYPE_ACK:
                        if (waiting is not None and frame.protocol == waiting.frame.protocol
                                and frame.roll_code == waiting.frame.roll_code):
                            waiting.future.set_result(frame)
                            waiting = None
                        continue
                    if frame.type == TYPE_TRANSMISSION:
                        try:
                            self._write(frame.make_ack())
                            last_write = time.monotonic()
                        except Exception:
                            pass
                    self._deliver(frame)

            # ---- ack timeout
            now = time.monotonic()
            if waiting is not None and (now - wait_start) > ACK_TIMEOUT_S:
                waiting.future.set_exception(AckTimeout(waiting.frame.to_string()))
                waiting = None

            # ---- transmit
            if waiting is None and (now - last_write) >= MIN_GAP_S:
                with self._lock:
                    item = self._txq.popleft() if self._txq else None
                if item is not None:
                    try:
                        self._write(item.frame)
                    except Exception as exc:
                        item.future.set_exception(ConnectionError(str(exc)))
                        continue
                    last_write = time.monotonic()
                    if item.frame.type == TYPE_TRANSMISSION:
                        waiting = item
                        wait_start = last_write
                    else:
                        item.future.set_result(None)
            if not chunk:
                time.sleep(0.002)
        if waiting is not None and not waiting.future.done():
            waiting.future.set_exception(ConnectionError("link closed"))

    def _deliver(self, frame: Frame) -> None:
        with self._lock:
            fns = list(self._listeners.get(frame.protocol, ())) + list(self._any_listeners)
        for fn in fns:
            try:
                fn(frame)
            except Exception:
                pass   # a bad listener must not stop the link

    def _fail_all(self, exc: Exception) -> None:
        with self._lock:
            pending = list(self._txq)
            self._txq.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_exception(exc)
