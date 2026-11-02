import threading
import time

import pytest

from brixbot_app.serlink import AckTimeout, Frame, Link, inc_roll_code
from brixbot_app.serlink.frame import ACK_OK


def test_frame_round_trip():
    f = Frame("CONFG", "T", 7, "Dabc")
    assert f.to_string() == "CONFGT007004Dabc"
    assert Frame.from_string("CONFGT007004Dabc\r") == f


def test_ack_frames():
    plain = Frame.from_string("LED01A492900")
    assert plain.code == ACK_OK and plain.data == ""
    assert plain.to_string() == "LED01A492900"
    with_data = Frame.from_string("CONFGA012019QCK000-000039483812")
    assert with_data.code is None and with_data.data == "QCK000-000039483812"


@pytest.mark.parametrize("line", ["", "CONFG", "CONFGX001001B", "CONFGT0a1001B", "CONFGT001005AB", "CONFGT001070" + "x" * 70])
def test_malformed(line):
    assert Frame.from_string(line) is None


def test_roll_code_wrap():
    assert inc_roll_code(5) == 6
    assert inc_roll_code(999) == 0


class FakeHub:
    """Transport that acks every 'T' frame (optionally not) and can push frames."""

    def __init__(self, ack=True):
        self.ack = ack
        self.written: list[str] = []
        self._rx = b""
        self._lock = threading.Lock()

    def read(self, size):
        time.sleep(0.001)
        with self._lock:
            out, self._rx = self._rx[:size], self._rx[size:]
        return out

    def write(self, data):
        line = data.decode().strip()
        self.written.append(line)
        f = Frame.from_string(line)
        if self.ack and f and f.type == "T":
            self.push(Frame(f.protocol, "A", f.roll_code, "", ACK_OK).to_string())
        return len(data)

    def push(self, line):
        with self._lock:
            self._rx += (line + "\n").encode()

    def close(self):
        pass


def test_link_request_and_unidirectional():
    hub = FakeHub()
    link = Link()
    link.open(hub, "fake")
    try:
        ack = link.request("CONFG", "B")
        assert ack.type == "A" and ack.code == ACK_OK
        assert link.send("LED00", "G1").result(1) is None
        assert link.send("LED00", "G0").result(1) is None
        assert [w[:6] for w in hub.written] == ["CONFGT", "LED00U", "LED00U"]
        # roll codes per protocol
        assert hub.written[1][6:9] == "000" and hub.written[2][6:9] == "001"
    finally:
        link.close()


def test_link_ack_timeout():
    link = Link()
    link.open(FakeHub(ack=False), "fake")
    try:
        with pytest.raises(AckTimeout):
            link.request("CONFG", "Q", timeout=3)
    finally:
        link.close()


def test_link_receives_and_acks_t_frames():
    hub = FakeHub()
    link = Link()
    got = []
    link.add_listener("BUTT0", got.append)
    link.open(hub, "fake")
    try:
        hub.push("BUTT0T042002AP")
        hub.push("BUTT0U043007AR00350")
        deadline = time.time() + 1
        while len(got) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert [f.data for f in got] == ["AP", "AR00350"]
        assert "BUTT0A042900" in hub.written
    finally:
        link.close()


def test_send_rejects_bad_payload():
    link = Link()
    with pytest.raises(ValueError):
        link.send("LED00", "x" * 65)
    with pytest.raises(ValueError):
        link.send("LED0", "x")
