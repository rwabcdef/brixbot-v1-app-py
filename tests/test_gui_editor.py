"""The GUI editor, offscreen: form round trips, and editing a project in the window."""

import copy
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from brixbot_app.gui.edit.fields import EditCtx  # noqa: E402
from brixbot_app.gui.edit.items import make_editor  # noqa: E402
from brixbot_app.gui.main_window import EXAMPLES, MainWindow  # noqa: E402

YES = QtWidgets.QMessageBox.StandardButton.Yes


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(qapp):
    w = MainWindow(str(EXAMPLES / "demo_project.json"))
    w.ask = lambda *a: YES
    yield w
    w.doc.mark_saved()
    w.close()


def demo() -> dict:
    return json.loads((EXAMPLES / "demo_project.json").read_text(encoding="utf-8"))


def test_every_demo_item_round_trips_through_its_form(qapp):
    d = demo()
    for side in ("hub", "app"):
        ctx = EditCtx(d, side)
        for c in d[side]["components"]:
            assert make_editor("component", ctx, c).value() == c
        for f in d[side]["functions"]:
            assert make_editor("function", ctx, f).value() == f
        assert make_editor("globals", ctx, d[side]["globals"]).value() == d[side]["globals"]
    for s in d["sockets"]:
        assert make_editor("socket", EditCtx(d), s).value() == s


def test_browsing_changes_nothing(win):
    for path in [("hub", "components", 2), ("hub", "functions", 2), ("sockets", 1), ("app", "functions", 1),
                 ("hub", "globals")]:
        win.select(path)
        assert not win.panel.dirty
    assert not win.doc.dirty


def test_rename_function_updates_binding_and_undo(win):
    orig = copy.deepcopy(win.doc.data)
    win.select(("hub", "functions", 0))
    win.panel.editor.id.setText("7")
    win.panel.apply()
    assert win.doc.data["hub"]["components"][2]["events"][0]["functions"][0]["functionId"] == "7"
    assert win.verify_result.ok
    assert win.windowTitle().endswith("*")
    win.action_undo()
    assert win.doc.data == orig and not win.doc.dirty


def test_delete_used_function_marks_the_binding(win):
    win.delete_item(("hub", "functions", 0))
    assert not win.verify_result.ok
    assert ("hub", "components", 2) in win._errors
    win.action_undo()
    assert win.verify_result.ok and not win._errors


def test_json_tab_edit(win):
    win.select(("hub", "components", 0))
    win.panel.tabs.setCurrentIndex(1)
    v = json.loads(win.panel.json.toPlainText())
    v["#name"] = "Led Green renamed"
    win.panel.json.setPlainText(json.dumps(v))
    win.panel.apply()
    assert win.doc.get(("hub", "components", 0, "#name")) == "Led Green renamed"


def test_editing_locked_while_running(win):
    win.action_run()
    assert win.running and not win.panel.btn_delete.isEnabled()
    win.select(("hub", "components", 0))
    assert not win.panel.editor.isEnabled()
    win.delete_item(("hub", "components", 0))
    assert len(win.doc.data["hub"]["components"]) == 4
    win.action_stop()


def test_build_a_project_from_new(win, qapp):
    """New project; add a hub led, an app button, a socket and two functions with the forms only."""
    win.action_new()
    assert win.project.path is None and win.doc.data["hub"]["components"] == []

    win.add_component("hub", "led")                     # led A
    win.add_component("app", "button")                  # button A
    win.add_component("app", "led")                     # led A (app indicator)

    # socket LEDS0: payload <state:uint8>, one schema, hub runs function 0
    win.add_socket()
    ed = win.panel.editor
    ed.proto.setText("LEDS0")
    ed.common.add_item({"#name": "state", "type": "uint8", "maxLen": 1})
    win.panel.apply()
    assert win.doc.data["sockets"][0]["protocol"] == "LEDS0"

    # hub function 0: led A on / off from arg 0
    win.add_function("hub", "conditional")
    ed = win.panel.editor
    ed.name.setText("set led")
    ed._args.add_item({"#name": "state", "type": "uint8"})
    groups = ed.body.w
    g0 = groups.widgets()[0]
    cond = g0.conds.widgets()[0]
    cond.left.kind.set_value("arg")
    g0.ops.add_item({"type": "command", "componentType": "led", "componentId": "A", "commandId": 1})
    groups.add_item({"type": "else", "onTrue": [
        {"type": "command", "componentType": "led", "componentId": "A", "commandId": 2}]})
    cond.right.kind.set_value("fixed")
    cond.right._body.val.setText("1")
    win.panel.apply()
    fn = win.doc.data["hub"]["functions"][0]
    c = fn["conditionBlock"]["conditionGroups"][0]["conditions"][0]
    assert c["leftVar"] == {"argIndex": 0} and c["rightFixedVal"] == 1, c
    assert [g["type"] for g in fn["conditionBlock"]["conditionGroups"]] == ["if", "else"]

    # bind the socket schema to the hub function, state from common arg 0
    win.select(("sockets", 0))
    sch = win.panel.editor.schemas.widgets()[0]
    sch.hub_fns.add_item({"functionId": "0", "args": [{"commonArgsIndex": 0}]})
    win.panel.apply()

    # app function: send 1 on press; bound to the app button's press event
    win.add_function("app", "basic")
    ed = win.panel.editor
    ed.body.w.add_item({"type": "message", "protocol": "LEDS0", "schema": 0, "args": [{"fixedVal": 1}]})
    ed.body.w.add_item({"type": "command", "componentType": "led", "componentId": "A", "commandId": 1})
    win.panel.apply()
    win.select(("app", "components", 0))
    _, _, press = win.panel.editor._event_edits[0]
    press.add_item({"functionId": "0", "args": None})
    win.panel.apply()

    assert win.verify_result.ok, win.verify_result.errors
    hub = json.loads(win.project.generate())
    assert hub["sockets"][0]["schemas"][0]["functions"][0]["functionId"] == "0"

    # the app side runs: pressing the button lights the app led (no hub: the message is dropped)
    win.action_run()
    win.app_program.resolve("button", "A").user_press()
    qapp.processEvents()
    assert win.app_program.resolve("led", "A").lit
    win.action_stop()


