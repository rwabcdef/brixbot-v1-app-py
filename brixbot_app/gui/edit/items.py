"""One editor per project item kind, in the hub's config format
(../hub/brixbot_hub/Brix_ConfigBuilder.hpp):

    ComponentEdit   type, id, hub pin slot, init args, controls (socket ->
                    command, a view that acts through EditCtx.on_control),
                    event -> function bindings
    FunctionEdit    args, temp vars, cond vars, operations or if / elif / else
    SocketEdit      protocol, common args, schemas (+ hub / app bindings)
    GlobalsEdit     a side's global variables

and the parts they are made of (operations, conditions, bindings, fields).
Each is built from an item's JSON and returns the edited JSON from value(),
keeping keys it does not manage.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ...editor import controls, refs, templates
from ...engine.hardware import SLOT_KEY, assign_slots, free_slots, load_hardware
from ...engine.project import DEFAULT_HARDWARE
from ...engine.runtime import CONDITION_OPS, STATEMENT_OPS
from ...engine.specs import COMMAND_RESOLVE, NONE, SIDE_APP, SIDE_HUB, VARIADIC
from ...engine.variable import TYPE_NAMES
from .fields import (
    VALUE_TYPES, VAR_KINDS, VAR_TYPES, Combo, EditCtx, FixedEdit, IntEdit, ListEditor, LiveCombo, NumberEdit,
    RefEdit, SizedList, TextEdit, TypeCombo, ValueEdit, _dicts, form_widget, hbox, hint, merge, section_label,
    var_label,
)


class Slot(QWidget):
    """A place in a form whose widget can be swapped."""
    changed = Signal()

    def __init__(self, w: QWidget | None = None):
        super().__init__()
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self.w: QWidget | None = None
        if w is not None:
            self.set(w)

    def set(self, w: QWidget) -> None:
        if self.w is not None:
            self._lay.removeWidget(self.w)
            self.w.hide()
            self.w.deleteLater()
        self.w = w
        self._lay.addWidget(w)
        if hasattr(w, "changed"):
            w.changed.connect(self.changed)

    def value(self):
        return self.w.value() if self.w is not None else None


class _Form(QWidget):
    """Base: a QFormLayout and a changed signal."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

    def row(self, label: str, w: QWidget) -> QWidget:
        self.form.addRow(label, w)
        if hasattr(w, "changed"):
            w.changed.connect(self.changed)
        return w

    def full(self, w: QWidget) -> QWidget:
        self.form.addRow(w)
        if hasattr(w, "changed"):
            w.changed.connect(self.changed)
        return w


def _type_name(t: int) -> str:
    return TYPE_NAMES[t] if 0 <= t < len(TYPE_NAMES) else "?"


# ================================================================== variables / fields
class VarRow(_Form):
    """{ "#name", "type", ["value"] } - a function arg, temp var or global."""

    def __init__(self, v: dict | None, types: list[str], with_value: bool = False):
        super().__init__()
        self.orig = dict(v or {})
        self.name = self.row("Name", TextEdit(self.orig.get("#name"), "optional label"))
        self.type = self.row("Type", TypeCombo(self.orig.get("type") or types[0], types))
        self.val = None
        if with_value:
            self.val = self.row("Initial value", ValueEdit(self.orig.get("value"), self.type.value(), optional=True))
            self.type.changed.connect(lambda: self.val.set_type(self.type.value()))

    def value(self) -> dict:
        m = {"#name": self.name.value(), "type": self.type.value()}
        if self.val is not None:
            m["value"] = self.val.value()
        return merge(self.orig, m)


class FieldRow(_Form):
    """A socket payload field: commonArgs / args, or constantArgs (with a value)."""

    def __init__(self, f: dict | None, constant: bool = False):
        super().__init__()
        self.orig = dict(f or {})
        self.name = self.row("Name", TextEdit(self.orig.get("#name"), "optional label"))
        self.type = self.row("Type", TypeCombo(self.orig.get("type") or "uint8", VALUE_TYPES))
        self.val = None
        if constant:
            self.val = self.row("Value", ValueEdit(self.orig.get("value"), self.type.value()))
            self.type.changed.connect(lambda: self.val.set_type(self.type.value()))
        self.min_len = IntEdit(self.orig.get("minLen"), "default")
        self.max_len = IntEdit(self.orig.get("maxLen"), "default")
        self.min_val = NumberEdit(self.orig.get("minValue"), "-")
        self.max_val = NumberEdit(self.orig.get("maxValue"), "-")
        self.row("Length min / max", hbox(self.min_len, QLabel("/"), self.max_len))
        for w in (self.min_len, self.max_len):
            w.changed.connect(self.changed)
        if not constant:
            self.row("Value min / max", hbox(self.min_val, QLabel("/"), self.max_val))
            for w in (self.min_val, self.max_val):
                w.changed.connect(self.changed)

    def label(self) -> str:
        n = self.name.value()
        return f"{n}:{self.type.value()}" if n else self.type.value()

    def value(self) -> dict:
        m = {"#name": self.name.value(), "type": self.type.value(),
             "minLen": self.min_len.value(), "maxLen": self.max_len.value()}
        if self.val is not None:
            m["value"] = self.val.value()
        else:
            m["minValue"] = self.min_val.value()
            m["maxValue"] = self.max_val.value()
        return merge(self.orig, m)


