"""Carbon playback geometry and reader cues. Synthetic Qt events, no user media."""

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from lextide.desktop.player import LocalPlayer
from lextide.desktop.window import MainWindow

Status = QMediaPlayer.MediaStatus
Playback = QMediaPlayer.PlaybackState


@pytest.fixture
def desktop():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    player = LocalPlayer(window)
    window.show_page("Listen")
    window.resize(1180, 804)
    window.show()
    app.processEvents()
    yield app, window, player
    window.close_allowed = True
    window.close()
    window.deleteLater()
    app.processEvents()


def test_player_height_tracks_decoder_status_across_playback_and_navigation(desktop, monkeypatch):
    app, w, player = desktop
    assert w.player_bar.height() == 56
    assert w.duration_label.text() == "--:--"
    player._media_status(Status.LoadingMedia)
    assert w.player_bar.height() == 56
    player._media_status(Status.LoadedMedia)
    for state in (Playback.PlayingState, Playback.PausedState, Playback.StoppedState):
        player._state(state)
        assert w.player_bar.height() == 64
    for page in ("Studio", "Library", "Listen"):
        w.show_page(page)
        app.processEvents()
        assert w.player_bar.height() == 64
    for milliseconds, expected in [
        (0, "00:00"),
        (17000, "00:17"),
        (143000, "02:23"),
        (3661000, "1:01:01"),
    ]:
        player._position(milliseconds)
        assert w.position_label.text() == expected
    for milliseconds, expected in [(-1, "--:--"), (0, "--:--"), (3665000, "1:01:05")]:
        player._duration(milliseconds)
        assert w.duration_label.text() == expected
        assert w.seek_slider.maximum() == max(0, milliseconds)
    monkeypatch.setattr(player.media, "duration", lambda: 3665000)
    player._media_status(Status.EndOfMedia)
    assert w.position_label.text() == w.duration_label.text() == "1:01:05"
    assert w.player_bar.height() == 64
    player._media_status(Status.LoadingMedia)
    assert w.player_bar.height() == 64
    monkeypatch.setattr(player.media, "source", lambda: QUrl())
    player._media_status(Status.NoMedia)
    assert w.player_bar.height() == 56
    assert not w.seek_slider.isEnabled()
    assert w.duration_label.text() == "--:--"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_active_row_remains_distinct_from_keyboard_selection(desktop, theme):
    app, w, _ = desktop
    w.set_theme(theme)
    w.set_transcript([{"text": f"Sentence {i}.", "start_ms": 3000 * i} for i in range(60)])
    w.set_active_cue(0)
    w.follow_check.setChecked(False)
    w.transcript_list.setCurrentRow(1)
    scroll = w.transcript_list.verticalScrollBar()
    scroll.setValue(0)
    w.set_active_cue(50)
    app.processEvents()
    assert scroll.value() == 0
    assert w.transcript_list.currentRow() == 1
    assert w.transcript_list.property("active_cue") == 50
    w.set_active_cue(0)
    widget = w.transcript_list
    image = QImage(widget.viewport().size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    for row in (0, 1):
        option = QStyleOptionViewItem()
        option.initFrom(widget)
        option.rect = widget.visualItemRect(widget.item(row))
        widget.itemDelegate().paint(painter, option, widget.model().index(row, 0))
    painter.end()
    active = widget.visualItemRect(widget.item(0))
    assert image.pixelColor(active.left() + 3, active.center().y()) == QColor(w._colors["accent"])
    assert image.pixelColor(active.right() - 10, active.center().y()) == QColor(
        w._colors.get("transcript_active_bg", w._colors["accent_soft"])
    )
    selected = widget.visualItemRect(widget.item(1))
    assert image.pixelColor(selected.left() + 3, selected.center().y()) != QColor(
        w._colors["accent"]
    )


@pytest.mark.parametrize("width,pixels", [(980, 18), (1180, 32), (1600, 40), (1700, 18)])
def test_reader_width_controls_and_hour_labels_fit(desktop, width, pixels):
    app, w, p = desktop
    w.resize(width, 804)
    w.set_transcript([{"start_ms": 3600000, "text": "Long reading material. " * 80}])
    p._media_status(Status.LoadedMedia)
    p._duration(75000000)
    p._position(37000000)
    w.font_combo.setCurrentIndex(w.font_combo.findData(pixels))
    for focus in (False, True):
        w.focus_check.setChecked(focus)
        app.processEvents()
        widget = w.transcript_list
        d = widget.itemDelegate()
        available = (
            widget.viewport().width()
            - 2 * widget.spacing()
            - (d.LEFT_PADDING + d.RIGHT_PADDING + d.TIME_WIDTH + d.COLUMN_GAP)
        )
        assert d.TIME_WIDTH == 56
        assert d._body_width() == min(available, max(820, pixels * 44))
        assert widget.horizontalScrollBar().maximum() == 0
        follow_right = w.follow_check.mapTo(w, QPoint()).x() + w.follow_check.width()
        assert w.subtitle_mode_combo.mapTo(w, QPoint()).x() - follow_right >= 16
        for clock in (w.position_label, w.duration_label):
            assert clock.width() >= clock.fontMetrics().horizontalAdvance(clock.text())
        assert w.player_bar.height() == 64


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("loaded", [False, True])
def test_play_control_is_a_real_circle_in_compact_and_loaded_bars(desktop, theme, loaded):
    app, w, player = desktop
    w.set_theme(theme)
    if loaded:
        player._media_status(Status.LoadedMedia)
        w.set_playing(True)
    app.processEvents()
    control = w.play_button
    assert (control.width(), control.height()) == (36, 36)
    assert w.player_bar.rect().contains(control.geometry())
    # Sample the composited bar. A circle leaves all four corners the same
    # color as the surrounding surface; a square draws its border there.
    frame = w.player_bar.grab().toImage().scaled(w.player_bar.size())
    image = frame.copy(control.geometry())
    surface = frame.pixelColor(control.x() - 3, control.y() + 8)
    for x, y in ((1, 1), (34, 1), (1, 34), (34, 34), (8, 1), (1, 8)):
        assert image.pixelColor(x, y) == surface
    assert image.pixelColor(18, 0) != surface
    expected_fill = w._colors["accent"] if loaded else w._colors["tint"]
    assert image.pixelColor(18, 7) == QColor(expected_fill)