def test_hub_slots_controls_and_handler_inset(win, qapp):
    """New project: led on D4 from the slot menu, connect a new socket to "on" from the
    Controls section; the connection shows inside the led's card."""
    from brixbot_app.engine.hardware import load_hardware
    from brixbot_app.gui.cards import Section

    win.action_new()
    hw = load_hardware(win.project.hardware)
    win.add_component("hub", "led", hw.slots["led"][1])
    assert win.doc.get(("hub", "components", 0, "hwSlot")) == 1
    hub = json.loads(win.project.generate())
    assert [c["id"] for c in hub["components"]] == ["B", "A"]     # placeholder keeps A on D4

    # the slot menu greys out the used slot
    menu = QtWidgets.QMenu()
    win._fill_hub_component_menu(menu)
    led = next(a.menu() for a in menu.actions() if a.text() == "led")
    assert [(a.text(), a.isEnabled()) for a in led.actions()][:2] == [("led D5", True), ("led D4", False)]
    assert [u for _, u in win.available_rows()][:2] == ["", "led A (D4)"]

    win.pick_control_target = lambda title: (None, None)        # new socket, new schema
    win.select(("hub", "components", 0))
    win.panel.editor.ctx.on_control("connect", 1, None)
    assert win.doc.data["sockets"][0]["protocol"] == "LEDA0"
    assert win.verify_result.ok, win.verify_result.errors
    qapp.processEvents()
    secs = [s for s in win.col_hub.findChildren(Section) if not s.card.isHidden()]
    assert any("on ← LEDA0 '1'" in r.text() for s in secs for r in s.rows)
    # the binding is shown in the card, not again under Socket handlers
    from brixbot_app.gui.cards import Card
    assert not [c for c in win.col_hub.findChildren(Card) if c.kind == "handler" and not c.isHidden()]

    win.action_undo()
    assert win.doc.data["sockets"] == []


def test_binding_mode_label(qapp):
    from brixbot_app.gui.edit.items import BindingEdit
    b = BindingEdit(EditCtx(demo(), "hub"), {"functionId": "2", "args": None}, lambda: [])
    assert b.mode.currentText() == "no args transferred"
    assert b.value() == {"functionId": "2", "args": None}


def test_no_functions_message(qapp):
    from PySide6.QtWidgets import QLabel
    d = {"hardware": "brixbot-uno-r4-v1", "hub": {}, "app": {"functions": [{"id": "0", "type": "basic"}]},
         "sockets": [{"protocol": "SOCKA", "schemas": [{"constantArgs": [], "args": []}]}]}
    ed = make_editor("socket", EditCtx(d), d["sockets"][0])
    sch = ed.schemas.widgets()[0]
    texts = [lbl.text() for lbl in sch.hub_fns.findChildren(QLabel)]
    assert any("There are no functions on the hub yet" in t for t in texts)
    assert not sch.hub_fns.add_btn.isEnabled()
    assert sch.app_fns.add_btn.isEnabled()
    assert not any("no functions" in lbl.text() for lbl in sch.app_fns.findChildren(QLabel))


def test_new_handler_function_for_event(win):
    win.action_new()
    win.add_component("app", "switch")
    win.select(("app", "components", 0))
    win.panel.editor.ctx.on_control("new_handler", 1, None)
    fn = win.doc.data["app"]["functions"][0]
    assert fn["#name"] == "switch A change"
    assert [(a["#name"], a["type"]) for a in fn["args"]] == [("id", "char"), ("on", "bool")]
    ev = win.doc.data["app"]["components"][0]["events"]
    assert ev == [{"eventId": 1, "functions": [{"functionId": "0",
                                                "args": [{"outArgIndex": 0}, {"outArgIndex": 1}]}]}]
    assert win.selected == ("app", "functions", 0)
    assert win.verify_result.ok, win.verify_result.errors
    win.action_undo()
    assert win.doc.data["app"]["functions"] == []
