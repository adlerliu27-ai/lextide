"""Regressions for the isolated pre-Carbon Overview presentation."""

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QTableWidgetItem, QWidget

from lextide.desktop.i18n import configure_language, current_language, tr
from lextide.desktop.localized import retranslate_ui
from lextide.desktop.presentation.view_overview import OverviewView
from lextide.desktop.theme import ASSETS, DARK, LIGHT, apply_theme, brand_symbol
from lextide.desktop.window import MainWindow


@pytest.fixture
def gui():
    app = QApplication.instance() or QApplication([])
    language = current_language()
    configure_language("en", resource_dir=ASSETS.parent / "translations")
    yield app
    configure_language(language, resource_dir=ASSETS.parent / "translations")


@pytest.fixture
def overview(gui):
    page = OverviewView()
    yield page
    page.close()
    page.deleteLater()


def test_historic_controls_headers_and_brand(overview):
    texts = {label.text() for label in overview.findChildren(QLabel)}
    assert {
        "Your listening workspace",
        "WELCOME TO LEXTIDE",
        "Make every word\nworth listening to.",
        "Import a document",
        "Your local sessions",
        "Recent sessions",
    } <= texts
    assert len(overview.findChildren(QFrame, "Card")) == 2
    recent = overview.recent_table
    assert [recent.horizontalHeaderItem(i).text() for i in range(recent.columnCount())] == [
        "Session",
        "Status",
        "Engine",
    ]
    brand = overview.findChild(QLabel, "OverviewBrand")
    assert brand.width() == brand.height() == 142
    expected_mark = QLabel()
    expected_mark.setPixmap(brand_symbol(116))
    assert brand.pixmap().toImage() == expected_mark.pixmap().toImage()
    for button, caption in (
        (overview.overview_new_button, "Create a session"),
        (overview.overview_import_button, "Choose a file"),
        (overview.overview_library_button, "Open library"),
        (overview.view_library_button, "View library  →"),
    ):
        assert button.text() == caption
        clicked = QSignalSpy(button.clicked)
        button.click()
        assert clicked.count() == 1


@pytest.mark.parametrize("width", [980, 1073, 1180])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_layout_and_live_translation_keep_session_selection(gui, overview, width, dark):
    outside = QWidget()
    application_css = gui.styleSheet()
    outside_css = outside.styleSheet()
    overview.set_dark(dark)
    # Carbon's sidebar is 216 logical points wide. The view receives the remaining width.
    overview.resize(width - 216, 700)
    table = overview.recent_table
    table.setRowCount(1)
    item = QTableWidgetItem("A synthetic lesson")
    item.setData(Qt.ItemDataRole.UserRole, "sample-session")
    table.setItem(0, 0, item)
    table.selectRow(0)
    overview.overview_summary_label.setText(
        tr("Found {count} local tasks/sessions. Files are verified before playback.", count=1)
    )
    overview.show()
    for lang, title, action, headers in (
        (
            "en",
            "Make every word\nworth listening to.",
            "Choose a file",
            ["Session", "Status", "Engine"],
        ),
        ("zh_CN", "让每一段文字\n都值得聆听。", "选择文件", ["项目", "状态", "引擎"]),
        (
            "en",
            "Make every word\nworth listening to.",
            "Choose a file",
            ["Session", "Status", "Engine"],
        ),
    ):
        configure_language(lang, resource_dir=ASSETS.parent / "translations")
        retranslate_ui(overview)
        gui.processEvents()
        assert title in {label.text() for label in overview.findChildren(QLabel)}
        assert overview.overview_import_button.text() == action
        assert [table.horizontalHeaderItem(i).text() for i in range(3)] == headers
        assert table.selectedItems() == [item]
        assert item.data(Qt.ItemDataRole.UserRole) == "sample-session"
        assert "1" in overview.overview_summary_label.text()
        assert overview.horizontalScrollBar().maximum() == 0
        left, right = overview.findChildren(QFrame, "Card")
        assert left.y() == right.y() and left.geometry().right() < right.x()
        assert abs(left.width() - right.width()) <= 1
    assert gui.styleSheet() == application_css
    assert outside.styleSheet() == outside_css
    outside.deleteLater()


