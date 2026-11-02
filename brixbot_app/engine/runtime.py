"""Runtime objects built from a config - Python port of the hub's engine
(Command, Statement, Condition*, Operation*, Function, FunctionBinding,
EventListener) plus the Program that ties them together.

Differences from the hub, by design:
  * app components push their events (Qt is event driven) instead of being
    polled; Program.handle_event runs the bound functions at once,
  * Program.tick(now_ms) drives time based components (led flashing, timers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import errors as E
from .sockets import SocketDef
from .specs import COMMAND_RESOLVE, ComponentSpec
from .variable import CHAR, COMPONENT, NONE, Variable, statement_op


# ====================================================================== components
class Component:
    """Base of every component. Subclasses implement execute()."""

    def __init__(self, spec: ComponentSpec, cid: str, cfg: dict):
        self.spec = spec
        self.type_name = spec.type_name
        self.id = cid
        self.cfg = cfg
        self.name = cfg.get("#name") or f"{spec.type_name} {cid}"
        self.event_sink: Callable[[Component, int, list[Variable]], None] | None = None
        self.running = False

    def init_arg(self, key: str, default=None):
        for a in self.cfg.get("args") or []:
            if isinstance(a, dict) and a.get("key") == key:
                return a.get("value", default)
        return default

    def execute(self, cmd: Command) -> int:          # noqa: ARG002
        return E.OK

    def emit(self, event_id: int, outs: list[Variable] | None = None) -> None:
        if self.running and self.event_sink is not None:
            self.event_sink(self, event_id, outs or [])

    def id_var(self) -> Variable:
        return Variable(CHAR, self.id)

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def tick(self, now_ms: int) -> None:             # noqa: ARG002
        pass


class SpecComponent(Component):
    """Stand-in for a hub component when validating a hub config."""

    def execute(self, cmd: Command) -> int:
        spec = self.spec.command(cmd.command_id)
        if spec is not None and spec.ret_type != NONE:
            cmd.set_ret(0, spec.ret_type)
        return E.OK


# ====================================================================== engine
class Command:
    def __init__(self) -> None:
        self.component_type = ""                  # "" = any (componentPtr without type)
        self.component: Component | None = None   # static target
        self.component_ref: Variable | None = None
        self.command_id = 0
        self.in_args: list[Variable] = []
        self.out_args: list[Variable] = []
        self.ret_arg: Variable | None = None
        self.program: Program | None = None

    def _target(self) -> Component | None:
        if self.component is not None:
            return self.component
        ref = self.component_ref
        if ref is None:
            return None
        if ref.type == COMPONENT:
            c = ref.value
            if c is not None and self.component_type and c.type_name != self.component_type:
                return None
            return c
        if ref.type == CHAR and self.program is not None:
            return self.program.resolve(self.component_type, ref.value)
        return None

    def execute(self) -> int:
        target = self._target()
        if target is None:
            return E.RUNTIME
        if self.command_id == COMMAND_RESOLVE:
            if self.ret_arg is not None:
                self.ret_arg.set(COMPONENT, target)
            return E.OK
        return target.execute(self)

    # helpers for Component.execute
    def in_int(self, i: int) -> int:
        return self.in_args[i].get_int() if i < len(self.in_args) else 0

    def set_ret(self, value, vtype: int) -> None:
        if self.ret_arg is None:
            return
        if self.ret_arg.type == NONE:
            self.ret_arg.type = vtype
        if isinstance(value, Variable):
            self.ret_arg.assign_from(value)
        else:
            tmp = Variable(vtype)
            if isinstance(value, bool) or isinstance(value, int):
                tmp.set_int(int(value))
            else:
                tmp.value = value
            self.ret_arg.assign_from(tmp)


@dataclass
class VariableSource:
    variable: Variable | None = None
    command: Command | None = None
    ret: bool = True
    out_index: int = -1
    ret_type: int = NONE      # type of the value read (condVar type)

    def populate(self, dst: Variable) -> int:
        src = self.variable
        if self.command is not None:
            err = self.command.execute()
            if err != E.OK:
                return err
            src = self.command.ret_arg if self.ret else (
                self.command.out_args[self.out_index] if 0 <= self.out_index < len(self.command.out_args) else None)
        if src is None:
            return E.RUNTIME
        dst.assign_from(src)
        return E.OK


STATEMENT_OPS = {"equals": 1, "plusequals": 2, "minusequals": 3, "multiplyequals": 4, "divideequals": 5}


@dataclass
class Statement:
    left: Variable
    op: int
    right: Variable | None = None
    right_source: VariableSource | None = None
    right_scratch: Variable = field(default_factory=Variable)

    def execute(self) -> int:
        r = self.right
        if self.right_source is not None:
            err = self.right_source.populate(self.right_scratch)
            if err != E.OK:
                return err
            r = self.right_scratch
        if r is None:
            return E.RUNTIME
        if self.op == 1:
            self.left.assign_from(r)
            return E.OK
        return E.OK if statement_op(self.left, self.op, r) else E.RUNTIME


@dataclass
class Message:
    socket: SocketDef
    schema_index: int
    args: list[Variable]
    program: Program | None = None

    def execute(self) -> int:
        if self.program is None:
            return E.OK
        return self.program.send_message(self.socket, self.schema_index, self.args)


CONDITION_OPS = {"equal": 1, "notequal": 2, "greaterthan": 3, "greaterthanorequal": 4,
                 "lessthan": 5, "lessthanorequal": 6}


@dataclass
class Condition:
    left: Variable
    op: int
    right: Variable

    def evaluate(self) -> bool:
        c = self.left.compare(self.right)
        if c is None:
            return False
        return {1: c == 0, 2: c != 0, 3: c > 0, 4: c >= 0, 5: c < 0, 6: c <= 0}.get(self.op, False)


IF, ELIF, ELSE = 1, 2, 3
AND, OR = 1, 2


@dataclass
class ConditionGroup:
    group_type: int
    conditions: list[Condition]
    connectors: list[int]
    on_true: list  # operations

    def evaluate(self) -> bool:
        if self.group_type == ELSE:
            return True
        if not self.conditions:
            return False
        acc = self.conditions[0].evaluate()
        for i, cond in enumerate(self.conditions[1:]):
            cur = cond.evaluate()
            acc = (acc and cur) if self.connectors[i] == AND else (acc or cur)
        return acc


def run_operations(ops: list) -> int:
    first = E.OK
    for op in ops:
        err = op.execute()
        if err != E.OK and first == E.OK:
            first = err
    return first


@dataclass
class Function:
    id: int
    name: str
    conditional: bool = False
    args: list[Variable] = field(default_factory=list)
    temp_vars: list[Variable] = field(default_factory=list)
    cond_sources: list[VariableSource] = field(default_factory=list)
    cond_vars: list[Variable] = field(default_factory=list)
    operations: list = field(default_factory=list)
    groups: list[ConditionGroup] = field(default_factory=list)

    def execute(self) -> int:
        if not self.conditional:
            return run_operations(self.operations)
        for src, var in zip(self.cond_sources, self.cond_vars):
            src.populate(var)
        for g in self.groups:
            if g.evaluate():
                return run_operations(g.on_true)
        return E.OK


@dataclass
class ArgBinding:
    source_index: int = -1
    fixed: Variable | None = None


@dataclass
class FunctionBinding:
    function: Function
    args: list[ArgBinding] | None = None

    def invoke(self, sources: list[Variable]) -> int:
        if self.args is not None:
            for i, a in enumerate(self.args[:len(self.function.args)]):
                if a.source_index >= 0:
                    if a.source_index < len(sources):
                        self.function.args[i].assign_from(sources[a.source_index])
                elif a.fixed is not None:
                    self.function.args[i].assign_from(a.fixed)
        return self.function.execute()


# ====================================================================== program
Sender = Callable[[str, str, bool], None]       # (protocol, payload, ack)


class Program:
    """Everything one side (hub or app) built from a config."""

    def __init__(self, side: str):
        self.side = side
        self.globals: list[Variable] = []
        self.components: list[Component] = []
        self.functions: dict[int, Function] = {}
        self.sockets: dict[str, SocketDef] = {}
        self.handlers: dict[tuple[int, int], list[FunctionBinding]] = {}   # (component index, event id)
        self.sender: Sender | None = None
        self.on_error: Callable[[str], None] | None = None
        self.running = False

    def resolve(self, type_name: str, cid: str) -> Component | None:
        for c in self.components:
            if c.type_name == type_name and c.id == cid:
                return c
        return None

    # ---------------------------------------------------------------- run
    def start(self) -> None:
        self.running = True
        for c in self.components:
            c.event_sink = self.handle_event
            c.start()

    def stop(self) -> None:
        self.running = False
        for c in self.components:
            c.stop()

    def tick(self, now_ms: int) -> None:
        if self.running:
            for c in self.components:
                c.tick(now_ms)

    def handle_event(self, component: Component, event_id: int, outs: list[Variable]) -> None:
        if not self.running:
            return
        idx = self.components.index(component)
        for b in self.handlers.get((idx, event_id), ()):
            self._report(b.invoke(outs), f"{component.name} event {event_id} -> {b.function.name}")

    def handle_frame(self, protocol: str, data: str) -> bool:
        """A received payload for a socket. True if a schema matched."""
        if not self.running:
            return False
        sock = self.sockets.get(protocol)
        if sock is None:
            return False
        m = sock.match(data)
        if m is None:
            self._report(E.RUNTIME, f"{protocol}: no schema matches {data!r}")
            return False
        si, fields = m
        for b in sock.schemas[si].bindings:
            self._report(b.invoke(fields), f"{protocol} schema {si} -> {b.function.name}")
        return True

    def send_message(self, sock: SocketDef, si: int, args: list[Variable]) -> int:
        try:
            payload = sock.encode(si, args)
        except ValueError as exc:
            self._report(E.RUNTIME, str(exc))
            return E.RUNTIME
        if self.sender is not None:
            self.sender(sock.protocol, payload, sock.schemas[si].ack)
        return E.OK

    def _report(self, err: int, what: str) -> None:
        if err != E.OK and self.on_error is not None:
            self.on_error(f"{what}: {E.ERROR_NAMES.get(err, err)}")