def field_label(f: dict) -> str:
    n = f.get("#name") if isinstance(f, dict) else None
    t = f.get("type", "?") if isinstance(f, dict) else "?"
    return f"{n}:{t}" if n else t


# ================================================================== commands
TARGET_FIXED, TARGET_ID, TARGET_PTR = "fixed", "idArg", "ptr"
TARGET_KEYS = {TARGET_FIXED: "componentId", TARGET_ID: "componentIdArg", TARGET_PTR: "componentPtr"}


class CommandEdit(_Form):
    """A component command. value_only: used as a value source (condVar /
    statement right side), so only commands returning something are offered
    and there is no retArg."""

    def __init__(self, ctx: EditCtx, op: dict | None, value_only: bool = False):
        super().__init__()
        self.ctx = ctx
        self.orig = dict(op or {})
        self.value_only = value_only
        types = list(ctx.specs)
        ctype = self.orig.get("componentType")
        if ctype not in types and types:
            ctype = types[0]
        self.ctype = self.row("Component type", Combo([(t, t) for t in types], ctype))
        if self.orig.get("componentIdArg") is not None:
            mode = TARGET_ID
        elif self.orig.get("componentPtr") is not None:
            mode = TARGET_PTR
        else:
            mode = TARGET_FIXED
        self.mode = self.row("Target", Combo([("a fixed component", TARGET_FIXED),
                                              ("component id from a variable", TARGET_ID),
                                              ("component pointer variable", TARGET_PTR)], mode))
        self.target = self.row("Component", Slot())
        self.command = self.row("Command", Slot())
        self.args = self.row("Command args", Slot())
        self.ret = None
        if not value_only:
            self.ret = self.row("Store result in", RefEdit(ctx, self.orig.get("retArg"),
                                                           kinds=("none", "temp", "arg", "cond", "global")))
        self._build_target(self.orig)
        self._build_command(self.orig.get("commandId"))
        self._build_args(self.orig.get("inArgs"))
        self.ctype.changed.connect(self._type_changed)
        self.mode.changed.connect(lambda: self._build_target({}))
        self.command.changed.connect(lambda: self._build_args(self.args.value()))

    # ---------------------------------------------------------------- parts
    def spec(self):
        return self.ctx.specs.get(self.ctype.value())

    def _component_choices(self):
        t = self.ctype.value()
        return [(f"{c.get('id')}  {c.get('#name') or ''}".rstrip(), c.get("id"))
                for c in self.ctx.components() if c.get("type") == t]

    def _build_target(self, src: dict) -> None:
        mode = self.mode.value()
        if mode == TARGET_FIXED:
            self.target.set(LiveCombo(self._component_choices, src.get("componentId")))
        elif mode == TARGET_ID:
            a = src.get("componentIdArg")
            ref = {"argIndex": a} if isinstance(a, int) and not isinstance(a, bool) else a
            self.target.set(RefEdit(self.ctx, ref, kinds=("arg", "temp", "cond", "global", "fixed")))
        else:
            self.target.set(RefEdit(self.ctx, src.get("componentPtr"), kinds=("temp", "arg", "global")))

    def _command_items(self):
        spec = self.spec()
        items = []
        for c in spec.commands if spec else ():
            if self.value_only and c.ret_type == NONE:
                continue
            ret = f" -> {_type_name(c.ret_type)}" if c.ret_type != NONE else ""
            args = ", ".join(c.in_names) if c.num_in != VARIADIC else "values..."
            items.append((f"{c.name}({args}){ret}", c.id))
        items.append(("resolve id -> component pointer", COMMAND_RESOLVE))
        return items

    def _build_command(self, cid) -> None:
        items = self._command_items()
        if cid is None and items:
            cid = items[0][1]
        self.command.set(Combo(items, cid))

    def _build_args(self, values) -> None:
        spec = self.spec()
        cid = self.command.value()
        cs = spec.command(cid) if spec and isinstance(cid, int) else None
        make = lambda r: RefEdit(self.ctx, r)  # noqa: E731
        if cs is not None and cs.num_in == VARIADIC:
            self.args.set(ListEditor(values or [], make, lambda: {"fixedVal": 0}, "arg"))
        else:
            names = list(cs.in_names) if cs else []
            if cs:
                names += [f"arg{i}" for i in range(len(names), cs.num_in)]
            self.args.set(SizedList(values or [], make, names))

    def _type_changed(self) -> None:
        self._build_target({})
        self._build_command(None)
        self._build_args([])
        self.changed.emit()

    # ---------------------------------------------------------------- value
    def value(self) -> dict:
        mode = self.mode.value()
        out = {k: v for k, v in self.orig.items() if k not in TARGET_KEYS.values()}
        tv = self.target.value()
        if mode == TARGET_ID and isinstance(tv, dict) and set(tv) == {"argIndex"}:
            tv = tv["argIndex"]
        m = {"componentType": self.ctype.value(), TARGET_KEYS[mode]: tv, "commandId": self.command.value(),
             "inArgs": self.args.value()}
        if self.ret is not None:
            m["retArg"] = self.ret.value()
        return merge(out, m)


# ================================================================== operations
def _statement_ops():
    return [(f"{refs.STATEMENT_SYMBOLS.get(k, k)}   ({k})", k) for k in STATEMENT_OPS]


def _condition_ops():
    return [(f"{refs.CONDITION_SYMBOLS.get(k, k)}   ({k})", k) for k in CONDITION_OPS]


