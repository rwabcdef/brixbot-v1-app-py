"""The app themes: Light (the original look) and Dark (the ControlHub app's look).

All GUI colours come from here. A theme is one application stylesheet, so
switching theme is just `apply()` - no widget needs rebuilding. Widgets pick
their themed look with an objectName and, for state, dynamic properties
(call `restyle()` after changing one):

    QFrame#card     kind = component | socket | handler | function | global;
                    selected / error = True
    QLabel#row      kind (of the card or section it is in); selected / error = True
    QFrame#section  kind
    QFrame#cardWidget kind   (a card row holding a widget, e.g. a live control)
    QFrame#column, QFrame#listrow, QLabel#listrowTitle, QLabel#hint, QLabel#emptyHint
    QLabel#verify   state = ok | error
    QLabel#lamp     (an LED view's lamp; a lit lamp sets its own background)
    QLineEdit       invalid = True

Things a stylesheet can't reach (the log's HTML colours, a lit lamp) read
`current()`.

The Dark theme's base stylesheet is copied from the ControlHub app
(ControlHub_App_V1_py/controlhub/ui/theme.py, itself the Electron app's dark
MUI theme), plus rules for the widgets BrixBot uses that ControlHub does not
(menus, docks, group boxes, tables, the status bar). It is copied, not
imported: the two are separate projects.

The chosen theme is saved in QSettings ("BrixBot", "BrixBot app"), key "theme".
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget

LIGHT_NAME = "light"
DARK_NAME = "dark"
NAMES = (LIGHT_NAME, DARK_NAME)
TITLES = {LIGHT_NAME: "Light", DARK_NAME: "Dark"}
KINDS = ("component", "socket", "handler", "function", "global")
CARD_RADIUS = 6         # px, the corners of the column cards (all themes)


@dataclass(frozen=True)
class Theme:
    name: str
    cards: dict[str, tuple[str, str]]       # kind -> (background, border)
    card_text: str
    select: str                             # selected card outline
    row_select_bg: str
    error: str                              # error outline / text
    ok: str
    warn: str
    tx: str                                 # log: frames sent
    rx: str                                 # log: frames received
    column_border: str
    listrow_border: str
    listrow_title: str
    hint: str
    empty_hint: str
    invalid: str                            # QSS declarations for an invalid line edit
    lamp_off: str
    lamp_border: str
    base: str = ""                          # stylesheet before the themed roles

    def log_color(self, kind: str) -> str | None:
        return {"ok": self.ok, "error": self.error, "warn": self.warn, "tx": self.tx, "rx": self.rx}.get(kind)

    @property
    def stylesheet(self) -> str:
        return self.base + _roles(self)


def _roles(t: Theme) -> str:
    """The stylesheet rules for BrixBot's own widgets, in the theme's colours."""
    out = []
    for kind, (bg, border) in t.cards.items():
        out.append(f'QFrame#card[kind="{kind}"] {{ background: {bg}; border: 1px solid {border}; '
                   f'border-radius: {CARD_RADIUS}px; }}')
        out.append(f'QFrame#section[kind="{kind}"] {{ background: {bg}; border: 1px solid {border}; '
                   f'border-radius: 6px; }}')
        out.append(f'QLabel#row[kind="{kind}"], QFrame#cardWidget[kind="{kind}"] '
                   f'{{ border-top: 1px solid {border}; }}')
    out.append(f'QFrame#card[error="true"] {{ border: 2px solid {t.error}; }}')
    out.append(f'QFrame#card[selected="true"] {{ border: 2px solid {t.select}; }}')
    out.append(f"QFrame#card QLabel {{ color: {t.card_text}; background: transparent; }}")
    out.append("QFrame#card .QWidget, QFrame#card QFrame#cardWidget, QFrame#card QDial, QFrame#card QCheckBox "
               "{ background: transparent; }")
    out.append("QLabel#row { padding: 3px 6px; }")
    out.append(f'QLabel#row[selected="true"] {{ background: {t.row_select_bg}; }}')
    out.append(f'QLabel#row[error="true"] {{ border-left: 4px solid {t.error}; }}')
    out.append(f"QFrame#column {{ border: 1px solid {t.column_border}; border-radius: 14px; }}")
    out.append(f"QFrame#listrow {{ border: 1px solid {t.listrow_border}; border-radius: 4px; }}")
    out.append(f"QLabel#listrowTitle {{ color: {t.listrow_title}; font-size: 11px; }}")
    out.append(f"QLabel#hint {{ color: {t.hint}; }}")
    out.append(f"QLabel#emptyHint {{ color: {t.empty_hint}; padding: 8px; }}")
    out.append("QLabel#verify { padding: 0 8px; }")
    out.append(f'QLabel#verify[state="ok"] {{ color: {t.ok}; }}')
    out.append(f'QLabel#verify[state="error"] {{ color: {t.error}; }}')
    out.append(f"QLabel#lamp {{ background: {t.lamp_off}; border: 1px solid {t.lamp_border}; border-radius: 11px; }}")
    out.append(f'QLineEdit[invalid="true"] {{ {t.invalid} }}')
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ light (the original look)
LIGHT = Theme(
    name=LIGHT_NAME,
    cards={
        "component": ("#dae8fc", "#6c8ebf"),
        "socket": ("#e1d5e7", "#9673a6"),
        "handler": ("#fff2cc", "#d6b656"),
        "function": ("#d5e8d4", "#82b366"),
        "global": ("#f0f0f0", "#999999"),
    },
    card_text="#111",
    select="#1a5fd0",
    row_select_bg="rgba(26, 95, 208, 0.16)",
    error="#d03030",
    ok="#2a9d3a",
    warn="#c07000",
    tx="#3070c0",
    rx="#8040a0",
    column_border="#888",
    listrow_border="#c8c8c8",
    listrow_title="#555",
    hint="#777",
    empty_hint="#666",
    invalid="background: #ffdddd;",
    lamp_off="#555",
    lamp_border="#222",
)

# ------------------------------------------------------------------ dark (ControlHub)
BG = "#0e1116"
PAPER = "#161b22"
DIVIDER = "#30363d"
TEXT = "#e6edf3"
TEXT_2 = "#9aa4af"
TEXT_DISABLED = "#5c6570"
TRACK = "#262c35"
PRIMARY = "#4fc3f7"
PRIMARY_HOVER = "#81d4fa"
ON_PRIMARY = "#0b1a22"
SUCCESS = "#66bb6a"
WARNING = "#ffa726"
ERROR = "#f44336"
ERROR_TEXT = "#f88078"
HOVER = "#1d232b"
SELECTION = "#1f3a48"

FONT = '"Segoe UI", "Ubuntu", "Helvetica Neue", Arial, sans-serif'
MONO = '"Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace'

_CONTROLHUB = f"""
* {{ font-family: {FONT}; font-size: 13px; }}
QWidget {{ background: {BG}; color: {TEXT}; }}
QWidget:disabled {{ color: {TEXT_DISABLED}; }}

