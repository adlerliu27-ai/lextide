"""Lazy desktop entry. Core CLI commands do not import or initialize Qt."""

from __future__ import annotations

import sys
from pathlib import Path


def launch(*, data_dir: Path | None = None) -> int:
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication

        from .controller import Controller
        from .i18n import configure_language, install_qt_translation
        from .preferences import default_data_dir, load_preferences
        from .window import MainWindow
    except ImportError:
        print(
            "桌面依赖未安装。请在项目目录运行：uv sync --frozen --extra desktop --group dev",
            file=sys.stderr,
        )
        return 2
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("Lextide")
    app.setOrganizationName("Lextide")
    root = data_dir or default_data_dir()
    configure_language(load_preferences(root)["language"])
    install_qt_translation(app)
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "assets/brand/lextide-app-icon-1024.png")))
    window = MainWindow()
    window.controller = Controller(window, root)
    window.show()
    return app.exec()
