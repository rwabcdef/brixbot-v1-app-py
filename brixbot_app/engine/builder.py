"""Config builder - Python port of Brix_ConfigBuilder.cpp, used for both sides:

* hub: validates the hub part of a project before it is generated / loaded,
  with the same checks, error codes, sections and indices as the firmware
  (except arena / JSON memory, which only the hub can measure),
* app: builds the PC app's own runtime.

Passes (section char in brackets): globals [G], components [C], sockets [S],
functions [F], socket bindings [B], component event bindings [E].
"""

from __future__ import annotations

from typing import Callable

from . import errors as E
from .runtime import (
    AND, CONDITION_OPS, ELIF, ELSE, IF, OR, STATEMENT_OPS, ArgBinding, Command, Component, Condition,
    ConditionGroup, Function, FunctionBinding, Message, Program, SpecComponent, Statement, VariableSource,
)
from .sockets import Schema, SocketDef, parse_field
from .specs import (
    APP_SPECS, COMMAND_RESOLVE, HUB_LIMITS, HUB_SPECS, HUB_UNSUPPORTED, SIDE_APP, SIDE_HUB, VARIADIC,
    ComponentSpec,
)
from .variable import (
    BOOL, CHAR, COMPONENT, FLOAT, INT8, INT16, NONE, STR10, STR10_LEN, UINT8, UINT16, Variable, type_from_name,
)

ComponentFactory = Callable[[ComponentSpec, str, dict], Component]

# socket schema key holding each side's function bindings
BINDING_KEY = {SIDE_HUB: "functions", SIDE_APP: "appFunctions"}


def _arr(v) -> list:
    return v if isinstance(v, list) else []


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _index(v, limit: int, what: str) -> int:
    if not _is_int(v) or not 0 <= v < limit:
        raise E.BuildError(E.BAD_INDEX, f"{what} {v!r} (have {limit})")
    return v


def _read_id(v) -> int:
    if _is_int(v) and 0 <= v <= 65535:
        return v
    if isinstance(v, str) and v.isdigit() and int(v) <= 65535:
        return int(v)
    raise E.BuildError(E.MISSING_FIELD if v is None else E.BAD_INDEX, f"id {v!r}")


def set_value(var: Variable, value) -> None:
    """Set a typed variable from a JSON value (None leaves it zero)."""
    if value is None:
        return
    t = var.type
    bad = E.BuildError(E.BAD_VALUE, f"{value!r} for {var!r}")
    if t == CHAR:
        if isinstance(value, str):
            if len(value) != 1:
                raise bad
            var.value = value
        elif _is_int(value):
            var.value = chr(value & 0xFF)
        else:
            raise bad
    elif t == STR10:
        if not isinstance(value, str) or len(value) > STR10_LEN:
            raise bad
        var.value = value
    elif t == BOOL:
        if isinstance(value, bool):
            var.value = value
        elif _is_int(value):
            var.value = value != 0
        else:
            raise bad
    elif t == FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise bad
        var.value = float(value)
    elif t in (UINT8, UINT16, INT8, INT16):
        if not _is_int(value):
            raise bad
        lo, hi = {UINT8: (0, 255), UINT16: (0, 65535), INT8: (-128, 127), INT16: (-32768, 32767)}[t]
        if not lo <= value <= hi:
            raise bad
        var.value = value
    else:
        raise bad


def make_fixed(v) -> Variable:
    """{ "type": "uint8", "value": 4 } or a bare value (type inferred like the hub)."""
    if isinstance(v, dict):
        t = type_from_name(v.get("type"))
        if t is None or t in (NONE, COMPONENT):
            raise E.BuildError(E.BAD_TYPE, f"fixed value type {v.get('type')!r}")
        var = Variable(t)
        set_value(var, v.get("value"))
        return var
    if isinstance(v, bool):
        return Variable(BOOL, v)
    if _is_int(v):
        if 0 <= v <= 255:
            t = UINT8
        elif 0 <= v <= 65535:
            t = UINT16
        else:
            t = INT16
        var = Variable(t)
        set_value(var, v)
        return var
    if isinstance(v, float):
        return Variable(FLOAT, v)
    if isinstance(v, str):
        var = Variable(CHAR if len(v) == 1 else STR10)
        set_value(var, v)
        return var
    raise E.BuildError(E.BAD_VALUE, f"fixed value {v!r}")


