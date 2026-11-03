"""Component type tables: commands and events of every hub and app component.

The hub tables must match the hub firmware (Brix_Led / Brix_Button / Brix_Timer
COMMANDS / EVENTS); they are used to validate a hub config before it is
loaded. The app tables define the PC app's own components.
"""

from __future__ import annotations

from dataclasses import dataclass

from .variable import BOOL, CHAR, NONE, UINT8, UINT16

VARIADIC = -1   # any number of inArgs


@dataclass(frozen=True)
class CommandSpec:
    id: int
    name: str
    num_in: int = 0
    num_out: int = 0
    ret_type: int = NONE
    in_names: tuple[str, ...] = ()
    # in-arg types, where known (app components; the hub firmware does not declare them)
    in_types: tuple[int, ...] = ()


@dataclass(frozen=True)
class EventSpec:
    id: int
    name: str
    out_types: tuple[int, ...] = ()
    out_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComponentSpec:
    type_name: str
    commands: tuple[CommandSpec, ...] = ()
    events: tuple[EventSpec, ...] = ()
    init_args: tuple[str, ...] = ()
    slots: int | None = None        # hardware slots (hub), None = unlimited

    def command(self, cid: int) -> CommandSpec | None:
        return next((c for c in self.commands if c.id == cid), None)

    def event(self, eid: int) -> EventSpec | None:
        return next((e for e in self.events if e.id == eid), None)


COMMAND_RESOLVE = 0     # handled by Command itself for every type

_LED_COMMANDS = (
    CommandSpec(1, "on"),
    CommandSpec(2, "off"),
    CommandSpec(3, "flash", 3, in_names=("onPeriods", "offPeriods", "numFlashes")),
    CommandSpec(4, "getState", ret_type=UINT8),
)
_LED_EVENTS = (EventSpec(1, "flashComplete"),)

_TIMER_COMMANDS = (
    CommandSpec(1, "start"),
    CommandSpec(2, "stop"),
    CommandSpec(3, "setTimeout", 1, in_names=("timeout_mS",)),
    CommandSpec(4, "getRunning", ret_type=BOOL),
)
_TIMER_EVENTS = (EventSpec(1, "timeout", (CHAR,), ("id",)),)

# ------------------------------------------------------------------ hub (firmware v1)
HUB_SPECS: dict[str, ComponentSpec] = {
    "led": ComponentSpec("led", _LED_COMMANDS, _LED_EVENTS, (), slots=4),
    "button": ComponentSpec(
        "button",
        (CommandSpec(1, "getPressed", ret_type=BOOL),),
        (EventSpec(1, "press", (CHAR,), ("id",)),
         EventSpec(2, "release", (CHAR, UINT16), ("id", "duration_mS")),
         EventSpec(3, "longPress", (CHAR,), ("id",))),
        ("releaseActive", "longPressThreshold_mS"),
        slots=3,
    ),
    "timer": ComponentSpec("timer", _TIMER_COMMANDS, _TIMER_EVENTS,
                           ("timeout_mS", "repeat", "autoStart"), slots=4),
}
# known to the config format but without a hub driver yet
HUB_UNSUPPORTED = ("motor", "pot")

HUB_LIMITS = {
    "components": 12,
    "functions": 24,
    "sockets": 6,
    "globals": 16,
    "config_len": 6143,     # BRIX_CONFIG__CONFIG_BUFF_LEN - 1
}

# ------------------------------------------------------------------ app (PC)
APP_SPECS: dict[str, ComponentSpec] = {
    "dial": ComponentSpec(
        "dial",
        (CommandSpec(1, "setValue", 1, in_names=("value",), in_types=(UINT8,)),
         CommandSpec(2, "getValue", ret_type=UINT8)),
        (EventSpec(1, "change", (CHAR, UINT8), ("id", "value")),),
        ("min", "max", "value"),
    ),
    "switch": ComponentSpec(
        "switch",
        (CommandSpec(1, "set", 1, in_names=("on",), in_types=(BOOL,)),
         CommandSpec(2, "get", ret_type=BOOL)),
        (EventSpec(1, "change", (CHAR, BOOL), ("id", "on")),),
        ("value", "onText", "offText"),
    ),
    "button": ComponentSpec(
        "button",
        (CommandSpec(1, "getPressed", ret_type=BOOL),),
        (EventSpec(1, "press", (CHAR,), ("id",)),
         EventSpec(2, "release", (CHAR, UINT16), ("id", "duration_mS"))),
        ("text",),
    ),
    "led": ComponentSpec("led", _LED_COMMANDS, _LED_EVENTS, ("color",)),
    "display": ComponentSpec(
        "display",
        (CommandSpec(1, "setValue", 1, in_names=("value",)),
         CommandSpec(2, "clear")),
        (),
        ("prefix", "suffix"),
    ),
    "log": ComponentSpec(
        "log",
        (CommandSpec(1, "append", VARIADIC, in_names=("values...",)),
         CommandSpec(2, "clear")),
        (),
    ),
    "timer": ComponentSpec("timer", _TIMER_COMMANDS, _TIMER_EVENTS, ("timeout_mS", "repeat", "autoStart")),
}

# component types whose commands carry no meaning without the hub
SIDE_HUB = "hub"
SIDE_APP = "app"
