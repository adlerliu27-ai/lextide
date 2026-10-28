"""G6 display boundaries and pixel-sized transcript controls; no audio/network needed."""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox  # noqa: E402

from lextide.desktop.i18n import configure_language, tr  # noqa: E402
from lextide.desktop.player import LocalPlayer  # noqa: E402
from lextide.desktop.theme import DARK, LIGHT  # noqa: E402
from lextide.desktop.window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    value = QApplication.instance() or QApplication([])
    value.setQuitOnLastWindowClosed(False)
    return value


@pytest.fixture(params=["en", "zh_CN"])
def window(app, request):
    assert configure_language(request.param)
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


def test_translated_display_preserves_internal_keys_and_user_content(window):
    assert window.PAGES == ("Overview", "Studio", "Library", "Listen", "Tasks", "Settings")
    for name in window.PAGES:
        window.show_page(name)
        assert window.breadcrumb_label.text() == tr(name)
        assert window.nav_buttons[name].text().strip() == tr(name)
    assert [window.engine_combo.itemData(i) for i in range(window.engine_combo.count())] == [
        "edge-tts",
        "kokoro",
        "auto",
    ]
    assert [window.theme_combo.itemData(i) for i in range(3)] == ["system", "light", "dark"]
    assert [window.language_combo.itemData(i) for i in range(2)] == ["en", "zh_CN"]
    assert [window.clean_mode_combo.itemData(i) for i in range(2)] == ["original", "english"]
    assert window.clean_button.text() == tr("Apply")
    assert window.clean_summary_label.wordWrap()
    assert window.language_hint.wordWrap()
    assert window.library_table.horizontalHeaderItem(1).text() == tr("Status")
    assert window.play_button.accessibleName() == tr("Play or pause audio")
    record = {"title": "Settings", "status": "paused", "engine": "kokoro", "path": "/user/source"}
    window.set_history([record])
    assert window.library_table.item(0, 0).text() == "Settings"
    assert window.library_table.item(0, 1).text() == tr("paused")
    assert window.library_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == record
    window.speech_edit.setPlainText("Keep 中文 and English exactly.")
    window.set_transcript([{"text": "Settings 中文原文", "start_ms": 0}])
    assert window.transcript_list.item(0).text() == "00:00   Settings 中文原文"
    assert window.speech_edit.toPlainText() == "Keep 中文 and English exactly."
    assert window.character_count_label.text() == tr("{count:,} characters", count=28)


def test_transcript_choices_apply_real_pixels_and_relayout(window, app):
    sizes = [12, 14, 16, 18, 20, 22, 24, 28, 32, 36, 40]
    assert not window.font_combo.isEditable()
    assert [window.font_combo.itemData(i) for i in range(window.font_combo.count())] == sizes
    assert window.font_combo.currentData() == 18
    window.show_page("Listen")
    text = "A long sentence wraps without losing its final words. 中文字幕仍然完整。 " * 3
    window.set_transcript([{"text": text, "start_ms": 0}, {"text": "Next cue", "start_ms": 2000}])
    app.processEvents()
    heights = []
    for size in sizes:
        window.font_combo.setCurrentIndex(window.font_combo.findData(size))
        app.processEvents()
        assert window.transcript_list.font().pixelSize() == size
        assert window.transcript_list.horizontalScrollBar().maximum() == 0
        first = window.transcript_list.visualItemRect(window.transcript_list.item(0))
        second = window.transcript_list.visualItemRect(window.transcript_list.item(1))
        assert first.height() > window.transcript_list.fontMetrics().height()
        assert second.top() > first.bottom()
        assert window.transcript_list.item(0).text().endswith(text)
        heights.append(first.height())
    assert heights == sorted(heights)
    assert heights[-1] > heights[0] * 2
    window.font_combo.setCurrentIndex(window.font_combo.findData(12))
    app.processEvents()
    first = window.transcript_list.visualItemRect(window.transcript_list.item(0))
    assert first.height() == heights[0]


def test_large_mixed_transcript_reserves_space_for_last_wrapped_line(window, app):
    window.show_page("Listen")
    window.set_transcript(
        [
            {
                "text": "This is the next sentence. 这是下一句。 " * 3,
                "start_ms": 5000,
            },
        ]
    )
    window.font_combo.setCurrentIndex(window.font_combo.findData(40))
    app.processEvents()
    item = window.transcript_list.visualItemRect(window.transcript_list.item(0))
    # The full-width G8 transcript still needs several wrapped lines for this
    # longer mixed-script fixture. Reserve room beyond the third line's baseline.
    assert item.height() >= 3 * window.transcript_list.fontMetrics().height() + 36


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_font_combo_has_visible_chevron_in_each_theme(window, app, theme):
    window.set_theme(theme)
    window.show_page("Listen")
    app.processEvents()
    combo = window.font_combo
    option = QStyleOptionComboBox()
    combo.initStyleOption(option)
    arrow = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxArrow,
        combo,
    )
    image = combo.grab().toImage()
    scale = image.devicePixelRatio()
    background = QColor((DARK if theme == "dark" else LIGHT)["surface"])
    ink = 0
    # Inspect only the center of the arrow area, excluding text and frame borders.
    for y in range(int((arrow.center().y() - 5) * scale), int((arrow.center().y() + 5) * scale)):
        for x in range(
            int((arrow.center().x() - 7) * scale), int((arrow.center().x() + 7) * scale)
        ):
            pixel = image.pixelColor(x, y)
            contrast = sum(
                abs(a - b)
                for a, b in zip(
                    pixel.getRgb()[:3],
                    background.getRgb()[:3],
                    strict=True,
                )
            )
            ink += contrast > 60
    assert ink >= 8 * scale * scale


@pytest.mark.parametrize("error", list(QMediaPlayer.Error)[1:])
def test_player_error_explains_failure_without_raw_backend_text(window, error):
    errors = []
    window.show_error = lambda title, message: errors.append((title, message))
    player = SimpleNamespace(
        window=window,
        _enable=lambda enabled: None,
        media=SimpleNamespace(error=lambda: error),
    )
    LocalPlayer._error(player)
    assert errors[0][0] == tr("Unable to play audio")
    assert errors[0][1]
    assert window.player_detail_label.text() == tr("Playback failed")
