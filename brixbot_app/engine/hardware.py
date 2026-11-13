"""Hardware descriptions: the components physically available on each hub type.

One JSON file per hardware type lives in ``brixbot_app/hardware/``. It lists,
per component type, the slots the firmware can give it (a led slot is a pin).
It mirrors the hub's ``Brix_HwConfig.cpp``: each type's slots are the hub's
pin table filtered to that type, in table order. A pin listed under several
types is shared: once one component has it, no other component of any type can.

A project hub component records its slot in the app-only key ``hwSlot`` (an
index within its type; a hub "pin" name is accepted too). Components without
one take the first free slots in file order, so older project files keep their
pins. Generate writes each pinned component's pin into the hub key ``pin``
(``hub_layout()``), so the firmware does not depend on component order.

No Qt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

HW_DIR = Path(__file__).resolve().parent.parent / "hardware"
SLOT_KEY = "hwSlot"
PIN_KEY = "pin"         # hub config key naming a component's pin


@dataclass(frozen=True)
class Slot:
    type: str
    index: int
    pin: str = ""

    @property
    def label(self) -> str:
        return f"{self.type} {self.pin}" if self.pin else f"{self.type} {self.index + 1}"


@dataclass
class Hardware:
    id: str
    name: str
    slots: dict[str, list[Slot]] = field(default_factory=dict)

    def slot_by_pin(self, ctype: str, pin) -> Slot | None:
        return next((s for s in self.slots.get(ctype) or [] if s.pin and s.pin == pin), None)

    def slot(self, ctype: str, index) -> Slot | None:
        lst = self.slots.get(ctype) or []
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(lst):
            return lst[index]
        return None


def _parse(d: dict) -> Hardware:
    hw = Hardware(str(d.get("id")), str(d.get("name") or d.get("id")))
    for c in d.get("components") or []:
        t = c.get("type")
        hw.slots[t] = [Slot(t, i, str(s.get("pin") or "")) for i, s in enumerate(c.get("slots") or [])]
    return hw


@lru_cache(maxsize=None)
def _all() -> dict[str, Hardware]:
    out = {}
    for p in sorted(HW_DIR.glob("*.json")):
        hw = _parse(json.loads(p.read_text(encoding="utf-8")))
        out[hw.id] = hw
    return out


def hardware_types() -> dict[str, str]:
    """{id: display name} of every known hardware type."""
    return {k: v.name for k, v in _all().items()}


def load_hardware(hw_id: str) -> Hardware | None:
    return _all().get(hw_id)


def slot_key(s: Slot) -> tuple:
    """What a slot occupies: its pin (shared by every type listing it), else its index."""
    return ("pin", s.pin) if s.pin else (s.type, s.index)


def assign_slots(hw: Hardware | None, components: list) -> dict[int, Slot | None]:
    """{component index: slot} for the components of types the hardware has.
    An explicit (hwSlot, else hub "pin"), valid, unused slot wins; the rest take
    the first free slots in file order (None when there is none left, or the
    explicit slot is bad)."""
    out: dict[int, Slot | None] = {}
    if hw is None:
        return out
    used: set[tuple] = set()
    rest: list[tuple[int, str]] = []
    for i, c in enumerate(components):
        if not isinstance(c, dict) or c.get("type") not in hw.slots:
            continue
        t = c["type"]
        if SLOT_KEY in c or PIN_KEY in c:
            s = hw.slot(t, c[SLOT_KEY]) if SLOT_KEY in c else hw.slot_by_pin(t, c[PIN_KEY])
            if s is not None and slot_key(s) not in used:
                used.add(slot_key(s))
                out[i] = s
            else:
                out[i] = None
        else:
            rest.append((i, t))
    for i, t in rest:
        s = next((s for s in hw.slots[t] if slot_key(s) not in used), None)
        if s is not None:
            used.add(slot_key(s))
        out[i] = s
    return out


def free_slots(hw: Hardware | None, components: list, ctype: str, keep: int | None = None) -> list[Slot]:
    """Slots of a type not used by any component (except component index keep)."""
    if hw is None:
        return []
    taken = {slot_key(s) for i, s in assign_slots(hw, components).items() if s is not None and i != keep}
    return [s for s in hw.slots.get(ctype, []) if slot_key(s) not in taken]


def slot_errors(hw: Hardware | None, components: list) -> list[tuple[int, str]]:
    """(component index, detail) for each component that gets no slot."""
    errs = []
    for i, s in assign_slots(hw, components).items():
        if s is None:
            c = components[i]
            if SLOT_KEY in c:
                errs.append((i, f"hwSlot {c[SLOT_KEY]!r} is not a free {c.get('type')} slot on this hardware"))
            elif PIN_KEY in c:
                errs.append((i, f"pin {c[PIN_KEY]!r} is not a free {c.get('type')} pin on this hardware"))
            else:
                errs.append((i, f"no free {c.get('type')} slot left on this hardware"))
    return errs


def hub_layout(hw: Hardware | None, components: list) -> tuple[list, list[int | None]]:
    """Components as the firmware gets them, and for each one the index of the
    project component it came from (the same order: the list is index for index).

    A component with a pinned slot gets the hub key "pin" set to that pin, so the
    firmware gives it that pin whatever the order. Components without a slot
    (none free, bad hwSlot) are left as they are; verify reports them."""
    if not isinstance(components, list):
        return components, []
    slots = assign_slots(hw, components)
    out: list = []
    for i, c in enumerate(components):
        s = slots.get(i)
        if s is not None and s.pin:
            c = {**c, PIN_KEY: s.pin}
        out.append(c)
    return out, list(range(len(components)))
