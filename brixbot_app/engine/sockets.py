"""Link socket definitions and payload codec - same rules as the hub
(Brix_LinkSocket.cpp):

    payload = commonArgs + one schema's constantArgs + that schema's args

Fields are fixed width (maxLen, or the type's default width) except the last
field of a payload, which may be variable (minLen..maxLen).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import errors as E
from .variable import COMPONENT, NONE, STR10, STR10_LEN, Variable, default_width, type_from_name

MAX_PAYLOAD = 64


@dataclass
class FieldSpec:
    type: int
    min_len: int
    max_len: int
    min_value: float | None = None
    max_value: float | None = None
    name: str = ""

    @property
    def fixed(self) -> bool:
        return self.min_len == self.max_len


@dataclass
class Schema:
    constant_args: list[FieldSpec]
    constant_values: list[Variable]
    args: list[FieldSpec]
    ack: bool = False
    comment: str = ""
    bindings: list = field(default_factory=list)     # FunctionBinding of this side


@dataclass
class SocketDef:
    protocol: str
    common_args: list[FieldSpec]
    schemas: list[Schema]
    comment: str = ""

    def fields(self, si: int) -> list[FieldSpec]:
        sc = self.schemas[si]
        return self.common_args + sc.constant_args + sc.args

    def validate(self) -> None:
        for si in range(len(self.schemas)):
            fs = self.fields(si)
            total = 0
            for i, f in enumerate(fs):
                if f.max_len == 0 or f.min_len > f.max_len:
                    raise E.BuildError(E.BAD_SCHEMA, f"schema {si} field {i}: bad minLen/maxLen")
                if not f.fixed and i != len(fs) - 1:
                    raise E.BuildError(E.BAD_SCHEMA, f"schema {si} field {i}: only the last field may be variable width")
                total += f.max_len
            if total > MAX_PAYLOAD:
                raise E.BuildError(E.BAD_SCHEMA, f"schema {si}: payload up to {total} chars > {MAX_PAYLOAD}")

    # ---------------------------------------------------------------- codec
    def encode(self, si: int, args: list[Variable]) -> str:
        """args = commonArgs values then the schema's args. Raises ValueError."""
        sc = self.schemas[si]
        nc, nk = len(self.common_args), len(sc.constant_args)
        out = ""
        ai = 0
        for i, f in enumerate(self.fields(si)):
            v = Variable(f.type)
            if nc <= i < nc + nk:
                v.assign_from(sc.constant_values[i - nc])
            else:
                if ai >= len(args):
                    raise ValueError(f"{self.protocol}: too few message args")
                v.assign_from(args[ai])
                ai += 1
            room = min(MAX_PAYLOAD - len(out), f.max_len)
            text = v.to_str(f.min_len, room)
            if not text:
                raise ValueError(f"{self.protocol}: value {v.display()} does not fit field {i}")
            out += text
        return out

    def decode(self, si: int, data: str) -> list[Variable] | None:
        sc = self.schemas[si]
        nc, nk = len(self.common_args), len(sc.constant_args)
        pos = 0
        out: list[Variable] = []
        for i, f in enumerate(self.fields(si)):
            width = f.max_len
            if not f.fixed:
                width = len(data) - pos
                if not f.min_len <= width <= f.max_len:
                    return None
            if pos + width > len(data):
                return None
            v = Variable(f.type)
            if not v.from_str(data[pos:pos + width]):
                return None
            pos += width
            if nc <= i < nc + nk:
                if v.compare(sc.constant_values[i - nc]) != 0:
                    return None
            elif v.is_numeric():
                x = v.get_float()
                if (f.min_value is not None and x < f.min_value) or (f.max_value is not None and x > f.max_value):
                    return None
            out.append(v)
        return out if pos == len(data) else None

    def match(self, data: str) -> tuple[int, list[Variable]] | None:
        for si in range(len(self.schemas)):
            fields = self.decode(si, data)
            if fields is not None:
                return si, fields
        return None


# ------------------------------------------------------------------ parsing
def parse_field(j: dict) -> FieldSpec:
    t = type_from_name(j.get("type"))
    if t is None or t in (NONE, COMPONENT):
        raise E.BuildError(E.BAD_TYPE, f"field type {j.get('type')!r}")
    w = default_width(t)
    dflt_min = w if w else 1
    dflt_max = w if w else (STR10_LEN if t == STR10 else 12)
    max_len = j["maxLen"] if isinstance(j.get("maxLen"), int) else dflt_max
    if isinstance(j.get("minLen"), int):
        min_len = j["minLen"]
    else:
        min_len = max_len if (w and isinstance(j.get("maxLen"), int)) else dflt_min

    def num(key):
        v = j.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    return FieldSpec(t, min_len, max_len, num("minValue"), num("maxValue"), j.get("#name", ""))