class StatementEdit(_Form):
    def __init__(self, ctx: EditCtx, op: dict):
        super().__init__()
        self.ctx = ctx
        self.orig = dict(op)
        self.left = self.row("Variable", RefEdit(ctx, op.get("leftVar"), kinds=VAR_KINDS))
        self.op = self.row("Operator", Combo(_statement_ops(), op.get("operator") or "equals"))
        right = op.get("rightVar")
        if right is None and op.get("rightFixedVal") is not None:
            right = {"fixedVal": op["rightFixedVal"]}
        self.right = self.row("Value", RefEdit(ctx, right, kinds=(*VAR_KINDS, "fixed", "command"),
                                               command_factory=lambda o: CommandEdit(ctx, o, value_only=True)))

    def value(self) -> dict:
        m = {"leftVar": self.left.value(), "operator": self.op.value()}
        if self.right.kind_value() == "fixed":
            m["rightFixedVal"], m["rightVar"] = self.right.fixed_value(), None
        else:
            m["rightVar"], m["rightFixedVal"] = self.right.value(), None
        return merge(self.orig, m)


class MessageEdit(_Form):
    def __init__(self, ctx: EditCtx, op: dict):
        super().__init__()
        self.ctx = ctx
        self.orig = dict(op)
        self.proto = self.row("Socket", LiveCombo(
            lambda: [(s.get("protocol"), s.get("protocol")) for s in ctx.sockets()], op.get("protocol")))
        self.schema = self.row("Schema", LiveCombo(self._schema_choices, op.get("schema", 0)))
        self.args = self.row("Values", SizedList(op.get("args") or [], lambda r: RefEdit(ctx, r), self._labels()))
        self.proto.changed.connect(self._socket_changed)
        self.schema.changed.connect(lambda: self.args.set_labels(self._labels()))

    def _socket(self) -> dict:
        return next((s for s in self.ctx.sockets() if s.get("protocol") == self.proto.value()), {})

    def _schema_choices(self):
        out = []
        for i, sc in enumerate(_dicts(self._socket().get("schemas"))):
            consts = "".join(str(c.get("value")) for c in _dicts(sc.get("constantArgs")))
            label = sc.get("#comment") or (f"'{consts}'" if consts else "")
            out.append((f"{i}: {label}".rstrip(": "), i))
        return out

    def _labels(self) -> list[str]:
        s = self._socket()
        schemas = _dicts(s.get("schemas"))
        si = self.schema.value() if hasattr(self, "schema") else self.orig.get("schema", 0)
        sc = schemas[si] if isinstance(si, int) and 0 <= si < len(schemas) else {}
        return ([f"common: {field_label(f)}" for f in _dicts(s.get("commonArgs"))]
                + [f"arg: {field_label(f)}" for f in _dicts(sc.get("args"))])

    def _socket_changed(self) -> None:
        self.schema.set_value(0)
        self.args.set_labels(self._labels())

    def value(self) -> dict:
        return merge(self.orig, {"protocol": self.proto.value(), "schema": self.schema.value(),
                                 "args": self.args.value()})


class OperationEdit(QWidget):
    """Wraps the editor for one operation (its kind is fixed; delete and add to change it)."""
    changed = Signal()

    def __init__(self, ctx: EditCtx, op: dict):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        t = op.get("type")
        if t == "command":
            self.ed = CommandEdit(ctx, op)
        elif t == "statement":
            self.ed = StatementEdit(ctx, op)
        elif t == "message":
            self.ed = MessageEdit(ctx, op)
        else:
            self.ed = None
            self._raw = op
            lay.addWidget(hint(f"unknown operation type {t!r} - edit it in the JSON tab"))
        if self.ed is not None:
            lay.addWidget(self.ed)
            self.ed.changed.connect(self.changed)

    def value(self) -> dict:
        return self.ed.value() if self.ed is not None else self._raw

    def summary(self) -> str:
        v = self.value()
        return f"{v.get('type')}:  {refs.operation_text(v, self.ctx.side, self.ctx.fn(), self.ctx.globals())}"


def operations_editor(ctx: EditCtx, ops: list) -> ListEditor:
    def add(kind):
        return lambda: templates.new_operation(kind, ctx.side, ctx.components(), ctx.sockets())
    return ListEditor(ops, lambda o: OperationEdit(ctx, o),
                      [("Command (call a component)", add("command")),
                       ("Statement (set a variable)", add("statement")),
                       ("Message (send on a socket)", add("message"))],
                      "operation", row_title=lambda i, w: f"{i}  {w.summary()}")


# ================================================================== conditions
class ConditionEdit(_Form):
    """One condition; item = {"cond": {...}, "conn": "and" | "or"} (conn joins it to the previous one)."""

    def __init__(self, ctx: EditCtx, item: dict):
        super().__init__()
        self.ctx = ctx
        c = item.get("cond") or {}
        self.orig = dict(c)
        self.conn = self.row("Joined to previous by", Combo([("and", "and"), ("or", "or")], item.get("conn", "and")))

        def ref(var_key, fixed_key):
            if c.get(var_key) is not None:
                return c[var_key]
            if c.get(fixed_key) is not None:
                return {"fixedVal": c[fixed_key]}
            return None
        kinds = (*VAR_KINDS, "fixed")
        self.left = self.row("Left", RefEdit(ctx, ref("leftVar", "leftFixedVal"), kinds))
        self.op = self.row("Compare", Combo(_condition_ops(), c.get("operator") or "equal"))
        self.right = self.row("Right", RefEdit(ctx, ref("rightVar", "rightFixedVal"), kinds))

    def value(self) -> dict:
        m = {"operator": self.op.value()}
        for ed, var_key, fixed_key in ((self.left, "leftVar", "leftFixedVal"), (self.right, "rightVar", "rightFixedVal")):
            if ed.kind_value() == "fixed":
                m[fixed_key], m[var_key] = ed.fixed_value(), None
            else:
                m[var_key], m[fixed_key] = ed.value(), None
        return {"cond": merge(self.orig, m), "conn": self.conn.value()}

    def summary(self) -> str:
        return refs.condition_text(self.value()["cond"], self.ctx.fn(), self.ctx.globals())


