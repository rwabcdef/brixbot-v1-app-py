"""Variable - Python port of Brix::Variable (Brix_Variable.cpp), same semantics:

* arithmetic / comparison through int (C int32 rules) unless either side is float,
* assignment converts into the destination's type (NONE takes the source's type),
* the same text encoding for socket payload fields.
"""

from __future__ import annotations

NONE, UINT8, UINT16, INT8, INT16, FLOAT, BOOL, CHAR, STR10, COMPONENT = range(10)
TYPE_NAMES = ["none", "uint8", "uint16", "int8", "int16", "float", "bool", "char", "str10", "component"]
STR10_LEN = 10


def type_from_name(name) -> int | None:
    if not isinstance(name, str) or name not in TYPE_NAMES:
        return None
    return TYPE_NAMES.index(name)


def default_width(vtype: int) -> int:
    return {UINT8: 3, UINT16: 5, INT8: 4, INT16: 6, BOOL: 1, CHAR: 1}.get(vtype, 0)


def _wrap(value: int, bits: int, signed: bool) -> int:
    mask = (1 << bits) - 1
    value &= mask
    if signed and value >= (1 << (bits - 1)):
        value -= (1 << bits)
    return value


def _c_div(a: int, b: int) -> int:
    """C integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


class Variable:
    __slots__ = ("type", "value")

    def __init__(self, vtype: int = NONE, value=None):
        self.type = vtype
        self.value = self._zero(vtype) if value is None else value

    @staticmethod
    def _zero(vtype: int):
        if vtype == FLOAT:
            return 0.0
        if vtype == BOOL:
            return False
        if vtype == CHAR:
            return "\0"
        if vtype == STR10:
            return ""
        if vtype == COMPONENT:
            return None
        return 0

    def __repr__(self) -> str:
        return f"Variable({TYPE_NAMES[self.type]}, {self.value!r})"

    # ---------------------------------------------------------------- kinds
    def is_unsigned_int(self) -> bool:
        return self.type in (UINT8, UINT16)

    def is_signed_int(self) -> bool:
        return self.type in (INT8, INT16)

    def is_integral(self) -> bool:
        return self.type in (UINT8, UINT16, INT8, INT16, BOOL, CHAR)

    def is_numeric(self) -> bool:
        return self.is_integral() or self.type == FLOAT

    # ---------------------------------------------------------------- get / set
    def get_int(self) -> int:
        t = self.type
        if t in (UINT8, UINT16, INT8, INT16):
            return int(self.value)
        if t == FLOAT:
            return int(self.value)          # truncates toward zero, like C
        if t == BOOL:
            return 1 if self.value else 0
        if t == CHAR:
            return ord(self.value) & 0xFF if self.value else 0
        return 0

    def get_float(self) -> float:
        return float(self.value) if self.type == FLOAT else float(self.get_int())

    def set_int(self, v: int) -> None:
        t = self.type
        v = int(v)
        if t == UINT8:
            self.value = _wrap(v, 8, False)
        elif t == UINT16:
            self.value = _wrap(v, 16, False)
        elif t == INT8:
            self.value = _wrap(v, 8, True)
        elif t == INT16:
            self.value = _wrap(v, 16, True)
        elif t == FLOAT:
            self.value = float(v)
        elif t == BOOL:
            self.value = v != 0
        elif t == CHAR:
            self.value = chr(v & 0xFF)

    def set_float(self, v: float) -> None:
        if self.type == FLOAT:
            self.value = float(v)
        else:
            self.set_int(int(v))

    def set(self, vtype: int, value) -> None:
        """Set type and value together."""
        self.type = vtype
        self.value = value

    def copy(self) -> Variable:
        return Variable(self.type, self.value)

    def assign_from(self, src: Variable) -> None:
        if self is src:
            return
        if self.type == NONE or self.type == src.type:
            self.type = src.type
            self.value = src.value
            return
        if COMPONENT in (self.type, src.type):
            return
        if self.type == STR10:
            text = src.to_str(0, STR10_LEN)
            self.value = text or ""
            return
        if src.type == STR10:
            self.from_str(src.value)
            return
        if self.type == FLOAT or src.type == FLOAT:
            self.set_float(src.get_float())
        else:
            self.set_int(src.get_int())

    def compare(self, other: Variable) -> int | None:
        """-1 / 0 / 1, None when not comparable."""
        if self.type == COMPONENT and other.type == COMPONENT:
            return 0 if self.value is other.value else 1
        if self.type == STR10 and other.type == STR10:
            a, b = self.value[:STR10_LEN], other.value[:STR10_LEN]
            return (a > b) - (a < b)
        if not self.is_numeric() or not other.is_numeric():
            return None
        if FLOAT in (self.type, other.type):
            a, b = self.get_float(), other.get_float()
        else:
            a, b = self.get_int(), other.get_int()
        return (a > b) - (a < b)

    # ---------------------------------------------------------------- text
    def to_str(self, width: int, max_len: int) -> str | None:
        """Payload text (None if the value does not fit)."""
        t = self.type
        if t in (UINT8, UINT16):
            return _uint_str(int(self.value), width, max_len)
        if t in (INT8, INT16):
            v = int(self.value)
            if v >= 0:
                return _uint_str(v, width, max_len)
            if max_len < 2:
                return None
            body = _uint_str(-v, max(width - 1, 0) if width else 0, max_len - 1)
            return None if body is None else "-" + body
        if t == BOOL:
            return ("1" if self.value else "0") if max_len >= 1 else None
        if t == CHAR:
            return self.value if max_len >= 1 else None
        if t == STR10:
            s = self.value[:STR10_LEN][:max_len]
            return s.ljust(min(width, max_len)) if width else s
        if t == FLOAT:
            v = float(self.value)
            sign = ""
            if v < 0:
                sign, v = "-", -v
            whole = int(v)
            frac = int((v - whole) * 100.0 + 0.5)
            if frac >= 100:
                whole, frac = whole + 1, frac - 100
            s = f"{sign}{whole}.{frac:02d}"
            return s if len(s) <= max_len else None
        return None

    def from_str(self, text: str) -> bool:
        """Parse payload text into this (typed) variable. False if invalid."""
        if not text:
            return False
        t = self.type
        if t in (UINT8, UINT16, INT8, INT16):
            neg = text[0] == "-"
            digits = text[1:] if neg else text
            if (neg and (t in (UINT8, UINT16) or not digits)) or not digits.isdigit() or not digits.isascii():
                return False
            v = int(digits)
            if v > 70000:
                return False
            if neg:
                v = -v
            lo, hi = {UINT8: (0, 255), UINT16: (0, 65535), INT8: (-128, 127), INT16: (-32768, 32767)}[t]
            if not lo <= v <= hi:
                return False
            self.value = v
            return True
        if t == BOOL:
            if text not in ("0", "1"):
                return False
            self.value = text == "1"
            return True
        if t == CHAR:
            if len(text) != 1:
                return False
            self.value = text
            return True
        if t == STR10:
            if len(text) > STR10_LEN:
                return False
            self.value = text
            return True
        if t == FLOAT:
            try:
                self.value = float(text)
            except ValueError:
                return False
            return True
        return False

    def display(self) -> str:
        """Human readable value (logs / displays)."""
        if self.type == BOOL:
            return "true" if self.value else "false"
        if self.type == FLOAT:
            return f"{self.value:.2f}"
        if self.type == COMPONENT:
            v = self.value
            return f"<{getattr(v, 'type_name', '?')} {getattr(v, 'id', '?')}>" if v is not None else "<none>"
        if self.type == NONE:
            return "-"
        return str(self.value)


def _uint_str(value: int, width: int, max_len: int) -> str | None:
    s = str(value)
    if width and len(s) > width:
        return None
    s = s.rjust(width, "0") if width else s
    return s if len(s) <= max_len else None


def statement_op(left: Variable, op: int, right: Variable) -> bool:
    """left (op)= right for the numeric operators (2..5). False on error."""
    if not left.is_numeric() or not right.is_numeric():
        return False
    if FLOAT in (left.type, right.type):
        a, b = left.get_float(), right.get_float()
        if op == 5 and b == 0.0:
            return False
        r = {2: a + b, 3: a - b, 4: a * b}.get(op, a / b if op == 5 else None)
        if r is None:
            return False
        left.set_float(r)
    else:
        a, b = left.get_int(), right.get_int()
        if op == 2:
            r = a + b
        elif op == 3:
            r = a - b
        elif op == 4:
            r = a * b
        elif op == 5:
            if b == 0:
                return False
            r = _c_div(a, b)
        else:
            return False
        r = _wrap(r, 32, True)   # int32 like the hub
        left.set_int(r)
    return True
