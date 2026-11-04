# CLAUDE.md

## This repo follows the hub repo

This is the BrixBot v1 PC app, written in Python with PySide6. It is one of two git repositories that sit side by side:

| Repo | Path | What |
|---|---|---|
| hub (**master / parent**) | `BrixBot/v1/hub` (`../hub`) | Arduino UNO R4 firmware |
| **app_py** (this repo) | `BrixBot/v1/app_py` | PC app |

The parent folder `v1/` is in no repo, so nothing that needs keeping belongs there.

The hub repo owns these definitions; this app follows them:
- the config format
- the `CONFG` load protocol
- component commands and events
- the SerLink frame format
- error codes

`../hub/CLAUDE.md` is the master reference for all of them.

Work in this repo when a change is **app-only**. If a change touches anything shared (see "Files that mirror the hub"), make it in a session started in `../hub`: change the firmware first, then this repo. Open this repo there with `/add-dir`.

An Electron version of the app is planned. Keep the engine and the hub I/O free of Qt, so they stay a clear reference for porting.

## Setup, run, test

```
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m brixbot_app examples\demo_project.json
.venv\Scripts\python -m pytest
```

pytest needs no hardware. It covers frames and the link (against a fake hub), Variable parity with the firmware, builder error codes, the app runtime, the editor model, hardware slots and controls, form round trips of every demo item, and GUI editing (including building a project from File > New).

**Testing GUI code without a display.** Set `QT_QPA_PLATFORM=offscreen` and `QT_QPA_FONTDIR=C:/Windows/Fonts`; without the font directory, all text renders as boxes. Use `MainWindow.grab().save(...)` for a screenshot.

**Hardware.** A real hub is usually on COM6, running the firmware from `../hub`. To check end to end, drive `MainWindow` offscreen in this order:
1. `connect_port`
2. `action_load`
3. `action_run`
4. use the component models
5. `action_stop`

Pump the Qt events until `busy` is false after each step.

## Layout

| Path | Role |
|---|---|
| `brixbot_app/serlink/` | Python port of the hub's SerLink. `frame.py` handles the wire format. `link.py` runs one I/O thread with one outstanding 'T' frame at a time, matches acks on protocol and roll code, acks incoming 'T' frames, and spaces writes 30 ms apart. |
| `brixbot_app/engine/` | No Qt. It is one builder and runtime used for both sides. |
| `engine/specs.py` | Command and event tables: `HUB_SPECS`, `HUB_LIMITS`, `APP_SPECS`. |
| `engine/builder.py` | Port of `Brix_ConfigBuilder.cpp`, with the same passes, error codes, sections and indices. |
| `engine/runtime.py` | Command, Statement, Condition, Function, FunctionBinding and Program. |
| `engine/variable.py`, `engine/sockets.py` | Typed values and the payload codec, matching the firmware exactly. |
| `engine/app_components.py` | App component models: dial, switch, button, led, display, log and timer. |
| `engine/project.py` | Project file: load and save, `generate()` (the hub string) and `verify()`. |
| `engine/hardware.py`, `brixbot_app/hardware/*.json` | The hub's available components: one file per hardware type, listing each type's slots (pins) in the order the firmware gives them out. `hub_layout()` orders the hub components by slot and pads unused lower pin slots with placeholders. |
| `brixbot_app/editor/` | No Qt. `document.py` is the project with undo / redo and a dirty flag; every change goes through `Document.apply()`. `templates.py` makes new items with free ids. `controls.py` connects a socket schema to a component command (it makes a `#control` function and the binding) and finds those connections. `refs.py` finds and renames users of functions, components, sockets and globals, and renders pseudo code. |
| `brixbot_app/hubio.py` | `CONFG` operations: `query`, `load`, `run`, `stop`, `detect`. They block, so call them from worker threads only. |
| `brixbot_app/gui/` | `main_window.py`, `cards.py` (clickable column cards), `dashboard.py` (the run dashboard of app components), `app_widgets.py` (views of the models), `theme.py` (the Light and Dark themes) and `bridge.py`. |
| `brixbot_app/gui/edit/` | The Editor dock. `fields.py` holds the form widgets, `items.py` one editor per item kind (component, function, socket, globals) and `panel.py` the dock with Form / JSON tabs. |
| `examples/demo_project.json` | Demo project. Its hub part equals `../hub/examples/example_config.json`. |

