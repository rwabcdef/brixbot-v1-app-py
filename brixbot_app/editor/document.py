"""Document - an editable project with undo / redo and a dirty flag.

Every change goes through apply(label, mutate): the project data is
snapshotted first, so undo is a plain restore. Items are addressed by paths
into the project JSON, e.g. ("hub", "components", 2) or ("sockets", 0).

No Qt here: observers are plain callables, called after every change.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from ..engine.project import Project

Path = tuple


def get_at(data, path: Path):
    """Value at path, or None when any step is missing."""
    o = data
    for p in path:
        try:
            o = o[p]
        except (KeyError, IndexError, TypeError):
            return None
    return o


def list_at(data: dict, path: Path) -> list:
    """The list at path, created (with any missing dicts on the way) when absent."""
    o = data
    for i, p in enumerate(path):
        last = i == len(path) - 1
        nxt = o.get(p) if isinstance(o, dict) else o[p]
        if not isinstance(nxt, (list if last else (dict, list))):
            nxt = [] if last else {}
            o[p] = nxt
        o = nxt
    return o


def normalize(data: dict) -> dict:
    """Make sure the top level parts exist (an edited project always has them)."""
    for side in ("hub", "app"):
        if not isinstance(data.get(side), dict):
            data[side] = {}
    if not isinstance(data.get("sockets"), list):
        data["sockets"] = []
    return data


class Document:
    MAX_UNDO = 200

    def __init__(self, project: Project | None = None):
        self.project = project or Project()
        normalize(self.project.data)
        self._undo: list[tuple[str, dict, int]] = []
        self._redo: list[tuple[str, dict, int]] = []
        self._rev = 0
        self._next_rev = 1
        self._saved_rev = 0
        self.observers: list[Callable[[], None]] = []

    # ---------------------------------------------------------------- state
    @property
    def data(self) -> dict:
        return self.project.data

    @property
    def dirty(self) -> bool:
        return self._rev != self._saved_rev

    def mark_saved(self) -> None:
        self._saved_rev = self._rev

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def _notify(self) -> None:
        for fn in list(self.observers):
            fn()

    # ---------------------------------------------------------------- change
    def apply(self, label: str, mutate: Callable[[dict], Any]) -> Any:
        """Run mutate(data) as one undoable step. No change -> no undo entry."""
        before = copy.deepcopy(self.data)
        try:
            result = mutate(self.data)
        except Exception:
            self.project.data = before
            raise
        if self.data == before:
            return result
        self._undo.append((label, before, self._rev))
        del self._undo[:-self.MAX_UNDO]
        self._redo.clear()
        self._rev = self._next_rev
        self._next_rev += 1
        self._notify()
        return result

    def undo(self) -> str:
        if not self._undo:
            return ""
        label, prev, rev = self._undo.pop()
        self._redo.append((label, copy.deepcopy(self.data), self._rev))
        self.project.data = prev
        self._rev = rev
        self._notify()
        return label

    def redo(self) -> str:
        if not self._redo:
            return ""
        label, nxt, rev = self._redo.pop()
        self._undo.append((label, copy.deepcopy(self.data), self._rev))
        self.project.data = nxt
        self._rev = rev
        self._notify()
        return label

    # ---------------------------------------------------------------- path helpers
    def get(self, path: Path):
        return get_at(self.data, path)

    def set(self, path: Path, value, label: str = "edit") -> None:
        def m(d):
            parent = get_at(d, path[:-1])
            parent[path[-1]] = copy.deepcopy(value)
        self.apply(label, m)

    def insert(self, list_path: Path, value, index: int | None = None, label: str = "add") -> Path:
        """Insert into the list at list_path (created if missing); returns the new item's path."""
        def m(d):
            lst = list_at(d, list_path)
            i = len(lst) if index is None else max(0, min(index, len(lst)))
            lst.insert(i, copy.deepcopy(value))
            return (*list_path, i)
        return self.apply(label, m)

    def remove(self, path: Path, label: str = "delete") -> None:
        def m(d):
            del get_at(d, path[:-1])[path[-1]]
        self.apply(label, m)

    def move(self, path: Path, delta: int, label: str = "move") -> Path:
        """Move a list item by delta places; returns its new path."""
        lst = get_at(self.data, path[:-1])
        i = path[-1]
        j = max(0, min(len(lst) - 1, i + delta))
        if i == j:
            return path

        def m(d):
            l2 = get_at(d, path[:-1])
            l2.insert(j, l2.pop(i))
            return (*path[:-1], j)
        return self.apply(label, m)
