"""G8 hierarchy, pinned settings action and aligned transcript drawing."""

from math import ceil

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea, QStyleOptionViewItem  # noqa: E402

from lextide.desktop import bilingual_ui  # noqa: E402
from lextide.desktop.i18n import configure_language, tr  # noqa: E402
from lextide.desktop.window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    value = QApplication.instance() or QApplication([])
    value.setQuitOnLastWindowClosed(False)
    return value


@pytest.fixture(params=["en", "zh_CN"])
def window(app, request):
    configure_language(request.param)
    value = MainWindow()
    value.resize(980, 700)
    value.show()
    app.processEvents()
    yield value
    value.close_allowed = True
    value.close()
    value.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    configure_language("en")


def test_studio_prioritizes_reading_and_retains_local_review_actions(window, app):
    window.show_page("Studio")
    app.processEvents()
    assert window.studio_splitter.count() == 2
    original, reading = window.studio_splitter.sizes()
    assert reading > original > 0
    assert window.review_content.isHidden()
    assert not window.review_toggle.isChecked()
    assert not window.undo_button.isHidden()
    assert not window.reset_button.isHidden()
    assert window.preview_button.isHidden()
    assert window.approval_check.isHidden()
    assert window.cancel_generation_button.isHidden()
    window.review_toggle.click()
    app.processEvents()
    assert window.review_content.isVisible()
    window.review_toggle.click()
    assert window.review_content.isHidden()
    assert not hasattr(window, "cleaning_strength_combo")
    window.clean_mode_combo.setCurrentIndex(window.clean_mode_combo.findData("english"))
    window.clean_mode_combo.setCurrentIndex(window.clean_mode_combo.findData("original"))
    controls = [window.clean_mode_combo, window.clean_button]
    assert all(a.geometry().right() < b.geometry().left() for a, b in zip(controls, controls[1:]))
    assert window.engine_combo.findData("auto") >= 0
    window.engine_settings_button.click()
    assert window.page_stack.currentWidget() is window.pages["Settings"]
    assert window.settings_advanced_toggle.isChecked()
    assert window.settings_advanced_content.isVisible()
    app.processEvents()
    settings_scroll = window.pages["Settings"].findChild(QScrollArea)
    advanced_top = window.settings_advanced_toggle.mapTo(settings_scroll.viewport(), QPoint()).y()
    assert 0 <= advanced_top <= 12


@pytest.mark.parametrize("online", [False, True])
def test_generate_action_keeps_preferred_width_and_right_alignment(window, app, online):
    window.show_page("Studio")
    window.online_notice.setText(tr("This reading text will be sent to Microsoft Edge TTS."))
    window.online_notice.setVisible(online)
    window.generate_button.setText(tr("Send to Edge & Generate" if online else "Generate Audio"))
    app.processEvents()
    generate = window.generate_button
    footer = generate.parentWidget()
    assert generate.width() <= generate.sizeHint().width() + 2
    assert abs(footer.width() - 26 - generate.x() - generate.width()) <= 1
    assert generate.geometry().center().x() > footer.width() // 2


def test_listen_uses_full_width_and_details_stay_collapsed_after_focus(window, app):
    window.show_page("Listen")
    window.set_transcript([{"text": "A verified sentence.", "start_ms": 0}])
    app.processEvents()
    assert window.transcript_list.width() >= window.pages["Listen"].width() - 100
    assert window.listen_aside.isHidden()
    window.focus_check.setChecked(True)
    window.focus_check.setChecked(False)
    assert window.listen_aside.isHidden()
    window.session_details_toggle.click()
    app.processEvents()
    assert window.listen_aside.isVisible()
    assert window.listen_aside.mapTo(window, QPoint()).y() > (
        window.transcript_list.mapTo(window, QPoint()).y() + window.transcript_list.height()
    )


def test_settings_save_remains_visible_at_both_scroll_extremes(window, app):
    window.show_page("Settings")
    scroller = window.pages["Settings"].findChild(QScrollArea)
    assert scroller is not None
    assert not scroller.isAncestorOf(window.settings_save_button)
    assert window.settings_save_button is window.save_settings
    assert window.settings_advanced_content.isHidden()
    window.settings_advanced_toggle.click()
    app.processEvents()
    assert window.settings_advanced_content.isVisible()
    positions = []
    for position in (0, scroller.verticalScrollBar().maximum()):
        scroller.verticalScrollBar().setValue(position)
        app.processEvents()
        assert window.settings_save_button.isVisible()
        point = window.settings_save_button.mapTo(window, QPoint())
        assert point.y() + window.settings_save_button.height() < window.height()
        positions.append(point)
    assert positions[0] == positions[1]


