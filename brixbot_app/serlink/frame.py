"""SerLink frame - Python port of SerLink::Frame (hub: Frame.hpp / Frame.cpp).

Wire format (ASCII, one frame per line, terminated by '\\n'):

    <protocol:5><type:1><rollCode:3><dataLen:3><data:dataLen>

    type  'T' transmission, needs an ack     'U' unidirectional (no ack)
          'A' ack                            'B' relay ack
    A dataLen >= ACK_OK (900) is a response code and the frame carries no data,
    e.g. the plain ack "CONFGA012900". An ack may instead carry data (the hub's
    instant handler reply), e.g. "CONFGA012019QCK000-000039483812".
"""

from __future__ import annotations

from dataclasses import dataclass

LEN_PROTOCOL = 5
LEN_TYPE = 1
LEN_ROLLCODE = 3
LEN_DATALEN = 3
LEN_HEADER = LEN_PROTOCOL + LEN_TYPE + LEN_ROLLCODE + LEN_DATALEN   # 12
MAX_DATALEN = 64
ACK_OK = 900
MAX_ROLLCODE = 999

TYPE_TRANSMISSION = "T"
TYPE_UNIDIRECTION = "U"
TYPE_ACK = "A"
TYPE_RELAY_ACK = "B"
FRAME_TYPES = (TYPE_TRANSMISSION, TYPE_UNIDIRECTION, TYPE_ACK, TYPE_RELAY_ACK)


@dataclass
class Frame:
    protocol: str
    type: str
    roll_code: int
    data: str = ""
    code: int | None = None      # response code (>= ACK_OK) instead of data

    def to_string(self) -> str:
        """Serialised frame, without the line terminator."""
        if self.code is not None:
            return f"{self.protocol}{self.type}{self.roll_code:03d}{self.code:03d}"
        return f"{self.protocol}{self.type}{self.roll_code:03d}{len(self.data):03d}{self.data}"

    @staticmethod
    def from_string(line: str) -> Frame | None:
        """Parse one received line (terminator already removed). None if malformed."""
        line = line.rstrip("\r\n")
        if len(line) < LEN_HEADER:
            return None
        protocol = line[0:5]
        ftype = line[5]
        roll = line[6:9]
        dlen = line[9:12]
        if ftype not in FRAME_TYPES or not roll.isdigit() or not dlen.isdigit():
            return None
        n = int(dlen)
        if n >= ACK_OK:
            return Frame(protocol, ftype, int(roll), "", n)
        if n > MAX_DATALEN:
            return None
        data = line[LEN_HEADER:LEN_HEADER + n]
        if len(data) != n:
            return None
        return Frame(protocol, ftype, int(roll), data)

    def make_ack(self) -> Frame:
        """Plain ack for this (received 'T') frame."""
        return Frame(self.protocol, TYPE_ACK, self.roll_code, "", ACK_OK)


def inc_roll_code(roll: int) -> int:
    """Same wrap as Frame::incRollCode (0 .. 999)."""
    return 0 if roll >= MAX_ROLLCODE else roll + 1


def check_protocol(protocol: str) -> None:
    if len(protocol) != LEN_PROTOCOL or not protocol.isascii():
        raise ValueError(f"protocol must be {LEN_PROTOCOL} ASCII chars: {protocol!r}")


def check_data(data: str) -> None:
    if len(data) > MAX_DATALEN:
        raise ValueError(f"payload longer than {MAX_DATALEN} chars")
    if not data.isascii() or "\n" in data or "\r" in data:
        raise ValueError("payload must be ASCII without line breaks")