def test_recent_table_fits_content_without_a_blank_table(overview, gui):
    overview.resize(920, 700)
    overview.show()
    gui.processEvents()
    table = overview.recent_table
    assert table.isHidden() and overview.recent_empty_label.isVisible()
    table.setRowCount(1)
    gui.processEvents()
    assert table.isVisible() and overview.recent_empty_label.isHidden()
    one = table.height()
    table.setRowCount(2)
    gui.processEvents()
    assert one < table.height() < 207
    table.setRowCount(4)
    assert table.height() == 207
    table.setRowCount(0)
    assert table.isHidden() and overview.recent_empty_label.isVisible()


def test_status_dots_and_long_titles_work_across_themes_and_narrow_width(gui, overview):
    """Component-level check: MainWindow._fill_table, live dark toggle, keyboard & callbacks."""
    previous_palette = gui.palette()
    previous_stylesheet = gui.styleSheet()
    try:
        apply_theme("dark")
        overview.set_dark(True)
        overview.resize(980 - 216, 700)
        overview.show()
        rows = [
            {
                "title": "Long-Academic-English-Shadowing-Session-" * 4,
                "status": "completed",
                "engine": "edge-tts",
                "path": "/synthetic/session-1",
            }
        ]
        MainWindow._fill_table(overview.recent_table, rows, ("title", "status", "engine"))
        gui.processEvents()
        assert overview.horizontalScrollBar().maximum() == 0
        assert overview.recent_table.horizontalScrollBar().maximum() == 0

        def dot_center(table):
            image = table.item(0, 1).icon().pixmap(16, 16).toImage()
            return image.pixelColor(image.width() // 2, image.height() // 2)

        assert dot_center(overview.recent_table) == QColor(DARK["success"])
        overview.set_dark(False)
        assert dot_center(overview.recent_table) == QColor(LIGHT["success"])
        overview.set_dark(True)
        assert dot_center(overview.recent_table) == QColor(DARK["success"])

        # Synthetic callback doubles verify single-shot signal emission and keyboard activation.
        events = []
        overview.overview_new_button.clicked.connect(lambda: events.append("new"))
        overview.overview_library_button.clicked.connect(lambda: events.append("open_library"))
        overview.overview_import_button.clicked.connect(lambda: events.append("import"))
        overview.view_library_button.clicked.connect(lambda: events.append("view_library"))
        overview.recent_table.cellDoubleClicked.connect(
            lambda r, c: events.append(
                ("double", r, c, overview.recent_table.item(r, 0).data(Qt.ItemDataRole.UserRole))
            )
        )
        for control in (
            overview.overview_new_button,
            overview.overview_library_button,
            overview.overview_import_button,
            overview.view_library_button,
        ):
            control.setFocus()
            QTest.keyClick(control, Qt.Key.Key_Space)
        overview.ensureWidgetVisible(overview.recent_table)
        gui.processEvents()
        rect = overview.recent_table.visualItemRect(overview.recent_table.item(0, 0))
        viewport = overview.recent_table.viewport()
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=rect.center())
        QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, pos=rect.center())
        assert events == [
            "new",
            "open_library",
            "import",
            "view_library",
            ("double", 0, 0, rows[0]),
        ]
    finally:
        gui.setPalette(previous_palette)
        gui.setStyleSheet(previous_stylesheet)