class GroupEdit(_Form):
    """An if / elif / else group of a condition block."""

    def __init__(self, ctx: EditCtx, g: dict):
        super().__init__()
        self.ctx = ctx
        self.orig = dict(g)
        self.type = self.row("Branch", Combo([("if", "if"), ("elif", "elif"), ("else", "else")], g.get("type") or "if"))
        conds = _dicts(g.get("conditions"))
        cons = g.get("connectors") if isinstance(g.get("connectors"), list) else []
        items = [{"cond": c, "conn": cons[i - 1] if 0 < i <= len(cons) else "and"} for i, c in enumerate(conds)]
        self.conds_label = QLabel("When")
        self.conds = ListEditor(items, lambda it: ConditionEdit(ctx, it),
                                lambda: {"cond": templates.new_condition(), "conn": "and"}, "condition",
                                row_title=lambda i, w: f"{'' if i == 0 else w.conn.value() + ' '}({w.summary()})")
        self.form.addRow(self.conds_label, self.conds)
        self.conds.changed.connect(self.changed)
        self.ops = self.row("Do", operations_editor(ctx, _dicts(g.get("onTrue"))))
        self.type.changed.connect(self._type_changed)
        self._type_changed()

    def _type_changed(self) -> None:
        is_else = self.type.value() == "else"
        self.conds.setVisible(not is_else)
        self.conds_label.setVisible(not is_else)

    def value(self) -> dict:
        t = self.type.value()
        m = {"type": t, "onTrue": self.ops.value(), "conditions": None, "connectors": None}
        if t != "else":
            items = self.conds.value()
            m["conditions"] = [it["cond"] for it in items]
            if len(items) > 1:
                m["connectors"] = [it["conn"] for it in items[1:]]
        return merge(self.orig, m)

    def summary(self) -> str:
        v = self.value()
        if v["type"] == "else":
            return "else"
        return f"{v['type']} " + " ".join(
            (f"{w.value()['conn']} " if i else "") + f"({w.summary()})" for i, w in enumerate(self.conds.widgets()))


# ================================================================== cond vars
class CondVarEdit(_Form):
    """A conditional function's cond var: a command result or a global, read before the conditions."""

    def __init__(self, ctx: EditCtx, cv: dict | None):
        super().__init__()
        self.ctx = ctx
        self.orig = dict(cv or {})
        self.name = self.row("Name", TextEdit(self.orig.get("#name"), "optional label"))
        kind = "command" if self.orig.get("type") == "command" else "global"
        self.kind = self.row("Read from", Combo([("a global", "global"), ("a command result", "command")], kind))
        self.body = self.row("", Slot())
        self._build(self.orig)
        self.kind.changed.connect(lambda: self._build({}))

    def _build(self, src: dict) -> None:
        if self.kind.value() == "command":
            body = {k: v for k, v in src.items() if k != "#name"} if src.get("type") == "command" else {}
            self.body.set(CommandEdit(self.ctx, body, value_only=True))
        else:
            self.body.set(LiveCombo(lambda: [(var_label(g, i, "global"), i) for i, g in enumerate(self.ctx.globals())],
                                    src.get("globalVariableIndex", 0)))

    def value(self) -> dict:
        if self.kind.value() == "command":
            out = self.body.value()
            out["type"] = "command"
        else:
            t = self.orig.get("type") if self.orig.get("type") in ("global", "globalVariable") else "global"
            keep = {k: v for k, v in self.orig.items() if k.startswith("#")}
            out = {**keep, "type": t, "globalVariableIndex": self.body.value()}
        return merge(out, {"#name": self.name.value()})


# ================================================================== bindings
class ArgSrcEdit(QWidget):
    """Where one function arg comes from: a source value index or a fixed value."""
    changed = Signal()

    def __init__(self, a: dict | None, sources: Callable[[], list[tuple[str, tuple[str, int]]]]):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        a = a or {}
        cur = None
        fixed = a.get("fixedVal", a.get("fixedValue"))
        for k, v in a.items():
            if k.endswith("Index") and isinstance(v, int):
                cur = f"{k}:{v}"

        def choices():
            return [(label, f"{k}:{i}") for label, (k, i) in sources()] + [("fixed value", "fixed")]
        if cur is None and fixed is None:
            cur = choices()[0][1]
        elif cur is None:
            cur = "fixed"
        self.src = LiveCombo(choices, cur)
        self.fixed = FixedEdit(fixed if fixed is not None else 0)
        lay.addWidget(self.src, 1)
        lay.addWidget(self.fixed, 1)
        self.src.changed.connect(self._changed)
        self.fixed.changed.connect(self.changed)
        self._changed(emit=False)

    def _changed(self, emit: bool = True) -> None:
        self.fixed.setVisible(self.src.value() == "fixed")
        if emit:
            self.changed.emit()

    def value(self) -> dict:
        s = self.src.value()
        if not isinstance(s, str) or ":" not in s:
            return {"fixedVal": self.fixed.value()}
        k, i = s.split(":", 1)
        return {k: int(i)}