## Rules

- **Threading.** Engine, model and widget code runs on the GUI thread only.
  - The serial link thread and the hubio workers hand results back with `Bridge.post()` or `Bridge.run_in_thread()`.
  - Link listeners run on the link thread, so they must only post.
- **Models and views.**
  - Models never import Qt. Views call `user_*` methods and observe the model through `add_observer`.
  - Commands never raise events; only user input and time do. This stops feedback loops.
- **App runs before the hub.** Run starts the app side before sending `R` to the hub, because the hub can send messages as soon as it runs.
- **Editing.**
  - Items are addressed by paths into the project JSON, e.g. `("hub", "functions", 2)` or `("sockets", 0)`.
  - Every editor is built from an item's JSON and returns it from `value()`, keeping keys it does not manage. A form round trip must not change an item; `tests/test_gui_editor.py` checks this for every demo item, so extend the demo when adding a format feature.
  - When the config format or `HUB_SPECS` changes, update the matching editor in `gui/edit/items.py` too.
  - Editing is locked while running. Message boxes go through `MainWindow.ask()`, which tests replace.
- **Themes.** View > Theme picks Light (the original look) or Dark (the ControlHub app's look); the choice is saved in QSettings.
  - Each theme is one app stylesheet in `gui/theme.py`, so switching theme rebuilds nothing.
  - Don't hard-code colours in widgets. Give the widget an objectName, and dynamic properties for its state (`theme.set_state()`), and add the rule to `theme._roles()`. Code a stylesheet can't reach reads `theme.current()`.
  - Font-only stylesheets on widgets are fine.
- **Errors.** A config error is a `BuildError(code, detail, section, index)`. Codes match the hub's `Err` codes. `Project.verify()` adds the item's `#name` to the message.

## Project file

`hardware`, `hub`, `sockets` and `app`.
- `hub` and `app` each hold `globals`, `components` and `functions`, in the hub's config format; the format is documented in `../hub/brixbot_hub/Brix_ConfigBuilder.hpp`.
- In each socket schema, `functions` binds **hub** functions (run when the hub receives that schema) and `appFunctions` binds **app** functions.
- Keys starting with `#` are comments, and `#name` is used as a label.
- Every socket schema needs a `#name` (an app rule; verify reports a missing one). `Project.load` migrates older files, where a schema's `#comment` becomes its `#name`.
- A hub component's `hwSlot` (app only) is its slot index within its type in the hardware file. Components without one take the first free slots, in file order.
- `generate()` builds the hub string from `hub` plus `sockets`. It puts the hub components in slot order with placeholders, strips comments, app-only keys (`appFunctions`, `hwSlot`), nulls and empty arrays, and the hub takes at most 6143 chars. Verify maps component error indices back to the project's own list.

Projects are edited in the GUI (Editor dock) or as JSON; File > Reload from disk picks up outside changes.

## Files that mirror the hub

Change these only to follow a firmware change made first in `../hub`:

| App file | Mirrors (in `../hub/brixbot_hub`) |
|---|---|
| `engine/specs.py` `HUB_SPECS`, `HUB_LIMITS` | `Brix_Led` / `Brix_Button` / `Brix_Timer` `COMMANDS` / `EVENTS`, `Brix_HwConfig` slot counts, `Brix_Config.hpp` limits |
| `engine/errors.py` | `Brix_Config.hpp` `Err` / `Section` |
| `engine/builder.py` | `Brix_ConfigBuilder.cpp` |
| `engine/variable.py`, `engine/sockets.py` | `Brix_Variable.cpp`, `Brix_LinkSocket.cpp` |
| `hubio.py` | `Brix_ConfigLoader.*` (CONFG commands and reply formats) |
| `serlink/frame.py` | `Frame.hpp` / `Frame.cpp` |
| `hardware/*.json` | `Brix_HwConfig.cpp` (led / button pins, timer count, the order they are given out) |

App-only changes need no hub change:
- app components (`APP_SPECS`, `app_components.py`, `app_widgets.py`)
- the GUI
- the project file's `app` and `appFunctions` parts

## Conventions

- Python 3.11 or later, with type hints and `from __future__ import annotations`.
- Each module's docstring explains its purpose and constraints.
- Commits go to this repo only. A change across both repos needs a commit in each.
