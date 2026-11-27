"""PF-01 idle/loaded spacing and BUG-UI-PLAYBACK-RATE-01 closed-combo rendering contract."""

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox

from lextide.desktop.i18n import configure_language, current_language
from lextide.desktop.localized import retranslate_ui
from lextide.desktop.player import LocalPlayer
from lextide.desktop.window import MainWindow


@pytest.mark.parametrize("language", ["en", "zh_CN"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_transport_stays_visible_in_order_at_minimum_width(language, theme):
    app = QApplication.instance() or QApplication([])
    previous_palette = app.palette()
    previous_stylesheet = app.styleSheet()
    previous_language = current_language()
    configure_language(language)
    window = MainWindow()
    try:
        window.set_theme(theme)
        window.resize(980, 740)
        window.show()
        controls = (
            window.player_title_label,
            window.previous_button,
            window.play_button,
            window.next_button,
            window.position_label,
            window.seek_slider,
            window.duration_label,
            window.playback_rate_combo,
            window.volume_slider,
        )
        for loaded in (False, True, False):
            window.set_player_loaded(loaded)
            app.processEvents()
            bar = window.player_bar
            assert window.player_detail_label.isVisible() == loaded
            if loaded:
                assert bar.height() == 64
            else:
                assert bar.height() in (40, 56)
            assert window.play_button.width() == window.play_button.height() == 36
            assert window.play_button.iconSize().width() == 16
            assert window.playback_rate_combo.currentData() == 1.0
            assert window.duration_label.text() == "--:--"
            previous_right = -1
            for control in controls:
                bounds = QRect(control.mapTo(bar, QPoint(0, 0)), control.size())
                assert control.isVisible() and not bounds.isEmpty()
                assert bar.rect().contains(bounds)
                assert previous_right < bounds.left()
                previous_right = bounds.right()
    finally:
        window.close_allowed = True
        window.close()
        window.deleteLater()
        configure_language(previous_language)
        app.setPalette(previous_palette)
        app.setStyleSheet(previous_stylesheet)


@pytest.mark.parametrize("width", [980, 1180])
@pytest.mark.parametrize("language", ["en", "zh_CN"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_recommended_56pt_idle_candidate_keeps_horizontal_geometry_and_10pt_clearance(
    width, language, theme
):
    """Verify 56 pt / (20, 9, 20, 10) idle geometry and 10/10 pt clearance."""
    app = QApplication.instance() or QApplication([])
    previous_palette = app.palette()
    previous_stylesheet = app.styleSheet()
    previous_language = current_language()
    configure_language(language)
    window = MainWindow()
    try:
        window.set_theme(theme)
        window.resize(width, 804 if width == 1180 else 740)
        window.show()
        controls = (
            window.player_title_label,
            window.previous_button,
            window.play_button,
            window.next_button,
            window.position_label,
            window.seek_slider,
            window.duration_label,
            window.playback_rate_combo,
            window.volume_slider,
        )
        bar = window.player_bar
        window.set_player_loaded(False)
        app.processEvents()
        baseline_horizontal = [(c.mapTo(bar, QPoint(0, 0)).x(), c.width()) for c in controls]
        if bar.height() != 56:
            bar.setFixedHeight(56)
            bar.layout().setContentsMargins(20, 9, 20, 10)
            app.processEvents()
        assert bar.height() == 56
        play_top = window.play_button.mapTo(bar, QPoint(0, 0)).y()
        play_bottom = bar.height() - (play_top + window.play_button.height())
        assert (play_top, play_bottom) == (10, 10)
        assert [
            (c.mapTo(bar, QPoint(0, 0)).x(), c.width()) for c in controls
        ] == baseline_horizontal
        assert window.duration_label.text() == "--:--"

        window.set_player_loaded(True)
        app.processEvents()
        assert bar.height() == 64
        assert [
            (c.mapTo(bar, QPoint(0, 0)).x(), c.width()) for c in controls
        ] == baseline_horizontal
    finally:
        window.close_allowed = True
        window.close()
        window.deleteLater()
        configure_language(previous_language)
        app.setPalette(previous_palette)
        app.setStyleSheet(previous_stylesheet)


@pytest.mark.parametrize("width", [980, 1073, 1180])
@pytest.mark.parametrize("language", ["en", "zh_CN"])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("loaded", [False, True], ids=["idle", "loaded64"])
def test_playback_rate_closed_box_renders_all_options_without_clipping(
    width, language, theme, loaded
):
    """BUG-UI-PLAYBACK-RATE-01: verify geometry and actual painted pixels for every rate."""
    app = QApplication.instance() or QApplication([])
    previous_palette = app.palette()
    previous_stylesheet = app.styleSheet()
    previous_language = current_language()
    configure_language(language)
    window = MainWindow()
    try:
        retranslate_ui(window)
        window.set_theme(theme)
        window.set_player_loaded(loaded)
        window.resize(width, 804 if width == 1180 else 740)
        window.show()
        app.processEvents()

        combo = window.playback_rate_combo
        expected_items = [(0.75, "0.75×"), (1.0, "1×"), (1.25, "1.25×"), (1.5, "1.5×"), (2.0, "2×")]
        assert [(combo.itemData(i), combo.itemText(i)) for i in range(combo.count())] == (
            expected_items
        )
        assert combo.width() == 76

        for focus in (False, True):
            if focus:
                combo.setFocus()
            else:
                combo.clearFocus()
            app.processEvents()
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            edit_rect = combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxEditField,
                combo,
            )
            arrow_rect = combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxArrow,
                combo,
            )
            assert not edit_rect.intersects(arrow_rect)
            for idx, (_, label) in enumerate(expected_items):
                combo.setCurrentIndex(idx)
                app.processEvents()
                fm = combo.fontMetrics()
                assert edit_rect.width() >= fm.horizontalAdvance(label)
                assert edit_rect.width() >= fm.boundingRect(label).width()

        # Verify actual painted glyph pixels inside edit_rect (not just currentText):
        combo.clearFocus()
        for idx, full_label, without_x, clipped_stub in (
            (0, "0.75×", "0.75", "0."),
            (2, "1.25×", "1.25", "1."),
            (3, "1.5×", "1.5", "1."),
        ):
            combo.setCurrentIndex(idx)
            app.processEvents()
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            er = combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxEditField,
                combo,
            )
            img_full = combo.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)

            combo.setItemText(idx, without_x)
            app.processEvents()
            img_no_x = combo.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)

            combo.setItemText(idx, clipped_stub)
            app.processEvents()
            img_stub = combo.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)

            combo.setItemText(idx, full_label)
            app.processEvents()

            dpr = img_full.devicePixelRatio()
            x0, x1 = int(er.left() * dpr), int((er.right() + 1) * dpr)
            y0, y1 = int(er.top() * dpr), int((er.bottom() + 1) * dpr)
            diff_x_symbol = sum(
                1
                for py in range(y0, y1)
                for px in range(x0, x1)
                if img_full.pixelColor(px, py) != img_no_x.pixelColor(px, py)
            )
            diff_decimals = sum(
                1
                for py in range(y0, y1)
                for px in range(x0, x1)
                if img_no_x.pixelColor(px, py) != img_stub.pixelColor(px, py)
            )
            assert diff_x_symbol > 8, (full_label, diff_x_symbol)
            assert diff_decimals > 8, (full_label, diff_decimals)
    finally:
        window.close_allowed = True
        window.close()
        window.deleteLater()
        configure_language(previous_language)
        app.setPalette(previous_palette)
        app.setStyleSheet(previous_stylesheet)