class BindingEdit(_Form):
    """{ "functionId", "args": null | [ArgSrc...] } - run a function, choosing where its args come from."""

    def __init__(self, ctx: EditCtx, b: dict, sources: Callable[[], list]):
        super().__init__()
        self.ctx = ctx
        self.orig = dict(b)
        self.sources = sources
        fid = b.get("functionId")
        # the function's own id value (so "3" and 3 match)
        fid = next((f.get("id") for f in ctx.functions() if refs.same_id(f.get("id"), fid)), fid)
        self.fn = self.row("Run function", LiveCombo(
            lambda: [(f"{f.get('id')}: {f.get('#name') or ''}".rstrip(": "), f.get("id")) for f in ctx.functions()],
            fid))
        explicit = isinstance(b.get("args"), list)
        self.mode = self.row("Its args", Combo([("set each arg", "explicit"),
                                                ("no args transferred", "none")],
                                               "explicit" if explicit else "none"))
        self.mode.setToolTip("no args transferred: the function runs with the arg values it already has")
        self.args = self.row("", SizedList(b.get("args") if explicit else [],
                                           lambda a: ArgSrcEdit(a, sources), self._labels()))
        self.fn.changed.connect(lambda: self.args.set_labels(self._labels()))
        self.mode.changed.connect(self._mode_changed)
        self._mode_changed(emit=False)

    def _function(self) -> dict:
        fid = self.fn.value()
        return next((f for f in self.ctx.functions() if refs.same_id(f.get("id"), fid)), {})

    def _labels(self) -> list[str]:
        return [var_label(a, i, "arg") for i, a in enumerate(_dicts(self._function().get("args")))]

    def _mode_changed(self, emit: bool = True) -> None:
        self.args.setVisible(self.mode.value() == "explicit")
        if emit:
            self.changed.emit()

    def value(self) -> dict:
        out = dict(self.orig)
        out["functionId"] = self.fn.value()
        if self.mode.value() == "explicit":
            out["args"] = self.args.value()
        elif "args" in self.orig:
            out["args"] = None
        return out

    def summary(self) -> str:
        f = self._function()
        return f"-> {f.get('#name') or 'function'} #{self.fn.value()}"


def bindings_editor(ctx: EditCtx, bindings: list, sources: Callable[[], list]) -> ListEditor:
    ed = ListEditor(bindings, lambda b: BindingEdit(ctx, b, sources),
                    lambda: templates.new_binding(ctx.functions()), "function",
                    row_title=lambda i, w: f"{i}  {w.summary()}")
    if not ctx.functions():
        where = "hub" if ctx.side == SIDE_HUB else "app"
        ed.add_note(hint(f"There are no functions on the {where} yet. Add one with "
                         f"+ Add > Function in the {where.capitalize()} column, then come back here."))
        ed.add_btn.setEnabled(False)
        ed.add_btn.setToolTip(f"there are no {where} functions to run")
    return ed


