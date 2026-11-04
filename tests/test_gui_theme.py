"""The Light / Dark themes: switching from View > Theme, the saved choice, and that
cards and other widgets take their colours from the theme's stylesheet."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, QSettings  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from brixbot_app.gui import theme  # noqa: E402
from brixbot_app.gui.cards import Card  # noqa: E402
from brixbot_app.gui.edit.fields import ValueEdit  # noqa: E402
from brixbot_app.gui.main_window import EXAMPLES, MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    # A theme switch repolishes every widget in the process: first delete the windows
    # earlier tests left behind (thousands of widgets), or each switch takes seconds.
    for tl in qapp.topLevelWidgets():
        tl.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(theme, "_settings", lambda: QSettings(ini, QSettings.Format.IniFormat))
    w = MainWindow(str(EXAMPLES / "demo_project.json"))
    w.ask = lambda *a: None
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    theme.apply(theme.LIGHT_NAME)        # other tests expect the default


def _centre_colour(w) -> QColor:
    img = w.grab().toImage()
    return img.pixelColor(img.width() // 2, img.height() - 3)


def test_menu_switches_and_saves_theme(win, qapp):
    assert theme.current() is theme.LIGHT
    assert qapp.styleSheet() == theme.LIGHT.stylesheet
    win.theme_actions["dark"].trigger()
    assert theme.current() is theme.DARK
    assert qapp.styleSheet() == theme.DARK.stylesheet
    assert theme.saved() == "dark"
    assert win.theme_actions["dark"].isChecked() and not win.theme_actions["light"].isChecked()
    win.theme_actions["light"].trigger()
    assert theme.current() is theme.LIGHT and theme.saved() == "light"


def test_cards_follow_theme(win, qapp):
    card = Card("c", "socket")
    card.resize(200, 60)
    card.show()
    qapp.processEvents()
    assert _centre_colour(card).name() == theme.LIGHT.cards["socket"][0]
    win.set_theme("dark")
    qapp.processEvents()
    assert _centre_colour(card).name() == theme.DARK.cards["socket"][0]
    card.close()


def test_states_are_properties(win):
    card = win.col_app.items[0]
    card.set_state(True, "")
    assert card.property("selected") is True
    card.set_state(False, "bad")
    assert card.property("error") is True and card.property("selected") is False
    ed = ValueEdit(1, "uint8")
    ed.setText("x")
    ed._check()
    assert not ed.is_valid() and ed.property("invalid") is True
    ed.setText("2")
    ed._check()
    assert ed.is_valid()


def test_unknown_saved_theme_gives_light(win):
    theme.save("purple")
    assert theme.saved() == "light"
