"""Hardware slots (available components, pin order) and controls (socket -> command)."""

import copy
import json

from brixbot_app.editor import controls, templates
from brixbot_app.editor.document import Document
from brixbot_app.engine import errors as E
from brixbot_app.engine.hardware import assign_slots, free_slots, hub_layout, load_hardware
from brixbot_app.engine.project import DEFAULT_HARDWARE, HARDWARE_TYPES, Project
from brixbot_app.engine.specs import HUB_SPECS

HW = load_hardware(DEFAULT_HARDWARE)


def test_hardware_file_matches_specs():
    assert DEFAULT_HARDWARE in HARDWARE_TYPES
    assert set(HW.slots) == set(HUB_SPECS)
    for t, spec in HUB_SPECS.items():
        assert len(HW.slots[t]) == spec.slots, t
    assert [s.pin for s in HW.slots["led"]] == ["D5", "D4", "D3", "D2"]
    assert [s.pin for s in HW.slots["button"]] == ["D7", "D6", "D8"]


def test_slots_explicit_and_first_free():
    comps = [{"type": "led", "id": "A", "hwSlot": 2}, {"type": "led", "id": "B"}, {"type": "led", "id": "C"},
             {"type": "timer", "id": "A"}]
    got = {i: s.pin for i, s in assign_slots(HW, comps).items() if s.pin}
    assert got == {0: "D3", 1: "D5", 2: "D4"}
    assert [s.pin for s in free_slots(HW, comps, "led")] == ["D2"]
    assert [s.pin for s in free_slots(HW, comps, "led", keep=0)] == ["D3", "D2"]


def test_layout_orders_by_slot_and_pads():
    comps = [{"type": "timer", "id": "A"}, {"type": "led", "id": "A", "hwSlot": 2},
             {"type": "button", "id": "A", "hwSlot": 1}]
    out, src = hub_layout(HW, comps)
    assert [(c["type"], c["id"]) for c in out] == [
        ("led", "B"), ("led", "C"), ("led", "A"), ("button", "B"), ("button", "A"), ("timer", "A")]
    assert src == [None, None, 1, None, 2, 0]
    p = Project({"hardware": DEFAULT_HARDWARE, "hub": {"components": comps}, "sockets": [], "app": {}})
    assert "hwSlot" not in p.generate()
    assert p.verify().ok


def test_verify_index_points_at_project_component():
    # an unknown event on the led in slot D3: generated index 2, project index 1
    comps = [{"type": "timer", "id": "A"},
             {"type": "led", "id": "A", "hwSlot": 2, "events": [{"eventId": 9, "functions": [{"functionId": "0"}]}]}]
    res = Project({"hardware": DEFAULT_HARDWARE, "hub": {"components": comps}, "sockets": [], "app": {}}).verify()
    assert not res.ok
    side, exc = res.issues[0]
    assert side == "hub" and exc.index == 1


def test_slot_errors():
    comps = [{"type": "led", "id": "A", "hwSlot": 0}, {"type": "led", "id": "B", "hwSlot": 0},
             {"type": "led", "id": "C", "hwSlot": 7}]
    res = Project({"hardware": DEFAULT_HARDWARE, "hub": {"components": comps}, "sockets": [], "app": {}}).verify()
    bad = sorted(exc.index for _, exc in res.issues if exc.code == E.NO_HW_SLOT)
    assert bad == [1, 2]


def test_new_component_takes_slot():
    c = templates.new_component("hub", "led", [], HW.slots["led"][1])
    assert c == {"type": "led", "id": "A", "#name": "led A (D4)", "hwSlot": 1}


def test_connect_and_disconnect_controls():
    data = templates.new_project()
    doc = Document(Project(data))
    doc.insert(("hub", "components"), {"type": "led", "id": "A", "hwSlot": 0})
    orig = copy.deepcopy(doc.data)
    comp = doc.get(("hub", "components", 0))
    spec = HUB_SPECS["led"]
    doc.apply("on", lambda d: controls.connect(d, "hub", comp, spec.command(1)))
    doc.apply("off", lambda d: controls.connect(d, "hub", comp, spec.command(2), socket=0))
    doc.apply("flash", lambda d: controls.connect(d, "hub", comp, spec.command(3), socket=0))
    doc.apply("on again", lambda d: controls.connect(d, "hub", comp, spec.command(1), socket=0, schema=1))
    d = doc.data
    assert [s["protocol"] for s in d["sockets"]] == ["LEDA0"]
    assert [sc["constantArgs"][0]["value"] for sc in d["sockets"][0]["schemas"]] == ["1", "0", "F"]
    assert len(d["hub"]["functions"]) == 3               # "on" reused
    assert Project(d).verify().ok
    hub = json.loads(Project(d).generate())
    flash = hub["sockets"][0]["schemas"][2]
    assert flash["functions"][0] == {"functionId": "2", "args": [{"argsIndex": 0}, {"argsIndex": 1}, {"argsIndex": 2}]}

    on = controls.connections(d, "hub", comp, 1)
    assert [(c.schema, c.payload) for c in on] == [(0, "1"), (1, "0")]
    doc.apply("dis", lambda d: controls.disconnect(d, "hub", on[1]))
    assert len(controls.connections(doc.data, "hub", comp, 1)) == 1
    assert len(doc.data["hub"]["functions"]) == 3        # still used by schema 0
    off = controls.connections(doc.data, "hub", comp, 2)[0]
    doc.apply("dis", lambda d: controls.disconnect(d, "hub", off))
    assert [f["#name"] for f in doc.data["hub"]["functions"]] == ["led A on", "led A flash"]
    assert Project(doc.data).verify().ok
    while doc.can_undo() and doc.data != orig:
        doc.undo()
    assert doc.data == orig


def test_control_commands():
    assert [c.name for c in controls.control_commands(HUB_SPECS["led"])] == ["on", "off", "flash"]
    assert controls.control_commands(HUB_SPECS["button"]) == []
    t = controls.control_commands(HUB_SPECS["timer"])
    assert [c.name for c in t] == ["start", "stop", "setTimeout"]


def test_control_arg_types_follow_the_command():
    from brixbot_app.engine.specs import APP_SPECS
    data = templates.new_project()
    sw = {"type": "switch", "id": "A"}
    data["app"]["components"].append(sw)
    controls.connect(data, "app", sw, APP_SPECS["switch"].command(1))
    assert data["sockets"][0]["schemas"][0]["args"][0]["type"] == "bool"
    assert data["app"]["functions"][0]["args"][0]["type"] == "bool"
    assert Project(data).verify().ok
    tm = {"type": "timer", "id": "A", "hwSlot": 0}
    data["hub"]["components"].append(tm)
    controls.connect(data, "hub", tm, HUB_SPECS["timer"].command(3))
    assert data["sockets"][1]["schemas"][0]["args"][0]["type"] == "uint16"