def test_dependency_configuration_is_only_visible_inside_advanced(window, app):
    window.show_page("Settings")
    controls = (
        window.ffmpeg_edit,
        window.ffprobe_edit,
        window.ffmpeg_button,
        window.ffprobe_button,
        window.media_status_label,
        window.model_dir_edit,
        window.offline_python_edit,
        window.model_dir_button,
        window.offline_python_button,
        window.model_refresh_button,
        window.model_status_label,
        window.model_install_label,
    )
    app.processEvents()
    assert not window.settings_advanced_toggle.isChecked()
    for control in controls:
        assert window.settings_advanced_content.isAncestorOf(control)
        assert not control.isVisible()
    assert window.theme_combo.isVisible()
    assert window.settings_output_dir_edit.isVisible()
    window.settings_advanced_toggle.click()
    app.processEvents()
    assert all(control.isVisible() for control in controls)


def test_task_diagnostics_are_collapsed_without_hiding_progress_or_actions(window, app):
    window.show_page("Tasks")
    diagnostic = '{"status": "failed", "path": "/local/task/diagnostic"}'
    window.task_detail_edit.setPlainText(diagnostic)
    app.processEvents()
    toggle = window.task_diagnostics_toggle
    assert not toggle.isChecked()
    assert toggle.arrowType() == Qt.ArrowType.RightArrow
    assert window.task_detail_edit.isHidden()
    for control in (
        window.task_stage_label,
        window.task_progress,
        window.task_pause_button,
        window.task_resume_button,
        window.task_cancel_button,
    ):
        assert control.isVisible()
    toggle.click()
    app.processEvents()
    assert toggle.arrowType() == Qt.ArrowType.DownArrow
    assert window.task_detail_edit.isVisible()
    assert window.task_detail_edit.isReadOnly()
    assert window.task_detail_edit.toPlainText() == diagnostic
    toggle.click()
    assert window.task_detail_edit.isHidden()
    assert toggle.arrowType() == Qt.ArrowType.RightArrow


@pytest.mark.parametrize("pixels", [12, 18, 40])
def test_time_column_and_bilingual_body_share_exact_draw_alignment(
    window, app, monkeypatch, pixels
):
    window.show_page("Listen")
    english = "A long English sentence wraps completely, including its last words. " * 3
    chinese = "中文字幕必须和英文正文对齐，并保留最后一行。" * 3
    window.set_transcript([{"text": english, "start_ms": 5000}])
    window.set_chinese_translations([chinese])
    window.set_subtitle_mode("bilingual")
    window.font_combo.setCurrentIndex(window.font_combo.findData(pixels))
    app.processEvents()
    widget = window.transcript_list
    delegate = widget.itemDelegate()
    original_layout = bilingual_ui._text_layout
    draws = []

    class CaptureLayout:
        def __init__(self, text, font, wrapped):
            self.text, self.font, self.wrapped = text, font, wrapped

        def draw(self, painter, origin):
            draws.append((self.text, self.font.pixelSize(), origin.x(), origin.y()))
            self.wrapped.draw(painter, origin)

    def capture(text, font, width):
        wrapped, height = original_layout(text, font, width)
        return CaptureLayout(text, font, wrapped), height

    monkeypatch.setattr(bilingual_ui, "_text_layout", capture)
    item = widget.item(0)
    option = QStyleOptionViewItem()
    option.initFrom(widget)
    option.font = widget.font()
    option.rect = widget.visualItemRect(item)
    image = QImage(widget.viewport().size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    delegate.paint(painter, option, widget.model().index(0, 0))
    painter.end()
    by_text = {entry[0]: entry for entry in draws}
    assert by_text[english][2] == by_text[chinese][2]
    assert by_text["00:05"][2] < by_text[english][2]
    assert by_text["00:05"][1] == 12
    assert by_text[chinese][1] == round(pixels * 0.8)
    _, english_height = original_layout(english, widget.font(), delegate._body_width())
    _, chinese_height = original_layout(
        chinese, delegate.chinese_font(widget.font()), delegate._body_width()
    )
    assert option.rect.height() >= (
        ceil(english_height + chinese_height + 6) + delegate.TOP_PADDING + delegate.BOTTOM_PADDING
    )
    assert item.text() == f"00:05   {english}"
    assert item.data(Qt.ItemDataRole.UserRole) == 0
    assert widget.horizontalScrollBar().maximum() == 0
    selected = []
    widget.itemClicked.connect(
        lambda clicked: selected.append(clicked.data(Qt.ItemDataRole.UserRole))
    )
    QTest.mouseClick(
        widget.viewport(),
        Qt.MouseButton.LeftButton,
        pos=option.rect.topLeft() + QPoint(35, 25),
    )
    assert selected == [0]
