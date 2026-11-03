"""Cross references inside a project: who uses a function, component or
socket, renaming them everywhere, and short readable text for operations,
conditions and whole functions (the editor's summaries and tooltips).

A side is "hub" or "app". Functions and components belong to one side;
sockets are shared, and each schema binds functions of both sides
("functions" = hub, "appFunctions" = app).
"""

from __future__ import annotations

from typing import Iterator

from ..engine.builder import BINDING_KEY
from ..engine.specs import COMMAND_RESOLVE
from .templates import specs_for


def _arr(v) -> list:
    return v if isinstance(v, list) else []


def _dicts(v) -> list[dict]:
    return [x for x in _arr(v) if isinstance(x, dict)]


def same_id(a, b) -> bool:
    return a is not None and b is not None and str(a) == str(b)


def section(data: dict, side: str) -> dict:
    s = data.get(side)
    return s if isinstance(s, dict) else {}


# ------------------------------------------------------------------ walking
def iter_operations(fn: dict) -> Iterator[dict]:
    yield from _dicts(fn.get("operationBlock"))
    cb = fn.get("conditionBlock")
    if isinstance(cb, dict):
        for g in _dicts(cb.get("conditionGroups")):
            yield from _dicts(g.get("onTrue"))


def iter_commands(fn: dict) -> Iterator[dict]:
    """Every command dict of a function: operations, condVars and statement right sides."""
    for op in iter_operations(fn):
        if op.get("type") == "command":
            yield op
        elif op.get("type") == "statement":
            r = op.get("rightVar")
            if isinstance(r, dict) and (r.get("commandId") is not None or r.get("componentType") is not None):
                yield r
    for cv in _dicts(fn.get("condVars")):
        if cv.get("type") == "command":
            yield cv


def iter_bindings(data: dict, side: str) -> Iterator[tuple[str, dict]]:
    """(where, binding) for every function binding of a side."""
    sec = section(data, side)
    for c in _dicts(sec.get("components")):
        for ev in _dicts(c.get("events")):
            for b in _dicts(ev.get("functions")):
                yield f"{c.get('type')} {c.get('id')} event {ev.get('eventId')}", b
    key = BINDING_KEY[side]
    for s in _dicts(data.get("sockets")):
        for si, sc in enumerate(_dicts(s.get("schemas"))):
            for b in _dicts(sc.get(key)):
                yield f"socket {s.get('protocol')} schema {si}", b


# ------------------------------------------------------------------ users
def function_users(data: dict, side: str, fid) -> list[str]:
    return [where for where, b in iter_bindings(data, side) if same_id(b.get("functionId"), fid)]


def component_users(data: dict, side: str, ctype: str, cid: str) -> list[str]:
    out = []
    for f in _dicts(section(data, side).get("functions")):
        for cmd in iter_commands(f):
            if cmd.get("componentType") == ctype and cmd.get("componentId") == cid:
                out.append(f"function {f.get('#name') or f.get('id')}")
                break
    return out


def socket_users(data: dict, protocol: str) -> list[str]:
    out = []
    for side in ("hub", "app"):
        for f in _dicts(section(data, side).get("functions")):
            if any(op.get("type") == "message" and op.get("protocol") == protocol for op in iter_operations(f)):
                out.append(f"{side} function {f.get('#name') or f.get('id')}")
    return out


def global_users(data: dict, side: str, index: int) -> list[str]:
    out = []
    for f in _dicts(section(data, side).get("functions")):
        if _uses_global(f, index):
            out.append(f"function {f.get('#name') or f.get('id')}")
    return out


def _uses_global(o, index: int) -> bool:
    if isinstance(o, dict):
        if o.get("globalVariableIndex") == index:
            return True
        return any(_uses_global(v, index) for v in o.values())
    if isinstance(o, list):
        return any(_uses_global(v, index) for v in o)
    return False


# ------------------------------------------------------------------ renames
def rename_function(data: dict, side: str, old, new) -> int:
    n = 0
    for _, b in iter_bindings(data, side):
        if same_id(b.get("functionId"), old):
            b["functionId"] = new
            n += 1
    return n


def rename_component(data: dict, side: str, old: tuple[str, str], new: tuple[str, str]) -> int:
    n = 0
    for f in _dicts(section(data, side).get("functions")):
        for cmd in iter_commands(f):
            if (cmd.get("componentType"), cmd.get("componentId")) == old and "componentId" in cmd:
                cmd["componentType"], cmd["componentId"] = new
                n += 1
    return n


def rename_socket(data: dict, old: str, new: str) -> int:
    n = 0
    for side in ("hub", "app"):
        for f in _dicts(section(data, side).get("functions")):
            for op in iter_operations(f):
                if op.get("type") == "message" and op.get("protocol") == old:
                    op["protocol"] = new
                    n += 1
    return n


def reindex_globals(data: dict, side: str, mapping: dict[int, int | None]) -> None:
    """Rewrite globalVariableIndex after globals were moved / removed (None = removed, left as is)."""
    def walk(o):
        if isinstance(o, dict):
            gi = o.get("globalVariableIndex")
            if isinstance(gi, int) and mapping.get(gi) is not None:
                o["globalVariableIndex"] = mapping[gi]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(_dicts(section(data, side).get("functions")))


# ------------------------------------------------------------------ text
def _var_name(lst, i, prefix: str) -> str:
    lst = _arr(lst)
    if isinstance(i, int) and 0 <= i < len(lst) and isinstance(lst[i], dict) and lst[i].get("#name"):
        return lst[i]["#name"]
    return f"{prefix}{i}"


