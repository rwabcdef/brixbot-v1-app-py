"""BrixBot project file - one human readable JSON file holding both sides:

    {
      "hardware": "brixbot-uno-r4-v1",
      "hub":     { "globals": [...], "components": [...], "functions": [...] },
      "sockets": [ ... ],     # shared by hub and app (the Link column)
      "app":     { "globals": [...], "components": [...], "functions": [...] }
    }

Socket schemas bind receive-side functions per side: "functions" are hub
functions (run when the hub receives the schema), "appFunctions" are app
functions (run when the app receives it). Keys starting with '#' are comments.

generate() produces the hub's config string: the hub part plus the sockets,
without app keys, comments, nulls or empty arrays, minified. The hub
components are put in the order the hardware gives out its slots (see
hardware.py; a component's slot is its app-only "hwSlot").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import errors as E
from .app_components import app_factory
from .builder import build_side
from .hardware import SLOT_KEY, hardware_types, hub_layout, load_hardware, slot_errors
from .runtime import Program
from .specs import HUB_LIMITS, SIDE_APP, SIDE_HUB

HARDWARE_TYPES = hardware_types()
DEFAULT_HARDWARE = "brixbot-uno-r4-v1"
APP_ONLY_KEYS = ("appFunctions", SLOT_KEY)


def strip_for_hub(o):
    """Remove comments, app-only keys, nulls and empty containers (recursively)."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if k.startswith("#") or k in APP_ONLY_KEYS or v is None:
                continue
            v = strip_for_hub(v)
            if v in ([], {}):
                continue
            out[k] = v
        return out
    if isinstance(o, list):
        return [strip_for_hub(v) for v in o]
    return o


@dataclass
class VerifyResult:
    errors: list[str] = field(default_factory=list)
    hub: Program | None = None
    app: Program | None = None
    hub_config_len: int = 0
    # (side, error) per build error, for pointing at the item in the editor
    issues: list[tuple[str, E.BuildError]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Project:
    def __init__(self, data: dict | None = None, path: Path | None = None):
        self.data: dict = data if data is not None else {"hardware": DEFAULT_HARDWARE, "hub": {}, "sockets": [], "app": {}}
        self.path = path

    # ---------------------------------------------------------------- file
    @classmethod
    def load(cls, path: str | Path) -> Project:
        p = Path(path)
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("project file must hold a JSON object")
        return cls(data, p)

    def save(self, path: str | Path | None = None) -> None:
        p = Path(path) if path else self.path
        if p is None:
            raise ValueError("no file name")
        p.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.path = p

    # ---------------------------------------------------------------- parts
    @property
    def hardware(self) -> str:
        return self.data.get("hardware") or DEFAULT_HARDWARE

    @hardware.setter
    def hardware(self, value: str) -> None:
        self.data["hardware"] = value

    @property
    def hub(self) -> dict:
        return self.data.get("hub") or {}

    @property
    def app(self) -> dict:
        return self.data.get("app") or {}

    @property
    def sockets(self) -> list:
        s = self.data.get("sockets")
        return s if isinstance(s, list) else []

    # ---------------------------------------------------------------- generate
    def hub_components(self) -> tuple[list, list[int | None]]:
        """The hub components in firmware (slot) order, with placeholders, and for
        each the index of the project component it is (None = placeholder)."""
        comps = self.hub.get("components")
        return hub_layout(load_hardware(self.hardware), comps if isinstance(comps, list) else [])

    def hub_config(self) -> dict:
        hub = self.hub
        comps, _ = self.hub_components()
        return strip_for_hub({
            "globals": hub.get("globals"),
            "components": comps,
            "sockets": self.sockets,
            "functions": hub.get("functions"),
        })

    def generate(self) -> str:
        """The minified hub config string sent by Load."""
        return json.dumps(self.hub_config(), separators=(",", ":"), ensure_ascii=True)

    # ---------------------------------------------------------------- verify
    def build_hub(self) -> Program:
        cfg = self.hub_config()
        return build_side(SIDE_HUB, cfg, cfg.get("sockets", []))

    def build_app(self) -> Program:
        return build_side(SIDE_APP, self.app, self.sockets, app_factory)

    def verify(self) -> VerifyResult:
        res = VerifyResult()
        if self.hardware not in HARDWARE_TYPES:
            res.errors.append(f"unknown hardware {self.hardware!r}")
        comps = self.hub.get("components")
        for i, detail in slot_errors(load_hardware(self.hardware), comps if isinstance(comps, list) else []):
            exc = E.BuildError(E.NO_HW_SLOT, detail, E.SECTION_COMPONENTS, i)
            res.errors.append(exc.describe("hub") + self.locate(SIDE_HUB, exc))
            res.issues.append((SIDE_HUB, exc))
        try:
            res.hub = self.build_hub()
        except E.BuildError as exc:
            if exc.section in (E.SECTION_COMPONENTS, E.SECTION_EVENTS):
                _, src = self.hub_components()       # generated index -> project index
                if 0 <= exc.index < len(src) and src[exc.index] is not None:
                    exc.index = src[exc.index]
            res.errors.append(exc.describe("hub") + self.locate(SIDE_HUB, exc))
            res.issues.append((SIDE_HUB, exc))
        try:
            res.app = self.build_app()
        except E.BuildError as exc:
            res.errors.append(exc.describe("app") + self.locate(SIDE_APP, exc))
            res.issues.append((SIDE_APP, exc))
        res.hub_config_len = len(self.generate())
        if res.hub_config_len > HUB_LIMITS["config_len"]:
            res.errors.append(f"hub config is {res.hub_config_len} chars, the hub takes at most "
                              f"{HUB_LIMITS['config_len']} ({E.ERROR_NAMES[E.BUFFER_OVERFLOW]})")
        return res

    def locate(self, side: str, exc: E.BuildError) -> str:
        """' (name)' of the item an error refers to, when it has a #name."""
        section = self.hub if side == SIDE_HUB else self.app
        items = {
            E.SECTION_COMPONENTS: section.get("components"),
            E.SECTION_EVENTS: section.get("components"),
            E.SECTION_FUNCTIONS: section.get("functions"),
            E.SECTION_SOCKETS: self.sockets,
            E.SECTION_BINDINGS: self.sockets,
        }.get(exc.section)
        if isinstance(items, list) and 0 <= exc.index < len(items) and isinstance(items[exc.index], dict):
            it = items[exc.index]
            name = it.get("#name") or it.get("protocol") or (
                f"{it.get('type')} {it.get('id')}" if it.get("type") else None)
            if name:
                return f" [{name}]"
        return ""
