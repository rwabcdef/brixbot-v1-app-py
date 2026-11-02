import json
from pathlib import Path

import pytest

from brixbot_app.engine import errors as E
from brixbot_app.engine.builder import build_side
from brixbot_app.engine.project import Project, strip_for_hub
from brixbot_app.engine.specs import SIDE_HUB
from brixbot_app.engine.variable import (
    BOOL, CHAR, FLOAT, INT8, INT16, STR10, UINT8, UINT16, Variable, statement_op,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo_project.json"
HUB_EXAMPLE = ROOT.parent / "hub" / "examples" / "example_config.json"


# ---------------------------------------------------------------- Variable (hub parity)
@pytest.mark.parametrize("vtype,value,width,text", [
    (UINT8, 7, 3, "007"), (UINT16, 350, 5, "00350"), (INT16, -27, 6, "-00027"), (INT8, 5, 4, "0005"),
    (BOOL, True, 1, "1"), (CHAR, "A", 1, "A"), (FLOAT, -1.5, 0, "-1.50"), (STR10, "hi", 0, "hi"),
])
def test_to_str_matches_hub(vtype, value, width, text):
    assert Variable(vtype, value).to_str(width, 64) == text
    back = Variable(vtype)
    assert back.from_str(text)


def test_from_str_ranges():
    assert not Variable(UINT8).from_str("256")
    assert not Variable(UINT8).from_str("-1")
    assert not Variable(INT8).from_str("-129")
    assert not Variable(BOOL).from_str("2")


def test_assign_converts_into_destination_type():
    d = Variable(UINT8)
    d.assign_from(Variable(UINT16, 300))
    assert d.value == 300 & 0xFF
    n = Variable()
    n.assign_from(Variable(CHAR, "x"))
    assert n.type == CHAR and n.value == "x"


def test_statement_c_semantics():
    v = Variable(INT16, -7)
    assert statement_op(v, 5, Variable(UINT8, 2))
    assert v.value == -3            # C truncation, not floor
    assert not statement_op(v, 5, Variable(UINT8, 0))


# ---------------------------------------------------------------- demo project
def test_demo_verifies():
    res = Project.load(DEMO).verify()
    assert res.ok, res.errors
    assert res.hub_config_len < 6143


@pytest.mark.skipif(not HUB_EXAMPLE.exists(), reason="hub repo not alongside")
def test_demo_hub_part_is_the_hub_example():
    hub_example = strip_for_hub(json.loads(HUB_EXAMPLE.read_text(encoding="utf-8")))
    assert Project.load(DEMO).hub_config() == hub_example


def test_generate_strips_comments_and_app_keys():
    s = Project.load(DEMO).generate()
    assert "#" not in s and "appFunctions" not in s and "null" not in s and " " not in s


# ---------------------------------------------------------------- hub error codes (as returned by the board)
def hub_err(cfg):
    with pytest.raises(E.BuildError) as ei:
        build_side(SIDE_HUB, cfg, cfg.get("sockets", []))
    e = ei.value
    return f"{e.code:03d}{e.section}{e.index:03d}"


def test_hub_errors_match_firmware():
    assert hub_err({"components": [{"type": "motor", "id": "A"}]}) == "003C000"
    assert hub_err({"components": [{"type": "led", "id": c} for c in "ABCDE"]}) == "004C004"
    assert hub_err({"functions": [{"id": 0, "operationBlock": [
        {"type": "statement", "leftVar": {"argIndex": 0}, "operator": "equals", "rightFixedVal": 1}]}]}) == "006F000"
    assert hub_err({"components": [{"type": "timer", "id": "A",
                                    "events": [{"eventId": 1, "functions": [{"functionId": 9}]}]}]}) == "009E000"
    assert hub_err({"components": [{"type": "led", "id": "G"}], "functions": [
        {"id": 0, "operationBlock": [{"type": "command", "componentType": "led", "componentId": "G", "commandId": 3}]}]}) == "011F000"
    cond = {"leftFixedVal": 1, "operator": "equal", "rightFixedVal": 1}
    assert hub_err({"functions": [{"id": 0, "type": "conditional", "conditionBlock": {"conditionGroups": [
        {"type": "if", "conditions": [cond], "onTrue": []}, {"type": "else", "onTrue": []},
        {"type": "elif", "conditions": [cond], "onTrue": []}]}}]}) == "017F000"


def test_verify_reports_names():
    p = Project.load(DEMO)
    p.data["app"]["functions"][0]["operationBlock"][0]["protocol"] = "NOPE0"
    res = p.verify()
    assert not res.ok
    assert "app functions[0]" in res.errors[0] and "dial -> green flash" in res.errors[0]


# ---------------------------------------------------------------- app runtime
@pytest.fixture
def app():
    prog = Project.load(DEMO).build_app()
    sent = []
    errors = []
    prog.sender = lambda proto, data, ack: sent.append(f"{proto}:{data}")
    prog.on_error = errors.append
    prog.start()
    yield prog, sent, errors
    assert not errors


def comp(prog, tname, cid):
    return prog.resolve(tname, cid)


def test_dial_sends_flash(app):
    prog, sent, _ = app
    comp(prog, "dial", "A").user_change(6)
    assert sent == ["LED00:GF006006000"]
    assert comp(prog, "log", "L").lines[-1].endswith("flash 6")


def test_switch_branches(app):
    prog, sent, _ = app
    sw = comp(prog, "switch", "D")
    sw.user_set(True)
    sw.user_set(False)
    assert sent == ["LED00:R1", "LED00:R0"]


def test_button_press_release(app):
    prog, sent, _ = app
    b = comp(prog, "button", "G")
    b.user_press()
    b.user_release()
    assert sent == ["LED00:G1", "LED00:G0"]


def test_hub_messages_run_app_functions(app):
    prog, _, _ = app
    led = comp(prog, "led", "A")
    assert prog.handle_frame("BUTT0", "AP")
    assert led.mode == 1
    assert prog.handle_frame("BUTT0", "AR00350")
    assert led.mode == 0
    assert comp(prog, "display", "T").text == "350"
    assert comp(prog, "log", "L").lines[-1].endswith("release A 350 mS")


def test_not_running_ignores_input():
    prog = Project.load(DEMO).build_app()
    sent = []
    prog.sender = lambda *a: sent.append(a)
    comp(prog, "dial", "A").user_change(3)
    assert sent == [] and not prog.handle_frame("BUTT0", "AP")


def test_led_flash_and_timer_tick():
    from brixbot_app.engine.app_components import TimerModel, LedModel
    from brixbot_app.engine.runtime import Command
    from brixbot_app.engine.specs import APP_SPECS

    led = LedModel(APP_SPECS["led"], "X", {})
    events = []
    led.event_sink = lambda c, e, o: events.append(e)
    led.start()
    cmd = Command()
    cmd.component, cmd.command_id = led, 3
    cmd.in_args = [Variable(UINT8, 1), Variable(UINT8, 1), Variable(UINT8, 2)]
    assert cmd.execute() == E.OK
    t = led._next_ms
    for i in range(6):
        led.tick(t + i * 250)
    assert events == [1] and led.mode == 0

    timer = TimerModel(APP_SPECS["timer"], "T", {"args": [{"key": "timeout_mS", "value": 100}]})
    fired = []
    timer.event_sink = lambda c, e, o: fired.append(o[0].value)
    timer.start()
    cmd = Command()
    cmd.component, cmd.command_id = timer, 1
    cmd.execute()
    timer.tick(timer._due)
    assert fired == ["T"] and not timer.active