def fixed_text(v) -> str:
    if isinstance(v, dict):
        v = v.get("value")
    if isinstance(v, str):
        return repr(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def ref_text(ref, fn: dict | None, globals_: list | None) -> str:
    if not isinstance(ref, dict):
        return "?" if ref is None else fixed_text(ref)
    fn = fn or {}
    if ref.get("argIndex") is not None:
        return _var_name(fn.get("args"), ref["argIndex"], "arg")
    if ref.get("tempVarIndex") is not None:
        return _var_name(fn.get("tempVars"), ref["tempVarIndex"], "temp")
    if ref.get("condVarIndex") is not None:
        return _var_name(fn.get("condVars"), ref["condVarIndex"], "cond")
    if ref.get("globalVariableIndex") is not None:
        return _var_name(globals_, ref["globalVariableIndex"], "global")
    for k in ("fixedVal", "fixedValue"):
        if ref.get(k) is not None:
            return fixed_text(ref[k])
    if ref.get("commandId") is not None:
        return command_text(ref, "hub", fn, globals_)
    return "?"


STATEMENT_SYMBOLS = {"equals": "=", "plusequals": "+=", "minusequals": "-=", "multiplyequals": "*=",
                     "divideequals": "/="}
CONDITION_SYMBOLS = {"equal": "==", "notequal": "!=", "greaterthan": ">", "greaterthanorequal": ">=",
                     "lessthan": "<", "lessthanorequal": "<="}


def command_text(op: dict, side: str, fn: dict | None, globals_: list | None) -> str:
    ctype = op.get("componentType") or "?"
    if op.get("componentId") is not None:
        target = f"{ctype} {op['componentId']}"
    elif op.get("componentIdArg") is not None:
        a = op["componentIdArg"]
        target = f"{ctype}[{ref_text({'argIndex': a} if isinstance(a, int) else a, fn, globals_)}]"
    elif op.get("componentPtr") is not None:
        target = f"*{ref_text(op['componentPtr'], fn, globals_)}"
    else:
        target = ctype
    cid = op.get("commandId")
    spec = specs_for(side).get(ctype)
    if cid == COMMAND_RESOLVE:
        name = "resolve"
    else:
        c = spec.command(cid) if spec and isinstance(cid, int) else None
        name = c.name if c else f"command{cid}"
    args = ", ".join(ref_text(a, fn, globals_) for a in _arr(op.get("inArgs")))
    text = f"{target}.{name}({args})"
    if op.get("retArg") is not None:
        text = f"{ref_text(op['retArg'], fn, globals_)} = {text}"
    return text


def operation_text(op: dict, side: str, fn: dict | None, globals_: list | None) -> str:
    t = op.get("type")
    if t == "command":
        return command_text(op, side, fn, globals_)
    if t == "statement":
        right = op.get("rightVar")
        if right is None and op.get("rightFixedVal") is not None:
            r = fixed_text(op["rightFixedVal"])
        elif isinstance(right, dict) and right.get("commandId") is not None:
            r = command_text(right, side, fn, globals_)
        else:
            r = ref_text(right, fn, globals_)
        sym = STATEMENT_SYMBOLS.get(op.get("operator"), op.get("operator"))
        return f"{ref_text(op.get('leftVar'), fn, globals_)} {sym} {r}"
    if t == "message":
        args = ", ".join(ref_text(a, fn, globals_) for a in _arr(op.get("args")))
        return f"send {op.get('protocol')}[{op.get('schema')}]({args})"
    return f"<{t}>"


def condition_text(c: dict, fn: dict | None, globals_: list | None) -> str:
    def side_text(var_key, fixed_key):
        if c.get(var_key) is not None:
            return ref_text(c[var_key], fn, globals_)
        if c.get(fixed_key) is not None:
            return fixed_text(c[fixed_key])
        return "?"
    sym = CONDITION_SYMBOLS.get(c.get("operator"), c.get("operator"))
    return f"{side_text('leftVar', 'leftFixedVal')} {sym} {side_text('rightVar', 'rightFixedVal')}"


def function_text(fn: dict, side: str, globals_: list | None) -> str:
    """Pseudo code of a function, as in the product spec's example."""
    args = ", ".join(_var_name(fn.get("args"), i, "arg") for i in range(len(_arr(fn.get("args")))))
    lines = [f"{fn.get('#name') or 'function'} #{fn.get('id')}({args}):"]
    for i, cv in enumerate(_dicts(fn.get("condVars"))):
        src = command_text(cv, side, fn, globals_) if cv.get("type") == "command" else \
            _var_name(globals_, cv.get("globalVariableIndex"), "global")
        lines.append(f"  {_var_name(fn.get('condVars'), i, 'cond')} <- {src}")
    cb = fn.get("conditionBlock")
    if fn.get("type") in ("conditional", "logic") and isinstance(cb, dict):
        for g in _dicts(cb.get("conditionGroups")):
            conds = _dicts(g.get("conditions"))
            cons = _arr(g.get("connectors"))
            parts = []
            for i, c in enumerate(conds):
                if i:
                    parts.append("&&" if (cons[i - 1] if i - 1 < len(cons) else "and") == "and" else "||")
                parts.append(f"({condition_text(c, fn, globals_)})")
            head = "else:" if g.get("type") == "else" else f"{g.get('type')} {' '.join(parts)}:"
            lines.append("  " + head)
            ops = _dicts(g.get("onTrue"))
            lines += [f"    {operation_text(o, side, fn, globals_)}" for o in ops] or ["    pass"]
    else:
        ops = _dicts(fn.get("operationBlock"))
        lines += [f"  {operation_text(o, side, fn, globals_)}" for o in ops] or ["  pass"]
    return "\n".join(lines)
