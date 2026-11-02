"""Hub operations over the CONFG socket (see the hub's Brix_ConfigLoader.hpp).

    B / D<chunk> / E   load + build      -> CONFG 'U' "EK<used:5>" | "EX<err:3><sec><idx:3>"
    R / S              run / stop        -> "RK" | "RX<err:3>",  "SK"
    Q                  status, in the ack: "Q<mode C|R><state N|L|K|X><err:3><sec><idx:3><used:5><len:4>"

All functions block (they wait for acks / replies), so call them from a
worker thread, never the GUI thread.
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Callable

from .engine import errors as E
from .serlink import AckTimeout, Frame, Link

CONFIG_PROTOCOL = "CONFG"
CHUNK_LEN = 63          # 64 payload chars minus the 'D'
BUILD_TIMEOUT_S = 5.0
REPLY_TIMEOUT_S = 2.0


class HubError(Exception):
    pass


@dataclass
class HubStatus:
    running: bool
    state: str          # N nothing loaded, L loading, K built, X error
    err: int
    section: str
    index: int
    arena_used: int
    config_len: int

    @classmethod
    def parse(cls, data: str) -> HubStatus | None:
        if len(data) < 19 or data[0] != "Q":
            return None
        try:
            return cls(data[1] == "R", data[2], int(data[3:6]), data[6], int(data[7:10]),
                       int(data[10:15]), int(data[15:19]))
        except ValueError:
            return None

    def describe(self) -> str:
        state = {"N": "no config", "L": "loading", "K": "config built", "X": "config error"}.get(self.state, self.state)
        s = f"{'running' if self.running else 'config mode'}, {state}"
        if self.state == "K":
            s += f" ({self.arena_used} bytes used)"
        if self.state == "X":
            s += ": " + describe_hub_error(self.err, self.section, self.index)
        return s


def describe_hub_error(err: int, section: str, index: int) -> str:
    where = E.SECTION_NAMES.get(section, section)
    loc = f" at {where}[{index}]" if where else ""
    return f"{E.ERROR_NAMES.get(err, f'error {err}')} ({err}){loc}"


class _Replies:
    """Collects CONFG 'U' replies while an operation runs."""

    def __init__(self, link: Link):
        self.link = link
        self.q: queue.Queue[str] = queue.Queue()

    def __enter__(self):
        self.link.add_listener(CONFIG_PROTOCOL, self._on)
        return self

    def __exit__(self, *exc):
        self.link.remove_listener(CONFIG_PROTOCOL, self._on)

    def _on(self, frame: Frame) -> None:
        self.q.put(frame.data)

    def wait(self, prefix: str, timeout: float) -> str:
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise HubError(f"no '{prefix}' reply from the hub")
            try:
                data = self.q.get(timeout=left)
            except queue.Empty:
                continue
            if data.startswith(prefix):
                return data


def _request(link: Link, data: str) -> Frame:
    try:
        return link.request(CONFIG_PROTOCOL, data)
    except AckTimeout as exc:
        raise HubError("hub did not ack (is it running BrixBot firmware?)") from exc


def query(link: Link) -> HubStatus:
    ack = _request(link, "Q")
    status = HubStatus.parse(ack.data)
    if status is None:
        raise HubError(f"unexpected status reply {ack.data!r}")
    return status


@dataclass
class LoadResult:
    ok: bool
    message: str
    arena_used: int = 0


def load(link: Link, config: str, progress: Callable[[int, int], None] | None = None) -> LoadResult:
    """Send the config (B, D..., E) and wait for the build result."""
    chunks = [config[i:i + CHUNK_LEN] for i in range(0, len(config), CHUNK_LEN)]
    with _Replies(link) as replies:
        _request(link, "B")
        for n, chunk in enumerate(chunks, 1):
            _request(link, "D" + chunk)
            if progress:
                progress(n, len(chunks))
        _request(link, "E")
        reply = replies.wait("E", BUILD_TIMEOUT_S)
    if reply.startswith("EK"):
        used = int(reply[2:7]) if reply[2:7].isdigit() else 0
        return LoadResult(True, f"hub built the config ({len(config)} chars, {used} bytes used)", used)
    if reply.startswith("EX") and len(reply) >= 9 and reply[2:5].isdigit() and reply[6:9].isdigit():
        return LoadResult(False, "hub rejected the config: " + describe_hub_error(int(reply[2:5]), reply[5], int(reply[6:9])))
    return LoadResult(False, f"unexpected build reply {reply!r}")


def run(link: Link) -> None:
    with _Replies(link) as replies:
        _request(link, "R")
        reply = replies.wait("R", REPLY_TIMEOUT_S)
    if reply != "RK":
        err = int(reply[2:5]) if reply[2:5].isdigit() else -1
        raise HubError("hub would not run: " + E.ERROR_NAMES.get(err, reply))


def stop(link: Link) -> None:
    with _Replies(link) as replies:
        _request(link, "S")
        replies.wait("S", REPLY_TIMEOUT_S)


def list_ports() -> list[tuple[str, str]]:
    """(device, description) of every serial port."""
    from serial.tools import list_ports as lp
    return [(p.device, p.description or "") for p in sorted(lp.comports(), key=lambda p: p.device)]


def probe(port: str, settle_s: float = 0.3) -> HubStatus | None:
    """Open a port, ask for the hub status, close it. None if no BrixBot hub."""
    link = Link()
    try:
        link.open_serial(port)
    except Exception:
        return None
    try:
        time.sleep(settle_s)
        return query(link)
    except Exception:
        return None
    finally:
        link.close()


def detect(ports: list[str] | None = None) -> list[tuple[str, HubStatus]]:
    """Every port answering as a BrixBot hub."""
    found = []
    for port in ports if ports is not None else [p for p, _ in list_ports()]:
        st = probe(port)
        if st is not None:
            found.append((port, st))
    return found