# ================================================================== component
class ComponentEdit(_Form):
    def __init__(self, ctx: EditCtx, comp: dict):
        super().__init__()
        self.ctx = ctx
        self.orig = dict(comp)
        types = list(ctx.specs)
        cur = comp.get("type") if comp.get("type") in types else (types[0] if types else None)
        self.type = self.row("Type", Combo([(t, t) for t in types], cur))
        self.id = self.row("Id (1 char)", TextEdit(comp.get("id"), "A", max_len=1))
        self.name = self.row("Name", TextEdit(comp.get("#name"), "label shown on the card"))
        self.comment = self.row("Comment", TextEdit(comp.get("#comment")))
        self.hw = load_hardware(ctx.data.get("hardware") or DEFAULT_HARDWARE) if ctx.side == SIDE_HUB else None
        self.pin = self.row("Pin / slot", LiveCombo(self._slot_choices, comp.get(SLOT_KEY)))
        self.full(section_label("Settings"))
        self.settings = self.full(Slot())
        self.full(section_label("Controls"))
        self.full(hint("a socket message that runs one of this component's commands"))
        self.controls = self.full(Slot())
        self.full(section_label("Events"))
        self.events = self.full(Slot())
        self._build(comp)
        self.type.changed.connect(lambda: self._build({}))
        self.type.changed.connect(self._pin_visible)
        self._pin_visible()

    # ---- hardware slot (hub)
    def _slot_choices(self) -> list:
        t = self.type.value()
        comps = self.ctx.components()
        first = "first free slot"
        if self.hw is not None and self.ctx.index is not None and SLOT_KEY not in self.orig                 and t == self.orig.get("type"):
            now = assign_slots(self.hw, comps).get(self.ctx.index)
            if now is not None:
                first += f" (now {now.label})"
        out = [] if SLOT_KEY in self.orig and t == self.orig.get("type") else [(first, None)]
        if self.hw is not None:
            for s in free_slots(self.hw, comps, t, keep=self.ctx.index):
                out.append((s.label, s.index))
        cur = self.orig.get(SLOT_KEY)
        if t == self.orig.get("type") and cur is not None and all(v != cur for _, v in out):
            out.append((f"slot {cur} (in use or unknown)", cur))
        return out

    def _pin_visible(self) -> None:
        on = self.hw is not None and self.type.value() in self.hw.slots
        self.pin.setVisible(on)
        lbl = self.form.labelForField(self.pin)
        if lbl is not None:
            lbl.setVisible(on)
        if self.type.value() != self.orig.get("type"):
            self.pin.set_value(None)        # first free slot of the new type

    # ---- controls (socket -> command)
    def _build_controls(self, spec) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        cmds = controls.control_commands(spec)
        same = self.type.value() == self.orig.get("type") and self.ctx.index is not None
        for cmd in cmds:
            box = QGroupBox(f"{cmd.name}({', '.join(cmd.in_names)})")
            bl = QVBoxLayout(box)
            conns = controls.connections(self.ctx.data, self.ctx.side, self.orig, cmd.id) if same else []
            for c in conns:
                fn = next((f for f in self.ctx.functions() if refs.same_id(f.get("id"), c.function_id)), {})
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.addWidget(QLabel(f"← {c.label}  via function {c.function_id}"
                                    + (f" ({fn.get('#name')})" if fn.get("#name") else "")), 1)
                if self.ctx.on_control is not None:
                    b = QPushButton("Disconnect")
                    b.clicked.connect(lambda _=False, cmd=cmd, c=c: self.ctx.on_control("disconnect", cmd.id, c))
                    rl.addWidget(b)
                bl.addWidget(row)
            if not conns:
                bl.addWidget(hint("not connected"))
            if self.ctx.on_control is not None:
                b = QPushButton("Connect socket...")
                b.setEnabled(same)
                if not same:
                    b.setToolTip("apply the component first")
                b.clicked.connect(lambda _=False, cmd=cmd: self.ctx.on_control("connect", cmd.id, None))
                bl.addWidget(b)
            lay.addWidget(box)
        if not cmds:
            lay.addWidget(hint("this component has no controls"))
        w.value = lambda: None
        self.controls.set(w)

    def _build(self, src: dict) -> None:
        spec = self.ctx.specs.get(self.type.value())
        self._build_controls(spec)
        # settings (init args)
        w, f = form_widget()
        self._arg_edits: dict[str, ValueEdit] = {}
        given = {a.get("key"): a.get("value") for a in _dicts(src.get("args"))}
        names = list(spec.init_args) if spec else []
        for k in given:
            if k not in names:
                names.append(k)
        for k in names:
            ed = ValueEdit(given.get(k), None, optional=True)
            ed.setPlaceholderText("default")
            ed.changed.connect(self.changed)
            self._arg_edits[k] = ed
            f.addRow(k, ed)
        if not names:
            f.addRow(hint("this component has no settings"))
        w.value = lambda: None
        self.settings.set(w)
        # events
        ew = QWidget()
        el = QVBoxLayout(ew)
        el.setContentsMargins(0, 0, 0, 0)
        self._event_edits: list[tuple[dict, int, ListEditor]] = []
        evs = {e.get("eventId"): e for e in _dicts(src.get("events"))}
        order = [e.get("eventId") for e in _dicts(src.get("events"))]
        order += [e.id for e in (spec.events if spec else ()) if e.id not in order]
        for eid in order:
            es = spec.event(eid) if spec and isinstance(eid, int) else None
            outs = ", ".join(f"{n}:{_type_name(t)}" for n, t in zip(es.out_names, es.out_types)) if es else ""
            box = QGroupBox(f"on {es.name if es else f'event {eid}'}({outs})")
            bl = QVBoxLayout(box)
            ev = evs.get(eid, {})

            def sources(es=es):
                if es is None:
                    return []
                return [(f"event value {i}: {n} ({_type_name(t)})", ("outArgIndex", i))
                        for i, (n, t) in enumerate(zip(es.out_names, es.out_types))]
            ed = bindings_editor(self.ctx, _dicts(ev.get("functions")), sources)
            ed.changed.connect(self.changed)
            if es is not None and self.ctx.on_control is not None:
                nb = QPushButton("+ new handler function")
                nb.setToolTip(f"make a function taking ({outs}) and run it on {es.name}"
                              if outs else f"make a function and run it on {es.name}")
                same = self.type.value() == self.orig.get("type") and self.ctx.index is not None
                nb.setEnabled(same)
                nb.clicked.connect(lambda _=False, eid=eid: self.ctx.on_control("new_handler", eid, None))
                ed.add_note(nb)
            bl.addWidget(ed)
            el.addWidget(box)
            self._event_edits.append((ev, eid, ed))
        if not order:
            el.addWidget(hint("this component has no events"))
        ew.value = lambda: None
        self.events.set(ew)
        self._args_orig = _dicts(src.get("args"))

    def value(self) -> dict:
        args = []
        orig_keys = [a.get("key") for a in self._args_orig]
        for k in orig_keys + [k for k in self._arg_edits if k not in orig_keys]:
            ed = self._arg_edits.get(k)
            if ed is None:
                continue
            v = ed.value()
            if v is not None:
                prev = next((a for a in self._args_orig if a.get("key") == k), {})
                args.append({**prev, "key": k, "value": v})
        events = []
        for ev, eid, ed in self._event_edits:
            fns = ed.value()
            if fns or ev:
                events.append(merge({**ev, "eventId": eid}, {"functions": fns}))
        same_type = self.type.value() == self.orig.get("type")
        m = {"type": self.type.value(), "id": self.id.text(), "#name": self.name.value(),
             "#comment": self.comment.value(), "args": args, "events": events}
        m[SLOT_KEY] = self.pin.value() if (self.hw and self.type.value() in self.hw.slots) else None
        if not same_type:
            m["args"] = args or None
            m["events"] = events or None
        return merge(self.orig, m)


