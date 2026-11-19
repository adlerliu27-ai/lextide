"""Studio output location and audio filter each have a full row."""

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lextide.desktop.i18n import configure_language  # noqa: E402
from lextide.desktop.window import MainWindow  # noqa: E402


@pytest.mark.parametrize(("language", "theme"), [("en", "light"), ("zh_CN", "dark")])
def test_filter_label_and_output_path_do_not_share_a_row(language, theme):
    app = QApplication.instance() or QApplication([])
    configure_language(language)
    w = MainWindow()
    try:
        w.resize(1180, 832)
        w.set_theme(theme)
        w.show()
        w.show_page("Studio")
        app.processEvents()
        checkbox = w.filter_check
        assert checkbox.width() >= checkbox.sizeHint().width()
        bottom = checkbox.mapTo(w, QPoint(0, checkbox.height())).y()
        assert bottom <= w.output_dir_edit.mapTo(w, QPoint(0, 0)).y()
    finally:
        w.close_allowed = True
        w.close()
        w.deleteLater()
        configure_language("en")
