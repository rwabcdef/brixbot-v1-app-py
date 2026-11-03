"""The Qt-free editor model: Document undo / redo, templates and cross references."""

import copy

from brixbot_app.editor import refs, templates
from brixbot_app.editor.document import Document
from brixbot_app.engine.project import Project
from brixbot_app.gui.main_window import EXAMPLES  # noqa: F401  (path only, no Qt app needed)

DEMO = EXAMPLES / "demo_project.json"


def demo_doc() -> Document:
    return Document(Project.load(DEMO))


def test_document_undo_redo_and_dirty():
    doc = demo_doc()
    orig = copy.deepcopy(doc.data)
    seen = []
    doc.observers.append(lambda: seen.append(1))
    assert not doc.dirty and not doc.can_undo()

    p = doc.insert(("hub", "components"), {"type": "led", "id": "Y"}, label="add led")
    assert p == ("hub", "components", 4) and doc.dirty
    doc.set(("hub", "components", 4, "id"), "Z")
    doc.move(p, -4)
    assert doc.get(("hub", "components", 0, "id")) == "Z"
    assert doc.undo_label() == "move"

    while doc.can_undo():
        doc.undo()
    assert doc.data == orig and not doc.dirty
    doc.redo()
    assert doc.dirty and doc.get(("hub", "components", 4, "type")) == "led"
    assert len(seen) == 3 + 3 + 1

    # a change that changes nothing is not an undo step
    n = len(doc._undo)
    doc.apply("noop", lambda d: None)
    assert len(doc._undo) == n

    doc.mark_saved()
    assert not doc.dirty
    doc.undo()
    assert doc.dirty


def test_document_creates_missing_lists():
    doc = Document(Project({"hardware": "brixbot-uno-r4-v1"}))
    p = doc.insert(("app", "functions"), {"id": "0"})
    assert p == ("app", "functions", 0) and doc.data["app"]["functions"] == [{"id": "0"}]
    assert doc.data["sockets"] == []


def test_templates_pick_free_ids_and_verify():
    comps = [{"type": "led", "id": "A"}, {"type": "led", "id": "B"}, {"type": "button", "id": "A"}]
    assert templates.free_component_id(comps, "led") == "C"
    assert templates.free_component_id(comps, "timer") == "A"
    assert templates.next_function_id([{"id": "0"}, {"id": "4"}]) == "5"
    assert templates.next_function_id([{"id": 10}, {"id": 15}]) == 16
    assert templates.next_function_id([]) == "0"
    assert templates.free_protocol([{"protocol": "SOCKA"}]) == "SOCKB"

    # a project made only of templates verifies
    data = templates.new_project()
    data["hub"]["components"].append(templates.new_component("hub", "led", []))
    data["hub"]["functions"].append(templates.new_function("basic", []))
    data["hub"]["functions"][0]["operationBlock"].append(
        templates.new_operation("command", "hub", data["hub"]["components"], []))
    data["hub"]["functions"].append(templates.new_function("conditional", data["hub"]["functions"]))
    data["sockets"].append(templates.new_socket([]))
    res = Project(data).verify()
    assert [e.detail for _, e in res.issues] == ["SOCKA schema 0 needs a name"]    # the one thing to fill in
    data["sockets"][0]["schemas"][0]["#name"] = "message"
    res = Project(data).verify()
    assert res.ok, res.errors


def test_function_rename_updates_bindings():
    doc = demo_doc()
    d = doc.data
    assert refs.function_users(d, "hub", "3") == ["socket LED00 schema 0", "socket LED00 schema 1"]
    assert refs.rename_function(d, "hub", "0", "9") == 1
    assert d["hub"]["components"][2]["events"][0]["functions"][0]["functionId"] == "9"
    # app ids are ints in the demo; "12" matches 12
    assert refs.function_users(d, "app", "12") == ["socket BUTT0 schema 0"]


def test_component_and_socket_rename():
    doc = demo_doc()
    d = doc.data
    assert refs.component_users(d, "hub", "led", "R") == ["function timerA timeout"]
    n = refs.rename_component(d, "hub", ("led", "R"), ("led", "Q"))
    assert n == 3          # on, flash, off in timerA timeout
    assert refs.component_users(d, "hub", "led", "R") == []
    assert refs.rename_socket(d, "LED00", "LED01") == 5      # app functions 10, 11 (x2), 13, 14
    d["sockets"][1]["protocol"] = "LED01"
    d["hub"]["components"][1]["id"] = "Q"
    assert Project(d).verify().ok


def test_reindex_globals():
    doc = demo_doc()
    d = doc.data
    d["hub"]["globals"].insert(0, {"type": "uint8"})
    refs.reindex_globals(d, "hub", {0: 1})
    assert d["hub"]["functions"][0]["operationBlock"][2]["leftVar"] == {"globalVariableIndex": 1}
    assert d["hub"]["functions"][2]["condVars"][1]["globalVariableIndex"] == 1
    assert Project(d).verify().ok


def test_function_text():
    d = demo_doc().data
    text = refs.function_text(d["hub"]["functions"][2], "hub", d["hub"]["globals"])
    assert "if (cond0 == true) && (cond1 >= 3):" in text
    assert "led R.flash(2, 2, 0)" in text
    assert "else:" in text
    text = refs.function_text(d["hub"]["functions"][0], "hub", d["hub"]["globals"])
    assert "button A press count += 1" in text
    assert "send BUTT0[0](button id)" in text


def test_schema_name_required_and_migrated(tmp_path):
    import json
    from brixbot_app.engine.project import migrate
    old = {"hardware": "brixbot-uno-r4-v1", "hub": {}, "app": {}, "sockets": [{"protocol": "SOCKA", "schemas": [
        {"#comment": "led on", "constantArgs": [{"type": "char", "value": "1"}]},
        {"#name": "led off", "#comment": "notes", "constantArgs": [{"type": "char", "value": "0"}]},
        {"constantArgs": [{"type": "char", "value": "F"}]}]}]}
    f = tmp_path / "old.json"
    f.write_text(json.dumps(old), encoding="utf-8")
    p = Project.load(f)
    sc = p.data["sockets"][0]["schemas"]
    assert sc[0] == {"#name": "led on", "constantArgs": [{"type": "char", "value": "1"}]}
    assert sc[1]["#name"] == "led off" and sc[1]["#comment"] == "notes"
    res = p.verify()
    assert [(e.section, e.index, e.detail) for _, e in res.issues] == [("S", 0, "SOCKA schema 2 needs a name")]
    sc[2]["#name"] = "led flash"
    assert p.verify().ok
    migrate(p.data)                                    # idempotent
    assert sc[0]["#name"] == "led on"


def test_app_bool_and_int_settings_are_parsed_strictly():
    def build(args, ctype="switch"):
        return Project({"hardware": "brixbot-uno-r4-v1", "hub": {}, "sockets": [],
                        "app": {"components": [{"type": ctype, "id": "A", "args": args}]}}).verify()
    for v, on in (("off", False), ("on", True), ("False", False), (True, True), (0, False), ("1", True)):
        res = build([{"key": "value", "value": v}])
        assert res.ok and res.app.resolve("switch", "A").on is on, v
    assert not build([{"key": "value", "value": "maybe"}]).ok
    assert not build([{"key": "repeat", "value": "sometimes"}], "timer").ok
    assert not build([{"key": "max", "value": "lots"}], "dial").ok
    assert build([{"key": "max", "value": "40"}], "dial").ok
