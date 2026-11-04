"""python -m brixbot_app [project.json]"""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from .gui import theme
    from .gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("BrixBot")
    theme.apply(theme.saved(), app)
    win = MainWindow(sys.argv[1] if len(sys.argv) > 1 else None)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
