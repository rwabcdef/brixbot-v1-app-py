"""The app's radio button group component: model, settings, project use and view."""

import os

import pytest

from brixbot_app.editor import controls, templates
from brixbot_app.engine import errors as E
from brixbot_app.engine.app_components import RadioModel, parse_radio_buttons
from brixbot_app.engine.project import Project
from brixbot_app.engine.runtime import Command
from brixbot_app.engine.specs import APP_SPECS
from brixbot_app.engine.variable import CHAR, NONE, UINT8, Variable

SPEC = APP_SPECS["radio"]
BUTTONS = {"args": [{"key": "buttons", "value": "F=forward, D=disabled, R=reverse"}]}


def test_parse_buttons():
    assert parse_radio_buttons("F=forward, D=disabled ,R=reverse") == [
        ("F", "forward"), ("D", "disabled"), ("R", "reverse")]
    assert parse_radio_buttons("A, B=bee") == [("A", "A"), ("B", "bee")]
    for bad in ("", "FW=forward", "F=a, F=b", " , ", None):
        with pytest.raises(E.BuildError):
            parse_radio_buttons(bad)


def test_value_set_get_and_change_event():
    r = RadioModel(SPEC, "A", BUTTONS)
    assert r.value == "F"                                   # first button by default
    events = []
    r.event_sink = lambda c, e, o: events.append((e, [v.value for v in o]))
    r.start()
    r.user_select("R")
    assert r.value == "R" and events == [(1, ["A", "R"])]
    r.user_select("R")                                      # no change, no event
    r.user_select("X")                                      # not a button
    assert len(events) == 1

    def cmd(cid, *args):
        c = Command()
        c.component, c.command_id, c.in_args = r, cid, list(args)
        c.ret_arg = Variable(NONE)
        return c.execute(), c.ret_arg.value
    assert cmd(1, Variable(CHAR, "D"))[0] == E.OK
    assert r.value == "D" and len(events) == 1              # commands never raise events
    assert cmd(1, Variable(CHAR, "Q"))[0] == E.RUNTIME      # unknown value
    assert cmd(1, Variable(UINT8, ord("F")))[0] == E.OK     # a char code works too
    assert cmd(2) == (E.OK, "F")


def test_start_value_and_bad_settings():
    r = RadioModel(SPEC, "A", {"args": BUTTONS["args"] + [{"key": "value", "value": "D"}]})
    assert r.value == "D"
    digits = RadioModel(SPEC, "A", {"args": [{"key": "buttons", "value": "0=idle, 1=fwd, 2=rev"},
                                             {"key": "value", "value": 1}]})
    assert digits.value == "1"
    with pytest.raises(E.BuildError):
        RadioModel(SPEC, "A", {"args": BUTTONS["args"] + [{"key": "value", "value": "Z"}]})


def test_new_radio_in_a_project():
    data = templates.new_project()
    comps = data["app"]["components"]
    r = templates.new_component("app", "radio", comps)
    assert r["args"] == [{"key": "buttons", "value": "F=forward, I=idle, R=reverse"}]
    comps.append(r)
    # a handler taking (id, value), run on change
    fid, _ = controls.new_event_handler(data, "app", 0, SPEC.event(1))
    fn = next(f for f in data["app"]["functions"] if f["id"] == fid)
    assert [a["type"] for a in fn["args"]] == ["char", "char"]
    res = Project(data).verify()
    assert res.ok, res.errors
    prog = Project(data).build_app()
    assert prog.resolve("radio", "A").buttons[1] == ("I", "idle")

    data["app"]["components"][0]["args"] = [{"key": "buttons", "value": "FF=x"}]
    assert not Project(data).verify().ok                    # bad buttons setting reported


def test_view_selects_and_follows_the_model():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from brixbot_app.gui.app_widgets import make_view
    r = RadioModel(SPEC, "A", BUTTONS)
    events = []
    r.event_sink = lambda c, e, o: events.append(o[1].value)
    r.start()
    v = make_view(r)
    assert [rb.text() for rb in v.radios.values()] == ["forward", "disabled", "reverse"]
    assert v.radios["F"].isChecked()
    v.radios["R"].click()
    app.processEvents()
    assert r.value == "R" and events == ["R"]
    c = Command()
    c.component, c.command_id, c.in_args = r, 1, [Variable(CHAR, "D")]
    c.execute()
    assert v.radios["D"].isChecked() and events == ["R"]    # set by a command: no event