def test_playback_rate_interactions_other_comboboxes_and_player_forwarding():
    """Verify popup, keyboard, cross-page/Focus/theme/lang, LocalPlayer, and other QComboBoxes."""
    app = QApplication.instance() or QApplication([])
    previous_palette = app.palette()
    previous_stylesheet = app.styleSheet()
    previous_language = current_language()
    configure_language("en")
    window = MainWindow()
    player = LocalPlayer(window)
    try:
        window.set_theme("light")
        window.resize(1180, 804)
        window.show_page("Listen")
        window.show()
        app.processEvents()

        # Other QComboBoxes outside QFrame#Player keep their standard edit/arrow separation
        for page_name, other_combo in (
            ("Studio", window.clean_mode_combo),
            ("Studio", window.engine_combo),
            ("Listen", window.subtitle_mode_combo),
            ("Listen", window.font_combo),
            ("Settings", window.theme_combo),
            ("Settings", window.language_combo),
        ):
            window.show_page(page_name)
            app.processEvents()
            opt = QStyleOptionComboBox()
            other_combo.initStyleOption(opt)
            er = other_combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                opt,
                QStyle.SubControl.SC_ComboBoxEditField,
                other_combo,
            )
            ar = other_combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                opt,
                QStyle.SubControl.SC_ComboBoxArrow,
                other_combo,
            )
            assert not er.intersects(
                ar
            ) and er.width() >= other_combo.fontMetrics().horizontalAdvance(
                other_combo.currentText()
            )

        window.show_page("Listen")
        combo = window.playback_rate_combo
        combo.showPopup()
        app.processEvents()
        assert combo.view().isVisible()
        combo.hidePopup()
        app.processEvents()

        combo.setCurrentIndex(0)
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key.Key_Down)
        app.processEvents()
        assert combo.currentText() == "1×" and abs(player.media.playbackRate() - 1.0) < 1e-6

        player._media_status(QMediaPlayer.MediaStatus.LoadedMedia)
        for idx, (rate, text) in enumerate(
            [(0.75, "0.75×"), (1.0, "1×"), (1.25, "1.25×"), (1.5, "1.5×"), (2.0, "2×")]
        ):
            player._state(
                QMediaPlayer.PlaybackState.PlayingState
                if idx % 2 == 0
                else QMediaPlayer.PlaybackState.PausedState
            )
            combo.setCurrentIndex(idx)
            app.processEvents()
            assert combo.currentText() == text
            assert abs(player.media.playbackRate() - rate) < 1e-6
            assert window.player_bar.height() == 64

        combo.setCurrentIndex(0)
        for page in ("Overview", "Studio", "Library", "Listen"):
            window.show_page(page)
            app.processEvents()
            assert combo.currentText() == "0.75×" and window.player_bar.height() == 64

        window.focus_check.setChecked(True)
        app.processEvents()
        assert combo.currentText() == "0.75×" and window.player_bar.height() == 64
        window.focus_check.setChecked(False)

        for theme in ("dark", "light"):
            window.set_theme(theme)
            for lang in ("zh_CN", "en"):
                configure_language(lang)
                retranslate_ui(window)
                app.processEvents()
                assert combo.currentText() == "0.75×"
                assert abs(player.media.playbackRate() - 0.75) < 1e-6
    finally:
        window.close_allowed = True
        window.close()
        window.deleteLater()
        configure_language(previous_language)
        app.setPalette(previous_palette)
        app.setStyleSheet(previous_stylesheet)
