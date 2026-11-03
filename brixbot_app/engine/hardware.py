"""Hardware descriptions: the components physically available on each hub type.

One JSON file per hardware type lives in ``brixbot_app/hardware/``. It lists,
per component type, the slots the firmware hands out in order (a led slot is
a pin). It mirrors the hub's ``Brix_HwConfig.cpp``.

A project hub component records its slot in the app-only key ``hwSlot`` (an
index within its type). Components without one take the first free slots in
file order, so older project files keep their pins. The firmware assigns
slots by declaration order, so ``hub_layout()`` orders the components by slot
and pads unused lower pin slots with placeholders.

No Qt.
"""

from __future__ import annotations

import json
import string
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

HW_DIR = Path(__file__).resolve().parent.parent / "hardware"
SLOT_KEY = "hwSlot"
_IDS = string.ascii_uppercase + string.digits


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


def assign_slots(hw: Hardware | None, components: list) -> dict[int, Slot | None]:
    """{component index: slot} for the components of types the hardware has.
    An explicit, valid, unused hwSlot wins; the rest take the first free slots
    in file order (None when there is none left, or the hwSlot is bad)."""
    out: dict[int, Slot | None] = {}
    if hw is None:
        return out
    used: dict[str, set[int]] = {}
    rest: list[tuple[int, str]] = []
    for i, c in enumerate(components):
        if not isinstance(c, dict) or c.get("type") not in hw.slots:
            continue
        t = c["type"]
        if SLOT_KEY in c:
            s = hw.slot(t, c[SLOT_KEY])
            if s is not None and s.index not in used.setdefault(t, set()):
                used[t].add(s.index)
                out[i] = s
            else:
                out[i] = None
        else:
            rest.append((i, t))
    for i, t in rest:
        u = used.setdefault(t, set())
        s = next((s for s in hw.slots[t] if s.index not in u), None)
        if s is not None:
            u.add(s.index)
        out[i] = s
    return out


def free_slots(hw: Hardware | None, components: list, ctype: str, keep: int | None = None) -> list[Slot]:
    """Slots of a type not used by any component (except component index keep)."""
    if hw is None:
        return []
    taken = {s.index for i, s in assign_slots(hw, components).items()
             if s is not None and s.type == ctype and i != keep}
    return [s for s in hw.slots.get(ctype, []) if s.index not in taken]


def slot_errors(hw: Hardware | None, components: list) -> list[tuple[int, str]]:
    """(component index, detail) for each component that gets no slot."""
    errs = []
    for i, s in assign_slots(hw, components).items():
        if s is None:
            c = components[i]
            if SLOT_KEY in c:
                errs.append((i, f"hwSlot {c[SLOT_KEY]!r} is not a free {c.get('type')} slot on this hardware"))
            else:
                errs.append((i, f"no free {c.get('type')} slot left on this hardware"))
    return errs


def hub_layout(hw: Hardware | None, components: list) -> tuple[list, list[int | None]]:
    """Components in the order the firmware needs, and for each one the index of
    the project component it came from (None for a placeholder).

    Components of a pinned type go in slot order, with placeholders in the unused
    lower slots; everything else keeps its place after them."""
    if not isinstance(components, list):
        return components, []
    slots = assign_slots(hw, components)
    out: list = []
    src: list[int | None] = []
    placed: set[int] = set()
    for t, tslots in (hw.slots.items() if hw else ()):
        if not any(s.pin for s in tslots):
            continue
        by_slot = {s.index: i for i, s in slots.items() if s is not None and s.type == t}
        if not by_slot:
            continue
        ids = {c.get("id") for c in components if isinstance(c, dict) and c.get("type") == t}
        for k in range(max(by_slot) + 1):
            if k in by_slot:
                out.append(components[by_slot[k]])
                src.append(by_slot[k])
                placed.add(by_slot[k])
            else:
                pid = next((ch for ch in _IDS if ch not in ids), "?")
                ids.add(pid)
                out.append({"type": t, "id": pid, "#name": f"unused {tslots[k].label}"})
                src.append(None)
    for i, c in enumerate(components):
        if i not in placed:
            out.append(c)
            src.append(i)
    return out, src