# ================================================================== function
class FunctionEdit(_Form):
    def __init__(self, ctx: EditCtx, fn: dict):
        super().__init__()
        self.orig = dict(fn)
        self.ctx = ctx.with_fn(self._live)
        self.id = self.row("Id", TextEdit(fn.get("id"), "0"))
        self.name = self.row("Name", TextEdit(fn.get("#name"), "label"))
        self.comment = self.row("Comment", TextEdit(fn.get("#comment")))
        conditional = fn.get("type") in ("conditional", "logic")
        self.type = self.row("Kind", Combo([("basic - a list of operations", "basic"),
                                            ("conditional - if / elif / else", "conditional")],
                                           "conditional" if conditional else "basic"))
        self.full(section_label("Args (set by the event / message that runs it)"))
        self._args = self.full(ListEditor(_dicts(fn.get("args")), lambda v: VarRow(v, VALUE_TYPES),
                                          templates.new_var, "arg",
                                          row_title=lambda i, w: f"arg {i}  {w.name.value() or ''}"))
        self.full(section_label("Temp vars"))
        self._temps = self.full(ListEditor(_dicts(fn.get("tempVars")), lambda v: VarRow(v, VAR_TYPES),
                                           templates.new_var, "temp var",
                                           row_title=lambda i, w: f"temp {i}  {w.name.value() or ''}"))
        self.cv_label = section_label("Cond vars (read before the conditions)")
        self.full(self.cv_label)
        self._cvs = self.full(ListEditor(_dicts(fn.get("condVars")), lambda v: CondVarEdit(self.ctx, v),
                                         lambda: templates.new_cond_var(self.ctx.globals()), "cond var",
                                         row_title=lambda i, w: f"cond {i}  {w.name.value() or ''}"))
        self.body_label = section_label("")
        self.full(self.body_label)
        self.body = self.full(Slot())
        self._build_body(fn)
        self.type.changed.connect(self._kind_changed)

    def _live(self) -> dict:
        out = {}
        for key, attr in (("args", "_args"), ("tempVars", "_temps"), ("condVars", "_cvs")):
            w = getattr(self, attr, None)
            out[key] = w.value() if w is not None else _dicts(self.orig.get(key))
        return out

    def _build_body(self, src: dict) -> None:
        cond = self.type.value() == "conditional"
        self.cv_label.setVisible(cond)
        self._cvs.setVisible(cond)
        if cond:
            self.body_label.setText("Branches")
            cb = src.get("conditionBlock") if isinstance(src.get("conditionBlock"), dict) else {}
            self.body.set(ListEditor(_dicts(cb.get("conditionGroups")), lambda g: GroupEdit(self.ctx, g),
                                     [("elif branch", lambda: templates.new_group("elif")),
                                      ("else branch", lambda: templates.new_group("else"))],
                                     "branch", row_title=lambda i, w: w.summary()))
        else:
            self.body_label.setText("Operations")
            self.body.set(operations_editor(self.ctx, _dicts(src.get("operationBlock"))))

    def _kind_changed(self) -> None:
        cur = self.body.value() or []
        if self.type.value() == "conditional":
            g = templates.new_group("if")
            g["onTrue"] = cur
            self._build_body({"conditionBlock": {"conditionGroups": [g]}})
        else:
            first = cur[0].get("onTrue", []) if cur else []
            self._build_body({"operationBlock": first})
        self.changed.emit()

    def fid(self):
        t = self.id.text().strip()
        o = self.orig.get("id")
        if isinstance(o, int) and not isinstance(o, bool) and t.isdigit():
            return int(t)
        return t

    def value(self) -> dict:
        cond = self.type.value() == "conditional"
        t = self.orig.get("type") if cond and self.orig.get("type") in ("conditional", "logic") else self.type.value()
        m = {"id": self.fid(), "#name": self.name.value(), "#comment": self.comment.value(), "type": t,
             "args": self._args.value(), "tempVars": self._temps.value(),
             "condVars": self._cvs.value() if cond else None}
        if cond:
            cb = self.orig.get("conditionBlock") if isinstance(self.orig.get("conditionBlock"), dict) else {}
            m["conditionBlock"] = merge(cb, {"conditionGroups": self.body.value()})
            m["operationBlock"] = None
        else:
            m["operationBlock"] = self.body.value()
            m["conditionBlock"] = None
        return merge(self.orig, m)


