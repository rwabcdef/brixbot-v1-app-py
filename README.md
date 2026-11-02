# BrixBot PC app (Python)

Desktop app for the BrixBot hub: open a project, verify it, load the hub part
into the hub over USB serial, and run it with live app-side controls.

```
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m brixbot_app examples\demo_project.json
.venv\Scripts\python -m pytest
```

## Using it

| Button | What it does |
|---|---|
| **Select** | choose the hardware type and serial port, connect |
| **Detect** | scan all serial ports for a BrixBot hub and connect to the first one |
| **Verify** | check both sides of the project (same checks / error codes as the hub firmware) |
| **Generate** | show the minified hub config string (copy / save) |
| **Load** | send the hub config to the hub (`CONFG` B / D… / E) and report the build result |
| **Run / Stop** | start / stop the app side and the hub |

Editing is done in the project JSON file for now; **File > Reload** (Ctrl+R)
picks up changes. Tick **show frames** to see SerLink traffic in the log.

## Project file

One JSON file holds both sides (see `examples/demo_project.json`):

```json
{
  "hardware": "brixbot-uno-r4-v1",
  "hub":     { "globals": [], "components": [], "functions": [] },
  "sockets": [],
  "app":     { "globals": [], "components": [], "functions": [] }
}
```

* `hub` uses the hub firmware's config format (documented in the hub repo,
  `Brix_ConfigBuilder.hpp`). Hub components: `led`, `button`, `timer`.
* `sockets` are shared. In each schema, `functions` binds **hub** functions
  (run when the hub receives that message) and `appFunctions` binds **app**
  functions (run when the app receives it).
* `app` uses the same format for the app's own components: `dial`, `switch`,
  `button`, `led`, `display`, `log`, `timer` (commands and events in
  `brixbot_app/engine/specs.py`).
* Keys starting with `#` are comments (`#name` is used as a label).

**Generate** builds the hub string from `hub` + `sockets`, dropping comments,
`appFunctions`, nulls and empty arrays. The hub accepts at most 6143 chars.

## Layout

| Path | Contents |
|---|---|
| `brixbot_app/serlink/` | SerLink frame + link (Python port of the hub's SerLink) |
| `brixbot_app/engine/` | config builder / runtime shared by hub validation and the app side |
| `brixbot_app/hubio.py` | hub operations over the `CONFG` socket (detect, load, run, stop, status) |
| `brixbot_app/gui/` | PySide6 window, column cards, app component widgets |
| `examples/` | demo project |
| `tests/` | pytest suite (no hardware needed) |
