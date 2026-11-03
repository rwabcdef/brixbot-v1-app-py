"""Controls: connect a socket schema to one command of a component.

Connecting writes ordinary items (the config format is unchanged):
  - a socket and / or schema when asked for a new one, its args matching the
    command's in-args;
  - a small "control function" (marked "#control": true) that runs just that
    command, taking the in-args as its args;
  - a binding of the schema to that function (side's binding key: hub
    "functions", app "appFunctions"), schema arg i -> function arg i.

new_event_handler() is the event side of the same idea: a new function whose
args match an event's out values, bound to that event (out value i -> arg i).

connections() finds them again (any schema bound to a function that runs the
command directly on the component); disconnect() removes a binding and, if
nothing else uses it, the control function it ran.

All functions take the project data dict and change it in place: call them
inside Document.apply(). No Qt.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..engine.builder import BINDING_KEY
from ..engine.specs import ComponentSpec, CommandSpec, EventSpec, NONE
from ..engine.variable import TYPE_NAMES
from . import templates
from .refs import _dicts, function_users, iter_operations, same_id, section

# Used when a command does not declare its in-arg types (CommandSpec.in_types;
# the hub firmware declares none): by in-arg name, else DEFAULT_ARG_TYPE.
ARG_TYPES = {"timeout_mS": "uint16"}
DEFAULT_ARG_TYPE = "uint8"
_CONST_PREF = {"on": "1", "off": "0"}
_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass(frozen=True)
class Connection:
    socket: int            # index into sockets
    schema: int            # index into that socket's schemas
    binding: int           # index into the schema's binding list
    protocol: str
    function_id: object
    payload: str           # the schema's constant chars, e.g. "1"

    @property
    def label(self) -> str:
        return f"{self.protocol} '{self.payload}'" if self.payload else f"{self.protocol} schema {self.schema}"


def control_commands(spec: ComponentSpec | None) -> list[CommandSpec]:
    """Commands a message can trigger: those with no return value."""
    if spec is None:
        return []
    return [c for c in spec.commands if c.ret_type == NONE and c.num_in >= 0]


def _runs(fn: dict, comp: dict, command_id: int) -> bool:
    return any(op.get("type") == "command" and op.get("componentType") == comp.get("type")
               and same_id(op.get("componentId"), comp.get("id")) and op.get("commandId") == command_id
               for op in iter_operations(fn))


def _function(data: dict, side: str, fid) -> dict | None:
    return next((f for f in _dicts(section(data, side).get("functions")) if same_id(f.get("id"), fid)), None)


def _payload(sc: dict) -> str:
    return "".join(str(c.get("value")) for c in _dicts(sc.get("constantArgs")))


def connections(data: dict, side: str, comp: dict, command_id: int) -> list[Connection]:
    key = BINDING_KEY[side]
    out = []
    for si, s in enumerate(data.get("sockets") or []):
        if not isinstance(s, dict):
            continue
        for ci, sc in enumerate(s.get("schemas") or []):
            if not isinstance(sc, dict):
                continue
            for bi, b in enumerate(sc.get(key) or []):
                if not isinstance(b, dict):
                    continue
                fn = _function(data, side, b.get("functionId"))
                if fn is not None and _runs(fn, comp, command_id):
                    out.append(Connection(si, ci, bi, str(s.get("protocol")), b.get("functionId"), _payload(sc)))
    return out


def all_connections(data: dict, side: str, comp: dict, spec: ComponentSpec | None) -> list[tuple[CommandSpec, Connection]]:
    return [(cmd, c) for cmd in control_commands(spec) for c in connections(data, side, comp, cmd.id)]


def arg_type(name: str, cmd: CommandSpec | None = None, i: int = 0) -> str:
    if cmd is not None and i < len(cmd.in_types):
        return TYPE_NAMES[cmd.in_types[i]]
    return ARG_TYPES.get(name, DEFAULT_ARG_TYPE)


def _comp_name(comp: dict) -> str:
    return comp.get("#name") or f"{comp.get('type')} {comp.get('id')}"


def new_protocol(sockets: list, comp: dict) -> str:
    """5 chars named after the component, e.g. LEDG0, TIMA1."""
    used = {s.get("protocol") for s in sockets if isinstance(s, dict)}
    head = (str(comp.get("type") or "X").upper()[:3]).ljust(3, "X") + str(comp.get("id") or "X")[:1]
    for ch in "0123456789" + _CHARS[:26]:
        if head + ch not in used:
            return head + ch
    return templates.free_protocol(sockets)


def new_constant(schemas: list, cmd: CommandSpec) -> str:
    used = {_payload(sc)[:1] for sc in schemas if isinstance(sc, dict)}
    for ch in [_CONST_PREF.get(cmd.name, ""), cmd.name[:1].upper()] + list(_CHARS):
        if ch and ch not in used:
            return ch
    return "?"


def _control_function(data: dict, side: str, comp: dict, cmd: CommandSpec) -> tuple[object, bool]:
    """(id, created) of the control function for this command, reusing one if it exists."""
    sec = data.setdefault(side, {})
    fns = sec.setdefault("functions", [])
    for f in _dicts(fns):
        if f.get("#control") and _runs(f, comp, cmd.id) and len(list(iter_operations(f))) == 1:
            return f.get("id"), False
    fid = templates.next_function_id(fns)
    op = {"type": "command", "componentType": comp.get("type"), "componentId": comp.get("id"), "commandId": cmd.id}
    n = max(cmd.num_in, 0)
    if n:
        op["inArgs"] = [{"argIndex": i} for i in range(n)]
    f = {"id": fid, "#name": f"{_comp_name(comp)} {cmd.name}", "#control": True, "type": "basic"}
    if n:
        f["args"] = [{"#name": cmd.in_names[i] if i < len(cmd.in_names) else f"arg {i}",
                      "type": arg_type(cmd.in_names[i] if i < len(cmd.in_names) else "", cmd, i)} for i in range(n)]
    f["operationBlock"] = [op]
    fns.append(f)
    return fid, True


def connect(data: dict, side: str, comp: dict, cmd: CommandSpec,
            socket: int | None = None, schema: int | None = None) -> list[str]:
    """Bind a schema to the command. socket / schema None = make a new one.
    Returns notes on what was made."""
    notes = []
    sockets = data.setdefault("sockets", [])
    n = max(cmd.num_in, 0)
    names = [cmd.in_names[i] if i < len(cmd.in_names) else f"arg {i}" for i in range(n)]
    if socket is None:
        proto = new_protocol(sockets, comp)
        sockets.append({"protocol": proto, "#comment": f"control {_comp_name(comp)}", "commonArgs": [],
                        "schemas": []})
        socket = len(sockets) - 1
        notes.append(f"made socket {proto}")
    s = sockets[socket]
    schemas = s.setdefault("schemas", [])
    if schema is None:
        const = new_constant(schemas, cmd)
        schemas.append({"#comment": f"{_comp_name(comp)} {cmd.name}",
                        "constantArgs": [{"type": "char", "value": const}],
                        "args": [{"#name": nm, "type": arg_type(nm, cmd, i)} for i, nm in enumerate(names)]})
        schema = len(schemas) - 1
        notes.append(f"made schema {schema} '{const}' in {s.get('protocol')}")
    sc = schemas[schema]
    fid, created = _control_function(data, side, comp, cmd)
    if created:
        notes.append(f"made function {fid} ({_comp_name(comp)} {cmd.name})")
    n_args = len(_dicts(sc.get("args")))
    args = [{"argsIndex": i} if i < n_args else {"fixedVal": 0} for i in range(n)]
    sc.setdefault(BINDING_KEY[side], []).append({"functionId": fid, "args": args if n else None})
    notes.append(f"{s.get('protocol')} schema {schema} now runs {_comp_name(comp)} {cmd.name}")
    return notes


def disconnect(data: dict, side: str, conn: Connection) -> list[str]:
    notes = []
    sc = data["sockets"][conn.socket]["schemas"][conn.schema]
    lst = sc.get(BINDING_KEY[side])
    if isinstance(lst, list) and 0 <= conn.binding < len(lst):
        lst.pop(conn.binding)
        if not lst:
            sc.pop(BINDING_KEY[side], None)
        notes.append(f"{conn.label} disconnected")
    fn = _function(data, side, conn.function_id)
    if fn is not None and fn.get("#control") and not function_users(data, side, conn.function_id):
        section(data, side)["functions"].remove(fn)
        notes.append(f"removed unused control function {conn.function_id}")
    return notes


def new_event_handler(data: dict, side: str, comp_index: int, es: EventSpec) -> tuple[object, list[str]]:
    """Make a function taking the event's out values as its args and bind it to the
    event of component comp_index. Returns (function id, notes)."""
    sec = data.setdefault(side, {})
    comp = sec["components"][comp_index]
    fns = sec.setdefault("functions", [])
    fid = templates.next_function_id(fns)
    name = f"{_comp_name(comp)} {es.name}"
    f = {"id": fid, "#name": name, "type": "basic"}
    if es.out_types:
        f["args"] = [{"#name": es.out_names[i] if i < len(es.out_names) else f"value {i}", "type": TYPE_NAMES[t]}
                     for i, t in enumerate(es.out_types)]
    f["operationBlock"] = []
    fns.append(f)
    events = comp.setdefault("events", [])
    ev = next((e for e in events if isinstance(e, dict) and e.get("eventId") == es.id), None)
    if ev is None:
        ev = {"eventId": es.id}
        events.append(ev)
    binding = {"functionId": fid,
               "args": [{"outArgIndex": i} for i in range(len(es.out_types))] if es.out_types else None}
    if not isinstance(ev.get("functions"), list):
        ev["functions"] = []
    ev["functions"].append(binding)
    return fid, [f"made function {fid} ({name}), run on {es.name}"]
