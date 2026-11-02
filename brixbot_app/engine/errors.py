"""Config / run error codes and sections - same values as the hub (Brix_Config.hpp)."""

from __future__ import annotations

OK = 0
JSON = 1
NO_MEMORY = 2
UNSUPPORTED_COMPONENT = 3
NO_HW_SLOT = 4
DUPLICATE_ID = 5
BAD_INDEX = 6
BAD_TYPE = 7
MISSING_FIELD = 8
UNKNOWN_FUNCTION = 9
UNKNOWN_SOCKET = 10
BAD_COMMAND = 11
TOO_MANY = 12
BUFFER_OVERFLOW = 13
NOT_CONFIGURED = 14
BAD_SCHEMA = 15
BAD_OPERATOR = 16
BAD_CONDITION_BLOCK = 17
UNKNOWN_COMPONENT = 18
BAD_VALUE = 19
RUNTIME = 20

ERROR_NAMES = {
    OK: "ok",
    JSON: "malformed JSON",
    NO_MEMORY: "out of memory (hub arena / JSON parser)",
    UNSUPPORTED_COMPONENT: "component type not supported",
    NO_HW_SLOT: "more components of a type than hardware slots",
    DUPLICATE_ID: "duplicate id",
    BAD_INDEX: "index out of range",
    BAD_TYPE: "unknown variable type",
    MISSING_FIELD: "required field missing or invalid",
    UNKNOWN_FUNCTION: "unknown functionId",
    UNKNOWN_SOCKET: "unknown socket protocol",
    BAD_COMMAND: "unknown commandId or wrong number of args",
    TOO_MANY: "too many items",
    BUFFER_OVERFLOW: "config too long for the hub",
    NOT_CONFIGURED: "not configured",
    BAD_SCHEMA: "invalid socket schema",
    BAD_OPERATOR: "unknown operator / connector",
    BAD_CONDITION_BLOCK: "invalid condition block",
    UNKNOWN_COMPONENT: "command refers to an undeclared component",
    BAD_VALUE: "value does not fit its type",
    RUNTIME: "run time error",
}

SECTION_NONE = "-"
SECTION_JSON = "J"
SECTION_GLOBALS = "G"
SECTION_COMPONENTS = "C"
SECTION_SOCKETS = "S"
SECTION_FUNCTIONS = "F"
SECTION_BINDINGS = "B"
SECTION_EVENTS = "E"

SECTION_NAMES = {
    SECTION_NONE: "",
    SECTION_JSON: "json",
    SECTION_GLOBALS: "globals",
    SECTION_COMPONENTS: "components",
    SECTION_SOCKETS: "sockets",
    SECTION_FUNCTIONS: "functions",
    SECTION_BINDINGS: "sockets (function bindings)",
    SECTION_EVENTS: "components (event bindings)",
}


class BuildError(Exception):
    """A config error, with the hub's code / section / index plus a readable detail."""

    def __init__(self, code: int, detail: str = "", section: str = SECTION_NONE, index: int = 0):
        self.code = code
        self.detail = detail
        self.section = section
        self.index = index
        super().__init__(self.describe())

    def describe(self, side: str = "") -> str:
        where = SECTION_NAMES.get(self.section, self.section)
        loc = f"{where}[{self.index}]" if where else ""
        parts = [p for p in (side, loc) if p]
        head = " ".join(parts)
        msg = ERROR_NAMES.get(self.code, f"error {self.code}")
        detail = f" - {self.detail}" if self.detail else ""
        return f"{head + ': ' if head else ''}{msg} ({self.code}){detail}"