# ================================================================== socket
class SchemaEdit(_Form):
    def __init__(self, ctx: EditCtx, sc: dict, common: Callable[[], list]):
        super().__init__()
        self.ctx = ctx
        self.orig = dict(sc)
        self.common = common
        self.comment = self.row("Comment", TextEdit(sc.get("#comment"), "what this message means"))
        self.ack = self.row("", QCheckBox("send with ack (T frame)"))
        self.ack.setChecked(bool(sc.get("ack")))
        self.ack.toggled.connect(lambda _: self.changed.emit())
        self.preview = self.row("Payload", QLabel())
        self.preview.setStyleSheet("font-family: Consolas, monospace;")
        self.consts = self.row("Constant fields", ListEditor(
            _dicts(sc.get("constantArgs")), lambda f: FieldRow(f, constant=True), templates.new_constant,
            "constant", row_title=lambda i, w: f"const {i}  {w.label()} = {w.val.value()!r}"))
        self.args = self.row("Value fields", ListEditor(
            _dicts(sc.get("args")), lambda f: FieldRow(f), templates.new_field, "field",
            row_title=lambda i, w: f"arg {i}  {w.label()}"))
        self.full(section_label("Hub runs, when the hub receives it"))
        self.hub_fns = self.full(bindings_editor(ctx.with_side(SIDE_HUB), _dicts(sc.get("functions")), self._sources))
        self.full(section_label("App runs, when the app receives it"))
        self.app_fns = self.full(bindings_editor(ctx.with_side(SIDE_APP), _dicts(sc.get("appFunctions")),
                                                 self._sources))
        self.changed.connect(self._update_preview)
        self._update_preview()

    def _sources(self):
        out = [(f"common {i}: {field_label(f)}", ("commonArgsIndex", i)) for i, f in enumerate(self.common())]
        out += [(f"const {i}: {field_label(f)}", ("constantArgsIndex", i)) for i, f in enumerate(self.consts.value())]
        out += [(f"arg {i}: {field_label(f)}", ("argsIndex", i)) for i, f in enumerate(self.args.value())]
        return out

    def _update_preview(self) -> None:
        common = "".join(f"<{field_label(f)}>" for f in self.common())
        consts = "".join(str(c.get("value")) for c in self.consts.value())
        args = "".join(f"<{field_label(f)}>" for f in self.args.value())
        hub = bool(self.hub_fns.value()) if hasattr(self, "hub_fns") else False
        app = bool(self.app_fns.value()) if hasattr(self, "app_fns") else False
        arrow = "app → hub" if hub and not app else ("hub → app" if app and not hub else ("↔" if hub else "(unused)"))
        self.preview.setText(f"{arrow}   {common}{consts}{args}")

    def summary(self) -> str:
        c = self.comment.value()
        consts = "".join(str(x.get("value")) for x in self.consts.value())
        return c or (f"'{consts}'" if consts else "")

    def value(self) -> dict:
        m = {"#comment": self.comment.value(), "constantArgs": self.consts.value(), "args": self.args.value(),
             "functions": self.hub_fns.value(), "appFunctions": self.app_fns.value()}
        if self.ack.isChecked():
            m["ack"] = True
        elif "ack" in self.orig:
            m["ack"] = False
        return merge(self.orig, m)


class SocketEdit(_Form):
    def __init__(self, ctx: EditCtx, sock: dict):
        super().__init__()
        self.ctx = ctx
        self.orig = dict(sock)
        self.proto = self.row("Protocol (5 chars)", TextEdit(sock.get("protocol"), "LED00", max_len=5))
        self.name = self.row("Name", TextEdit(sock.get("#name"), "optional label"))
        self.comment = self.row("Comment", TextEdit(sock.get("#comment")))
        self.full(hint("Payload = common fields + one schema's constant fields + its value fields. "
                       "Fields are fixed width except the last one."))
        self.common = self.row("Common fields", ListEditor(
            _dicts(sock.get("commonArgs")), lambda f: FieldRow(f), templates.new_field, "field",
            row_title=lambda i, w: f"common {i}  {w.label()}"))
        self.full(section_label("Schemas (message kinds)"))
        self.schemas = self.full(ListEditor(
            _dicts(sock.get("schemas")), lambda sc: SchemaEdit(ctx, sc, self.common.value), templates.new_schema,
            "schema", row_title=lambda i, w: f"schema {i}  {w.summary()}"))
        self.common.changed.connect(self._common_changed)

    def _common_changed(self) -> None:
        for w in self.schemas.widgets():
            w._update_preview()

    def value(self) -> dict:
        return merge(self.orig, {"protocol": self.proto.text(), "#name": self.name.value(),
                                 "#comment": self.comment.value(), "commonArgs": self.common.value(),
                                 "schemas": self.schemas.value()})


# ================================================================== globals
class GlobalsEdit(_Form):
    def __init__(self, ctx: EditCtx, globals_: list):
        super().__init__()
        self.ctx = ctx
        self.full(hint("Global variables keep their value between function runs. Functions refer to them "
                       "by position; moving or removing one updates the functions that use it."))
        self.list = self.full(ListEditor(_dicts(globals_), lambda v: VarRow(v, VALUE_TYPES, with_value=True),
                                         templates.new_global, "global",
                                         row_title=lambda i, w: f"global {i}  {w.name.value() or ''}"))

    def index_map(self) -> dict[int, int | None]:
        """old index -> new index (removed ones are absent)."""
        tags = self.list.tags()
        m: dict[int, int | None] = {}
        for new, old in enumerate(tags):
            if old is not None:
                m[old] = new
        return m

    def value(self) -> list:
        return self.list.value()


EDITORS = {"component": ComponentEdit, "function": FunctionEdit, "socket": SocketEdit, "globals": GlobalsEdit}


def make_editor(kind: str, ctx: EditCtx, value) -> QWidget:
    return EDITORS[kind](ctx, value)