QPushButton {{ background: {PRIMARY}; color: {ON_PRIMARY}; border: 1px solid {PRIMARY}; border-radius: 4px;
  padding: 5px 12px; font-weight: 600; }}
QPushButton:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
QPushButton:disabled {{ background: #2a3038; border-color: #2a3038; color: {TEXT_DISABLED}; }}
QToolButton {{ background: transparent; border: none; border-radius: 4px; padding: 4px; color: {TEXT_2}; }}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton:checked {{ color: {PRIMARY}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ background: transparent; border: 1px solid #3d444d;
  border-radius: 4px; padding: 5px 8px; selection-background-color: {SELECTION}; }}
QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover {{ border-color: {TEXT_2}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {PRIMARY}; }}
QLineEdit:disabled, QComboBox:disabled {{ border-color: {DIVIDER}; color: {TEXT_DISABLED}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {PAPER}; border: 1px solid {DIVIDER}; selection-background-color: {SELECTION};
  outline: none; }}

QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; border: 2px solid {TEXT_2};
  background: transparent; }}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked {{ background: {PRIMARY}; border-color: {PRIMARY}; }}
QRadioButton::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
  stop:0 {PRIMARY}, stop:0.45 {PRIMARY}, stop:0.55 transparent); border-color: {PRIMARY}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {PRIMARY}; }}

QSlider::groove:horizontal {{ height: 4px; background: {TRACK}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {PRIMARY}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {TEXT_2}; padding: 9px 14px; border: none;
  border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {PRIMARY}; border-bottom: 2px solid {PRIMARY}; }}
QTabBar::tab:disabled {{ color: {TEXT_DISABLED}; }}

QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 5px 6px; border-radius: 4px; }}
QListWidget::item:hover {{ background: {HOVER}; }}
QListWidget::item:selected {{ background: {SELECTION}; color: {TEXT}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3a414a; border-radius: 4px; min-height: 24px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #3a414a; border-radius: 4px; min-width: 24px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: #2b313a; color: {TEXT}; border: none; padding: 4px 6px; font-size: 12px; }}
QSplitter::handle {{ background: {DIVIDER}; }}
"""

# widgets BrixBot uses that the ControlHub stylesheet does not cover
_BRIXBOT_EXTRA = f"""
QPushButton:checkable {{ background: transparent; color: {PRIMARY}; border: 1px solid #2f5d73; }}
QPushButton:checkable:hover {{ background: #14262f; border-color: {PRIMARY}; }}
QPushButton:checkable:checked {{ background: {PRIMARY}; color: {ON_PRIMARY}; border-color: {PRIMARY}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QTextEdit, QTextBrowser {{ background: transparent; border: 1px solid #3d444d; border-radius: 4px;
  selection-background-color: {SELECTION}; }}
QPlainTextEdit:read-only {{ border-color: {DIVIDER}; }}

QMainWindow::separator {{ background: {DIVIDER}; width: 1px; height: 1px; }}
QMenuBar {{ background: {PAPER}; border-bottom: 1px solid {DIVIDER}; }}
QMenuBar::item {{ background: transparent; padding: 5px 10px; }}
QMenuBar::item:selected {{ background: {HOVER}; }}
QMenu {{ background: {PAPER}; border: 1px solid {DIVIDER}; padding: 4px 0; }}
QMenu::item {{ background: transparent; padding: 5px 28px 5px 24px; }}
QMenu::item:selected {{ background: {SELECTION}; }}
QMenu::item:disabled {{ color: {TEXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {DIVIDER}; margin: 4px 8px; }}
QMenu::indicator {{ width: 8px; height: 8px; left: 8px; border-radius: 4px; }}
QMenu::indicator:checked {{ background: {PRIMARY}; }}

QDockWidget {{ color: {TEXT_2}; }}
QDockWidget::title {{ background: {PAPER}; padding: 6px 8px; border-bottom: 1px solid {DIVIDER}; }}

QGroupBox {{ border: 1px solid {DIVIDER}; border-radius: 6px; margin-top: 10px; padding: 8px 4px 4px 4px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEXT_2}; font-weight: 600; }}
QGroupBox QCheckBox, QGroupBox QLabel {{ background: transparent; }}

QTabWidget::pane {{ border: 1px solid {DIVIDER}; border-radius: 4px; top: -1px; }}

QTableView {{ background: transparent; border: 1px solid {DIVIDER}; gridline-color: {DIVIDER};
  selection-background-color: {SELECTION}; selection-color: {TEXT}; }}
QHeaderView::section {{ background: {PAPER}; color: {TEXT_2}; border: none; border-right: 1px solid {DIVIDER};
  border-bottom: 1px solid {DIVIDER}; padding: 4px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {PAPER}; border: none; }}

QStatusBar {{ background: {PAPER}; border-top: 1px solid {DIVIDER}; color: {TEXT_2}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ background: transparent; }}
"""

DARK = Theme(
    name=DARK_NAME,
    cards={
        "component": ("#16273a", "#4f86c6"),
        "socket": ("#241b2e", "#a582c0"),
        "handler": ("#2e2812", "#d6b656"),
        "function": ("#18291a", "#82b366"),
        "global": ("#1d232b", "#5c6570"),
    },
    card_text=TEXT,
    select=PRIMARY,
    row_select_bg="rgba(79, 195, 247, 0.18)",
    error=ERROR,
    ok=SUCCESS,
    warn=WARNING,
    tx=PRIMARY,
    rx="#ce93d8",
    column_border="#3d444d",
    listrow_border=DIVIDER,
    listrow_title=TEXT_2,
    hint=TEXT_2,
    empty_hint=TEXT_2,
    invalid=f"border: 1px solid {ERROR}; background: #2a1414;",
    lamp_off="#3a4049",
    lamp_border=DIVIDER,
    base=_CONTROLHUB + _BRIXBOT_EXTRA,
)

THEMES = {LIGHT_NAME: LIGHT, DARK_NAME: DARK}
_current: Theme = LIGHT


def current() -> Theme:
    return _current


def apply(name: str, app: QApplication | None = None) -> Theme:
    """Make `name` the current theme and set its stylesheet on the app (an unknown
    name gives Light)."""
    global _current
    _current = THEMES.get(name, LIGHT)
    app = app or QApplication.instance()
    if app is not None:
        app.setStyleSheet(_current.stylesheet)
    return _current


def restyle(w: QWidget) -> None:
    """Re-apply the stylesheet after changing a dynamic property."""
    w.style().unpolish(w)
    w.style().polish(w)


def set_state(w: QWidget, **props) -> None:
    """Set dynamic properties and restyle the widget, if any changed."""
    changed = False
    for k, v in props.items():
        if w.property(k) != v:
            w.setProperty(k, v)
            changed = True
    if changed:
        restyle(w)


# ------------------------------------------------------------------ the saved choice
def _settings() -> QSettings:
    return QSettings("BrixBot", "BrixBot app")


def saved() -> str:
    v = _settings().value("theme", LIGHT_NAME)
    return v if v in THEMES else LIGHT_NAME


def save(name: str) -> None:
    _settings().setValue("theme", name)