class Builder:
    def __init__(self, side: str, factory: ComponentFactory | None = None):
        self.side = side
        self.specs = HUB_SPECS if side == SIDE_HUB else APP_SPECS
        self.factory = factory or (lambda spec, cid, cfg: SpecComponent(spec, cid, cfg))
        self.p = Program(side)
        self._slots: dict[str, int] = {}

    # ================================================================ entry
    def build(self, section: dict, sockets: list) -> Program:
        if not isinstance(section, dict):
            section = {}
        hub = self.side == SIDE_HUB

        def each(items, sec, fn):
            for i, item in enumerate(_arr(items)):
                try:
                    if not isinstance(item, dict):
                        raise E.BuildError(E.MISSING_FIELD, "expected an object")
                    fn(item, i)
                except E.BuildError as exc:
                    exc.section, exc.index = sec, i
                    raise

        try:
            g = _arr(section.get("globals"))
            if hub and len(g) > HUB_LIMITS["globals"]:
                raise E.BuildError(E.TOO_MANY, "globals")
            self.p.globals = self._typed_vars(g)
        except E.BuildError as exc:
            exc.section = E.SECTION_GLOBALS
            raise
        each(section.get("components"), E.SECTION_COMPONENTS, lambda o, i: self._component(o))
        each(sockets, E.SECTION_SOCKETS, lambda o, i: self._socket(o))
        each(section.get("functions"), E.SECTION_FUNCTIONS, lambda o, i: self._function(o))
        each(sockets, E.SECTION_BINDINGS, lambda o, i: self._socket_bindings(o))
        each(section.get("components"), E.SECTION_EVENTS, lambda o, i: self._listener(o, i))
        return self.p

    # ================================================================ helpers
    def _typed_vars(self, arr) -> list[Variable]:
        out = []
        for v in _arr(arr):
            t = type_from_name(v.get("type") if isinstance(v, dict) else None)
            if t is None:
                raise E.BuildError(E.BAD_TYPE, f"type {v!r}")
            var = Variable(t)
            set_value(var, v.get("value"))
            out.append(var)
        return out

    def _ref(self, ref, fn: Function | None) -> Variable | None:
        """ArgRef -> Variable, None when ref names nothing."""
        if ref is None:
            return None
        if not isinstance(ref, dict):
            raise E.BuildError(E.MISSING_FIELD, f"arg ref {ref!r}")
        if ref.get("argIndex") is not None:
            return fn.args[_index(ref["argIndex"], len(fn.args) if fn else 0, "argIndex")]
        if ref.get("tempVarIndex") is not None:
            return fn.temp_vars[_index(ref["tempVarIndex"], len(fn.temp_vars) if fn else 0, "tempVarIndex")]
        if ref.get("condVarIndex") is not None:
            return fn.cond_vars[_index(ref["condVarIndex"], len(fn.cond_vars) if fn else 0, "condVarIndex")]
        if ref.get("globalVariableIndex") is not None:
            return self.p.globals[_index(ref["globalVariableIndex"], len(self.p.globals), "globalVariableIndex")]
        for key in ("fixedVal", "fixedValue"):
            if ref.get(key) is not None:
                return make_fixed(ref[key])
        return None

    def _require(self, ref, fn: Function | None, what: str) -> Variable:
        v = self._ref(ref, fn)
        if v is None:
            raise E.BuildError(E.MISSING_FIELD, what)
        return v

    # ================================================================ C: components
    def _component(self, o: dict) -> None:
        tname = o.get("type")
        cid = o.get("id")
        if not isinstance(cid, str) or len(cid) != 1:
            raise E.BuildError(E.MISSING_FIELD, f"component id {cid!r} (one char)")
        spec = self.specs.get(tname)
        if spec is None:
            raise E.BuildError(E.UNSUPPORTED_COMPONENT, f"{self.side} has no component type {tname!r}")
        if self.p.resolve(tname, cid) is not None:
            raise E.BuildError(E.DUPLICATE_ID, f"{tname} {cid}")
        if spec.slots is not None:
            used = self._slots.get(tname, 0)
            if used >= spec.slots:
                raise E.BuildError(E.NO_HW_SLOT, f"hub has {spec.slots} {tname} slots")
            self._slots[tname] = used + 1
        for a in _arr(o.get("args")):
            v = a.get("value") if isinstance(a, dict) else None
            if self.side == SIDE_HUB and _is_int(v) and not 0 <= v <= 65535:
                raise E.BuildError(E.BAD_VALUE, f"arg {a.get('key')}")
        if self.side == SIDE_HUB and len(self.p.components) >= HUB_LIMITS["components"]:
            raise E.BuildError(E.TOO_MANY, "components")
        self.p.components.append(self.factory(spec, cid, o))

    # ================================================================ S: sockets
    def _socket(self, o: dict) -> None:
        proto = o.get("protocol")
        if not isinstance(proto, str) or len(proto) != 5:
            raise E.BuildError(E.MISSING_FIELD, f"protocol {proto!r} (5 chars)")
        if proto == "CONFG" or proto in self.p.sockets:
            raise E.BuildError(E.DUPLICATE_ID, proto)
        common = [parse_field(f) for f in _arr(o.get("commonArgs"))]
        schemas = []
        for sc in _arr(o.get("schemas")):
            consts = [parse_field(f) for f in _arr(sc.get("constantArgs"))]
            values = []
            for f, c in zip(consts, _arr(sc.get("constantArgs"))):
                if c.get("value") is None:
                    raise E.BuildError(E.MISSING_FIELD, "constantArgs value")
                v = Variable(f.type)
                set_value(v, c["value"])
                values.append(v)
            args = [parse_field(f) for f in _arr(sc.get("args"))]
            schemas.append(Schema(consts, values, args, bool(sc.get("ack", False)), sc.get("#comment", "")))
        sock = SocketDef(proto, common, schemas, o.get("#comment", ""))
        sock.validate()
        if self.side == SIDE_HUB and len(self.p.sockets) >= HUB_LIMITS["sockets"]:
            raise E.BuildError(E.TOO_MANY, "sockets")
        self.p.sockets[proto] = sock

    # ================================================================ F: functions
    def _command(self, op: dict, fn: Function) -> Command:
        cmd = Command()
        cmd.program = self.p
        tname = op.get("componentType")
        if tname is not None:
            if tname not in self.specs:
                raise E.BuildError(E.UNSUPPORTED_COMPONENT, f"component type {tname!r}")
            cmd.component_type = tname
        if not _is_int(op.get("commandId")):
            raise E.BuildError(E.MISSING_FIELD, "commandId")
        cmd.command_id = op["commandId"]

        cid = op.get("componentId")
        if cid is not None:
            if not isinstance(cid, str) or len(cid) != 1:
                raise E.BuildError(E.MISSING_FIELD, f"componentId {cid!r}")
            cmd.component = self.p.resolve(cmd.component_type, cid)
            if cmd.component is None:
                raise E.BuildError(E.UNKNOWN_COMPONENT, f"{tname} {cid}")
        elif op.get("componentIdArg") is not None:
            a = op["componentIdArg"]
            if _is_int(a):
                cmd.component_ref = fn.args[_index(a, len(fn.args), "componentIdArg")]
            else:
                cmd.component_ref = self._require(a, fn, "componentIdArg")
            if not cmd.component_type:
                raise E.BuildError(E.MISSING_FIELD, "componentType (needed with componentIdArg)")
        elif op.get("componentPtr") is not None:
            cmd.component_ref = self._require(op["componentPtr"], fn, "componentPtr")
        else:
            raise E.BuildError(E.MISSING_FIELD, "componentId / componentIdArg / componentPtr")

        cmd.in_args = [self._require(r, fn, "inArgs") for r in _arr(op.get("inArgs"))]
        cmd.out_args = [self._require(r, fn, "outArgs") for r in _arr(op.get("outArgs"))]
        cmd.ret_arg = self._ref(op.get("retArg"), fn)

        if cmd.command_id != COMMAND_RESOLVE and cmd.component_type:
            spec = self.specs[cmd.component_type].command(cmd.command_id)
            if spec is None:
                raise E.BuildError(E.BAD_COMMAND, f"{cmd.component_type} has no command {cmd.command_id}")
            if spec.num_in != VARIADIC and spec.num_in != len(cmd.in_args):
                raise E.BuildError(E.BAD_COMMAND,
                                   f"{cmd.component_type}.{spec.name} takes {spec.num_in} inArgs, got {len(cmd.in_args)}")
            if len(cmd.out_args) > spec.num_out:
                raise E.BuildError(E.BAD_COMMAND, f"{cmd.component_type}.{spec.name} outArgs")
        return cmd

    def _command_source(self, op: dict, fn: Function) -> VariableSource:
        cmd = self._command(op, fn)
        src = VariableSource(command=cmd)
        if op.get("outArgIndex") is not None:
            spec = self.specs[cmd.component_type].command(cmd.command_id) if cmd.component_type else None
            if spec is None:
                raise E.BuildError(E.BAD_COMMAND, "outArgIndex needs a known command")
            src.ret = False
            src.out_index = _index(op["outArgIndex"], spec.num_out, "outArgIndex")
            if not cmd.out_args:
                cmd.out_args = [Variable() for _ in range(spec.num_out)]
            return src
        if cmd.command_id == COMMAND_RESOLVE:
            vtype = COMPONENT
        else:
            spec = self.specs[cmd.component_type].command(cmd.command_id) if cmd.component_type else None
            if spec is None or spec.ret_type == NONE:
                raise E.BuildError(E.BAD_COMMAND, "command returns nothing to read")
            vtype = spec.ret_type
        if cmd.ret_arg is None:
            cmd.ret_arg = Variable(vtype)
        src.ret_type = vtype
        return src

    def _statement(self, op: dict, fn: Function) -> Statement:
        left = self._require(op.get("leftVar"), fn, "leftVar")
        opc = STATEMENT_OPS.get(op.get("operator"))
        if opc is None:
            raise E.BuildError(E.BAD_OPERATOR, f"statement operator {op.get('operator')!r}")
        right = op.get("rightVar")
        if isinstance(right, dict) and (right.get("commandId") is not None or right.get("componentType") is not None):
            return Statement(left, opc, right_source=self._command_source(right, fn))
        rv = self._ref(right, fn)
        if rv is None:
            if op.get("rightFixedVal") is None:
                raise E.BuildError(E.MISSING_FIELD, "rightVar / rightFixedVal")
            rv = make_fixed(op["rightFixedVal"])
        return Statement(left, opc, rv)

    def _message(self, op: dict, fn: Function) -> Message:
        sock = self.p.sockets.get(op.get("protocol"))
        if sock is None:
            raise E.BuildError(E.UNKNOWN_SOCKET, f"protocol {op.get('protocol')!r}")
        si = _index(op.get("schema"), len(sock.schemas), "schema")
        args = [self._require(r, fn, "message args") for r in _arr(op.get("args"))]
        expected = len(sock.common_args) + len(sock.schemas[si].args)
        if len(args) != expected:
            raise E.BuildError(E.BAD_SCHEMA, f"{sock.protocol} schema {si} takes {expected} args "
                                             f"(commonArgs + args), got {len(args)}")
        return Message(sock, si, args, self.p)

    def _operations(self, ops, fn: Function) -> list:
        out = []
        for op in _arr(ops):
            t = op.get("type") if isinstance(op, dict) else None
            if t == "command":
                out.append(self._command(op, fn))
            elif t == "statement":
                out.append(self._statement(op, fn))
            elif t == "message":
                out.append(self._message(op, fn))
            else:
                raise E.BuildError(E.MISSING_FIELD, f"operation type {t!r}")
        return out

    def _condition(self, c: dict, fn: Function) -> Condition:
        left = self._ref(c.get("leftVar"), fn)
        if left is None and c.get("leftFixedVal") is not None:
            left = make_fixed(c["leftFixedVal"])
        right = self._ref(c.get("rightVar"), fn)
        if right is None and c.get("rightFixedVal") is not None:
            right = make_fixed(c["rightFixedVal"])
        if left is None or right is None:
            raise E.BuildError(E.MISSING_FIELD, "condition left / right")
        opc = CONDITION_OPS.get(c.get("operator"))
        if opc is None:
            raise E.BuildError(E.BAD_OPERATOR, f"condition operator {c.get('operator')!r}")
        return Condition(left, opc, right)

    def _groups(self, cb: dict, fn: Function) -> list[ConditionGroup]:
        groups_json = _arr(cb.get("conditionGroups"))
        if not groups_json:
            raise E.BuildError(E.BAD_CONDITION_BLOCK, "no conditionGroups")
        groups = []
        for gi, gj in enumerate(groups_json):
            gt = {"if": IF, "elif": ELIF, "else": ELSE}.get(gj.get("type"))
            if gt is None:
                raise E.BuildError(E.BAD_CONDITION_BLOCK, f"group type {gj.get('type')!r}")
            first, last = gi == 0, gi == len(groups_json) - 1
            if first != (gt == IF) or (gt == ELSE and not last):
                raise E.BuildError(E.BAD_CONDITION_BLOCK, "first group must be 'if', 'else' only last")
            conds, cons = [], []
            if gt != ELSE:
                conds = [self._condition(c, fn) for c in _arr(gj.get("conditions"))]
                if not conds:
                    raise E.BuildError(E.BAD_CONDITION_BLOCK, f"group {gi} has no conditions")
                if len(conds) > 1:
                    cj = _arr(gj.get("connectors"))
                    if len(cj) != len(conds) - 1:
                        raise E.BuildError(E.BAD_CONDITION_BLOCK, f"group {gi} needs {len(conds) - 1} connectors")
                    for c in cj:
                        if c not in ("and", "or"):
                            raise E.BuildError(E.BAD_OPERATOR, f"connector {c!r}")
                        cons.append(AND if c == "and" else OR)
            groups.append(ConditionGroup(gt, conds, cons, self._operations(gj.get("onTrue"), fn)))
        return groups

    def _function(self, o: dict) -> None:
        fid = _read_id(o.get("id"))
        if fid in self.p.functions:
            raise E.BuildError(E.DUPLICATE_ID, f"function {fid}")
        t = o.get("type")
        if t not in (None, "basic", "conditional", "logic"):
            raise E.BuildError(E.MISSING_FIELD, f"function type {t!r}")
        fn = Function(fid, o.get("#name") or f"function {fid}", conditional=t in ("conditional", "logic"))
        fn.args = self._typed_vars(o.get("args"))
        fn.temp_vars = self._typed_vars(o.get("tempVars"))
        cvs = _arr(o.get("condVars"))
        fn.cond_vars = [Variable() for _ in cvs]
        for i, cv in enumerate(cvs):
            ct = cv.get("type") if isinstance(cv, dict) else None
            if ct == "command":
                src = self._command_source(cv, fn)
                fn.cond_vars[i].type = src.ret_type
            elif ct in ("global", "globalVariable"):
                g = _index(cv.get("globalVariableIndex"), len(self.p.globals), "globalVariableIndex")
                src = VariableSource(variable=self.p.globals[g])
                fn.cond_vars[i].type = self.p.globals[g].type
            else:
                raise E.BuildError(E.MISSING_FIELD, f"condVar type {ct!r}")
            fn.cond_sources.append(src)
        if fn.conditional:
            if not isinstance(o.get("conditionBlock"), dict):
                raise E.BuildError(E.BAD_CONDITION_BLOCK, "conditional function needs conditionBlock")
            fn.groups = self._groups(o["conditionBlock"], fn)
        else:
            fn.operations = self._operations(o.get("operationBlock"), fn)
        if self.side == SIDE_HUB and len(self.p.functions) >= HUB_LIMITS["functions"]:
            raise E.BuildError(E.TOO_MANY, "functions")
        self.p.functions[fid] = fn

    # ================================================================ B / E: bindings
    def _bindings(self, arr, arg_source: Callable[[dict], int]) -> list[FunctionBinding]:
        out = []
        for b in _arr(arr):
            fid = _read_id(b.get("functionId"))
            fn = self.p.functions.get(fid)
            if fn is None:
                raise E.BuildError(E.UNKNOWN_FUNCTION, f"functionId {fid}")
            args = b.get("args")
            if args is None:
                out.append(FunctionBinding(fn, None))
                continue
            if len(args) > len(fn.args):
                raise E.BuildError(E.BAD_INDEX, f"{len(args)} args for {fn.name} which takes {len(fn.args)}")
            abs_ = []
            for a in args:
                if not isinstance(a, dict):
                    raise E.BuildError(E.MISSING_FIELD, "binding arg")
                fixed_key = "fixedVal" if a.get("fixedVal") is not None else (
                    "fixedValue" if a.get("fixedValue") is not None else None)
                idx = arg_source(a)
                if idx >= 0:
                    abs_.append(ArgBinding(idx))
                elif fixed_key:
                    abs_.append(ArgBinding(-1, make_fixed(a[fixed_key])))
                else:
                    raise E.BuildError(E.MISSING_FIELD, f"binding arg {a!r}")
            out.append(FunctionBinding(fn, abs_))
        return out

    def _socket_bindings(self, o: dict) -> None:
        sock = self.p.sockets[o.get("protocol")]
        nc = len(sock.common_args)
        for si, sc in enumerate(_arr(o.get("schemas"))):
            schema = sock.schemas[si]
            nk = len(schema.constant_args)

            def src(a, schema=schema, nk=nk):
                if a.get("commonArgsIndex") is not None:
                    return _index(a["commonArgsIndex"], nc, "commonArgsIndex")
                if a.get("constantArgsIndex") is not None:
                    return nc + _index(a["constantArgsIndex"], nk, "constantArgsIndex")
                if a.get("argsIndex") is not None:
                    return nc + nk + _index(a["argsIndex"], len(schema.args), "argsIndex")
                return -1

            schema.bindings = self._bindings(sc.get(BINDING_KEY[self.side]), src)

    def _listener(self, o: dict, index: int) -> None:
        comp = self.p.components[index]
        for ev in _arr(o.get("events")):
            if not _is_int(ev.get("eventId")):
                raise E.BuildError(E.MISSING_FIELD, "eventId")
            spec = comp.spec.event(ev["eventId"])
            if spec is None:
                raise E.BuildError(E.BAD_INDEX, f"{comp.type_name} has no event {ev['eventId']}")
            n_out = len(spec.out_types)

            def src(a, n_out=n_out):
                if a.get("outArgIndex") is not None:
                    return _index(a["outArgIndex"], n_out, "outArgIndex")
                return -1

            bindings = self._bindings(ev.get("functions"), src)
            if bindings:
                self.p.handlers[(index, spec.id)] = bindings


def build_side(side: str, section: dict, sockets: list, factory: ComponentFactory | None = None) -> Program:
    return Builder(side, factory).build(section, sockets)
