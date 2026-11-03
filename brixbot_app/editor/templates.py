"""New-item templates: an empty project, and fresh components, functions,
sockets, globals and operations with free ids. Every template is a valid
starting point in the hub's config format; Verify reports what still needs
filling in (e.g. a message with no socket chosen).
"""

from __future__ import annotations

import string

from ..engine.hardware import SLOT_KEY, Slot
from ..engine.project import DEFAULT_HARDWARE
from ..engine.specs import APP_SPECS, HUB_SPECS, SIDE_HUB

ID_CHARS = string.ascii_uppercase + string.digits


def specs_for(side: str):
    return HUB_SPECS if side == SIDE_HUB else APP_SPECS


def new_project() -> dict:
    return {
        "hardware": DEFAULT_HARDWARE,
        "hub": {"globals": [], "components": [], "functions": []},
        "sockets": [],
        "app": {"globals": [], "components": [], "functions": []},
    }


def free_component_id(components: list, ctype: str) -> str:
    used = {c.get("id") for c in components if isinstance(c, dict) and c.get("type") == ctype}
    return next((ch for ch in ID_CHARS if ch not in used), "?")


def new_component(side: str, ctype: str, components: list, slot: Slot | None = None) -> dict:
    """slot: the hardware slot a hub component takes (recorded as hwSlot)."""
    cid = free_component_id(components, ctype)
    c = {"type": ctype, "id": cid, "#name": f"{ctype} {cid}" + (f" ({slot.pin})" if slot and slot.pin else "")}
    if slot is not None:
        c[SLOT_KEY] = slot.index
    return c


def next_function_id(functions: list):
    """One past the highest numeric id, in the same style (str / int) as the others."""
    ids = [f.get("id") for f in functions if isinstance(f, dict)]
    nums = [int(i) for i in ids if (isinstance(i, int) and not isinstance(i, bool)) or (isinstance(i, str) and i.isdigit())]
    n = max(nums) + 1 if nums else 0
    as_str = not ids or isinstance(ids[0], str)
    return str(n) if as_str else n


def new_function(kind: str, functions: list) -> dict:
    fid = next_function_id(functions)
    f = {"id": fid, "#name": f"function {fid}", "type": kind}
    if kind == "conditional":
        f["conditionBlock"] = {"conditionGroups": [new_group("if")]}
    else:
        f["operationBlock"] = []
    return f


def new_group(gtype: str) -> dict:
    g = {"type": gtype}
    if gtype != "else":
        g["conditions"] = [new_condition()]
    g["onTrue"] = []
    return g


def new_condition() -> dict:
    # valid as is (0 == 0); the user then picks the real left / right values
    return {"leftFixedVal": 0, "operator": "equal", "rightFixedVal": 0}


def free_protocol(sockets: list) -> str:
    used = {s.get("protocol") for s in sockets if isinstance(s, dict)}
    return next((f"SOCK{ch}" for ch in ID_CHARS if f"SOCK{ch}" not in used), "SOCK?")


def new_socket(sockets: list) -> dict:
    return {"protocol": free_protocol(sockets), "commonArgs": [], "schemas": [new_schema()]}


def new_schema() -> dict:
    return {"constantArgs": [], "args": []}


def new_field() -> dict:
    return {"#name": "", "type": "uint8"}


def new_constant() -> dict:
    return {"type": "char", "value": "X"}


def new_global() -> dict:
    return {"#name": "new global", "type": "uint8", "value": 0}


def new_var(vtype: str = "uint8") -> dict:
    return {"#name": "", "type": vtype}


def new_operation(kind: str, side: str, components: list, sockets: list) -> dict:
    if kind == "command":
        c = next((c for c in components if isinstance(c, dict) and c.get("type") in specs_for(side)), None)
        if c is None:
            return {"type": "command", "componentType": next(iter(specs_for(side))), "componentId": "A",
                    "commandId": 1}
        spec = specs_for(side)[c["type"]]
        cmd = spec.commands[0] if spec.commands else None
        op = {"type": "command", "componentType": c["type"], "componentId": c.get("id"),
              "commandId": cmd.id if cmd else 1}
        if cmd and cmd.num_in > 0:
            op["inArgs"] = [{"fixedVal": 0} for _ in range(cmd.num_in)]
        return op
    if kind == "statement":
        return {"type": "statement", "leftVar": {"globalVariableIndex": 0}, "operator": "equals", "rightFixedVal": 0}
    if kind == "message":
        s = next((s for s in sockets if isinstance(s, dict)), None)
        return {"type": "message", "protocol": s.get("protocol") if s else "", "schema": 0, "args": []}
    raise ValueError(kind)


def new_binding(functions: list) -> dict:
    f = next((f for f in functions if isinstance(f, dict)), None)
    return {"functionId": f.get("id") if f else "0", "args": None}


def new_cond_var(globals_: list) -> dict:
    return {"type": "global", "globalVariableIndex": 0}
