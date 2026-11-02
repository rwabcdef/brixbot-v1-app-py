"""Builds the main window offscreen with the demo project and drives the app side."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from brixbot_app.gui.main_window import EXAMPLES, MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_window_runs_demo_app_side(qapp):
    w = MainWindow(str(EXAMPLES / "demo_project.json"))
    try:
        assert w.app_program is not None
        assert w.action_verify(quiet=True)
        w.action_run()                      # no hub connected: app side only
        assert w.app_program.running
        w.app_program.handle_frame("BUTT0", "AR00123")
        qapp.processEvents()
        assert w.app_program.resolve("display", "T").text == "123"
        w.action_stop()
        assert not w.app_program.running
    finally:
        w.close()