def test_integrated_mainwindow_and_controller_overview_wiring(gui, tmp_path, monkeypatch):
    """End-to-end host check: MainWindow + Controller wiring, 3 vs 5 cols, theme & i18n."""
    from PySide6.QtCore import QCoreApplication, QEvent

    from lextide.desktop.controller import Controller

    previous_palette = gui.palette()
    previous_stylesheet = gui.styleSheet()
    monkeypatch.setattr(Controller, "refresh_history", lambda *_: None)
    monkeypatch.setattr(Controller, "refresh_engines", lambda *_: None)
    window = MainWindow()
    control = Controller(window, tmp_path)
    try:
        window.resize(1180, 804)
        window.show()
        gui.processEvents()

        # 1. Mounted on stack page 0 ("Overview") with idle 56 pt player.
        assert window.pages["Overview"] is window.overview_view
        assert window.page_stack.currentWidget() is window.overview_view
        assert window.player_bar.height() == 56
        assert not hasattr(window, "environment_voice")
        assert not hasattr(window, "environment_folder")
        assert (
            window.overview_summary_label.text()
            == "No local sessions yet. Import text to create your first listening session."
        )
        assert window.recent_table.isHidden() and window.recent_empty_label.isVisible()

        # 2. Real Controller actions for all four Overview entry points (single-shot).
        window.overview_new_button.click()
        assert window.page_stack.currentWidget() is window.pages["Studio"]
        assert window.title_edit.text() == "Untitled session"

        window.show_page("Overview")
        window.overview_library_button.click()
        assert window.page_stack.currentWidget() is window.pages["Library"]

        window.show_page("Overview")
        window.view_library_button.click()
        assert window.page_stack.currentWidget() is window.pages["Library"]

        window.show_page("Overview")
        dialog_calls = []
        monkeypatch.setattr(
            "lextide.desktop.controller.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (dialog_calls.append("open") or "", ""),
        )
        window.overview_import_button.click()
        assert dialog_calls == ["open"]

        # 3. 3-col Overview vs 5-col Library isolation, real count, double-click, theme & i18n.
        verified_calls = []
        monkeypatch.setattr(
            control,
            "_verify",
            lambda path, title="", generation_signature=None: verified_calls.append((path, title)),
        )
        control._history_loaded(
            {
                "items": [
                    {
                        "path": str(tmp_path / "job-1"),
                        "title": "English practice",
                        "status": "completed",
                        "engine": "edge-tts",
                        "duration_seconds": 42,
                        "created_at": "2026-01-01",
                        "kind": "job",
                    },
                    {
                        "path": str(tmp_path / "job-2"),
                        "title": "Second reading",
                        "status": "completed",
                        "engine": "kokoro",
                        "duration_seconds": 95,
                        "created_at": "2026-01-02",
                        "kind": "job",
                    },
                ]
            }
        )
        gui.processEvents()
        assert (
            window.overview_summary_label.text()
            == "Found 2 local tasks/sessions. Files are verified before playback."
        )
        assert window.recent_table.columnCount() == 3
        assert [window.recent_table.horizontalHeaderItem(i).text() for i in range(3)] == [
            "Session",
            "Status",
            "Engine",
        ]
        assert window.library_table.columnCount() == 5
        assert [window.library_table.horizontalHeaderItem(i).text() for i in range(5)] == [
            "Session",
            "Status",
            "Engine",
            "Duration",
            "Created",
        ]

        # Theme forwarding & status dot live update through MainWindow.set_theme
        window.set_theme("dark")
        dot_dark = window.recent_table.item(0, 1).icon().pixmap(16, 16).toImage().pixelColor(8, 8)
        assert dot_dark == QColor(DARK["success"])
        window.set_theme("light")
        dot_light = window.recent_table.item(0, 1).icon().pixmap(16, 16).toImage().pixelColor(8, 8)
        assert dot_light == QColor(LIGHT["success"])

        # Dynamic localization forwarding through Controller.refresh_localized_text
        control.refresh_localized_text("zh_CN")
        gui.processEvents()
        assert "本地找到 2 个" in window.overview_summary_label.text()
        assert [window.recent_table.horizontalHeaderItem(i).text() for i in range(3)] == [
            "项目",
            "状态",
            "引擎",
        ]
        control.refresh_localized_text("en")
        gui.processEvents()
        assert (
            window.overview_summary_label.text()
            == "Found 2 local tasks/sessions. Files are verified before playback."
        )

        # Double-click on Overview recent_table invokes real load_selected(recent=True) -> _verify
        window.overview_view.ensureWidgetVisible(window.recent_table)
        gui.processEvents()
        rect = window.recent_table.visualItemRect(window.recent_table.item(0, 0))
        vp = window.recent_table.viewport()
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=rect.center())
        QTest.mouseDClick(vp, Qt.MouseButton.LeftButton, pos=rect.center())
        assert verified_calls == [(str(tmp_path / "job-1"), "English practice")]
    finally:
        control.poll_timer.stop()
        window.close_allowed = True
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gui.setPalette(previous_palette)
        gui.setStyleSheet(previous_stylesheet)
