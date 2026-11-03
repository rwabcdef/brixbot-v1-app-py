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

### Editing a project

* **File > New** starts an empty project; **Open / Save / Save As** work on the project JSON file.
* **Click** a card (component, socket, Globals) or a row (a function, an event handler) to edit it in
  the **Editor** dock on the right. The **Form** tab covers every part of the config format; the
  **JSON** tab edits the same item as text. **Apply** (Ctrl+Enter) commits, **Revert** drops the edit.
* **+ Add** above each column adds components, functions (basic or conditional), globals and sockets.
  **Right click** a card or row to duplicate, move or delete it.
* The hub's components come from its hardware file (`brixbot_app/hardware/<hardware>.json`): each led
  or button is a pin slot (leds D5, D4, D3, D2; buttons D7, D6, D8; 4 timers). **Hub > + Add >
  Component** lists the slots and greys out the ones in use; **View > Available components** (above
  the Hub column, or the View menu) shows every slot and what uses it. A component's slot is saved as
  `hwSlot` (app only); the editor's **Pin / slot** field changes it.
* A component's **Controls** section lists the commands a socket message can run (e.g. led on / off /
  flash). **Connect socket...** picks a socket and schema, or makes new ones, and creates a small
  function that runs the command, bound to the schema. **Disconnect** removes the binding (and the
  function, if nothing else uses it).
* Each event box in a component's editor has **+ new handler function**: it makes a function whose args
  match the event's values (e.g. switch change: `id:char, on:bool`), binds it to the event, and opens it.
* Each component card shows its event handlers and control connections in a yellow **Handlers** inset.
  Socket messages bound to other functions are listed under **Socket handlers**.
* A function binding's **Its args** is either *set each arg* or *no args transferred* (`"args": null`:
  the function runs with the arg values it already has).
* Renaming a function id, a component's type / id or a socket's protocol updates everything that
  refers to it. Moving or removing a global updates the functions that use it. Deleting an item that is
  still used asks first and lists the users.
* Every change is verified straight away: the status bar shows the result and the cards at fault are
  outlined red (hover for the message). Hover a function row to see it as pseudo code.
* **Edit > Undo / Redo** (Ctrl+Z / Ctrl+Y) cover every change; the title shows `*` while unsaved.
* Editing is locked while running. **File > Reload from disk** re-reads the file.

Tick **show frames** to see SerLink traffic in the log.

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
| `brixbot_app/editor/` | editing without Qt: undoable document, new-item templates, cross references |
| `brixbot_app/hubio.py` | hub operations over the `CONFG` socket (detect, load, run, stop, status) |
| `brixbot_app/gui/` | PySide6 window, column cards, app component widgets |
| `brixbot_app/gui/edit/` | the editor dock: form widgets and one editor per item kind |
| `examples/` | demo project |
| `tests/` | pytest suite (no hardware needed) |
