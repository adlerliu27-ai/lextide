"""Carbon's fixed composer dock and local clean preview are presentation-only."""

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g8_generation as g8  # noqa: E402
from PySide6.QtCore import QPoint, QTimer  # noqa: E402
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QScrollArea,
    QTextEdit,
)  # noqa: E402
from test_g9_clean_preview import finish_clean, import_source  # noqa: E402

from lextide.desktop.controller import confirm_transmission  # noqa: E402
from lextide.desktop.i18n import tr  # noqa: E402

desktop = g8.desktop


@pytest.mark.parametrize(("width", "height"), [(980, 700), (1180, 832)])
def test_preview_actions_and_voice_controls_fit_without_scrolling(desktop, width, height):
    w, c, requests, starts, _ = desktop
    w.resize(width, height)
    w.show_page("Studio")
    w.show()
    import_source(c)
    assert w.generation_summary.property("role") == "error"
    assert w.generation_summary.text() == tr(c._reading_issue())
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    finish_clean(requests.pop())
    QApplication.processEvents()
    assert w.generation_summary.property("role") == "muted"
    assert w.preview_badge.isVisible()
    assert w.preview_badge.text() == tr(
        "Not yet applied" if width < 1080 else "Preview — not yet applied"
    )
    assert w.generation_summary.text() == tr(
        "Edge online · {voice} · Rate {rate}", voice="Guy", rate=c._settings()["rate"]
    )
    assert w.text_tabs.count() == 2
    tab_bar = w.text_tabs.tabBar()
    for index in range(tab_bar.count()):
        tab = tab_bar.tabRect(index)
        assert tab.width() >= tab_bar.fontMetrics().horizontalAdvance(tab_bar.tabText(index)) + 12
        assert tab.x() >= 0 and tab.right() < tab_bar.width()
    assert not w.studio_options.isVisible()
    assert w.source_edit.isReadOnly() and not w.speech_edit.isReadOnly()
    assert "⌘V" in w.source_edit.placeholderText() and "⌘O" in w.source_edit.placeholderText()
    assert not w.paste_source_button.icon().isNull()
    scroller = w.pages["Studio"].findChild(QScrollArea)
    assert scroller.horizontalScrollBar().maximum() == scroller.verticalScrollBar().maximum() == 0
    for group in (
        [
            w.clean_mode_combo,
            w.clean_button,
            w.discard_preview_button,
            w.undo_button,
            w.reset_button,
            w.review_toggle,
        ],
        [
            w.engine_combo,
            w.gender_combo,
            w.voice_combo,
            w.rate_spin,
            w.more_options_toggle,
            w.generate_button,
        ],
    ):
        right = 0
        for control in group:
            assert control.isVisible()
            point = control.mapTo(w, QPoint())
            assert point.x() >= right
            assert point.y() + control.height() <= w.height()
            right = point.x() + control.width()
            assert right <= w.width()
    assert w.generate_button.width() >= w.generate_button.sizeHint().width()
    assert not scroller.isAncestorOf(w.generate_button)
    assert not scroller.isAncestorOf(w.voice_combo)
    w.more_options_toggle.click()
    assert all(
        field.isVisible()
        for field in (w.encoding_combo, w.output_dir_edit, w.title_edit, w.engine_settings_button)
    )
    QApplication.processEvents()
    assert w.filter_check.width() >= w.filter_check.sizeHint().width()
    assert w.filter_check.geometry().bottom() < w.output_dir_edit.geometry().top()
    w.more_options_toggle.click()
    w.clean_button.click()
    assert not w.preview_badge.isVisible() and not starts


def test_consent_digest_wraps_visually_but_selects_exact_value(desktop):
    w, *_ = desktop
    digest = "9a1b" * 16
    result = []

    def inspect():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, QDialog):
            return
        try:
            view = dialog.findChild(QTextEdit, "ConsentDigest")
            view.selectAll()
            document = view.document()
            lines = document.firstBlock().layout()
            bottom = lines.lineAt(lines.lineCount() - 1)
            result.append(
                (
                    view.textCursor().selectedText(),
                    lines.lineCount(),
                    bottom.y() + bottom.height(),
                    view.viewport().height(),
                    dialog.findChild(QCheckBox).isChecked(),
                    dialog.findChild(QDialogButtonBox)
                    .button(QDialogButtonBox.StandardButton.Ok)
                    .isEnabled(),
                )
            )
        finally:
            dialog.reject()

    QTimer.singleShot(20, inspect)
    assert not confirm_transmission(
        w, "A local QA phrase.", {"voice": "en-US-GuyNeural", "rate": "-4%"}, digest
    )
    [(selected, line_count, bottom, viewport_height, checked, allowed)] = result
    assert selected == digest and len(selected) == 64
    assert line_count >= 2 and bottom <= viewport_height
    assert not checked and not allowed
