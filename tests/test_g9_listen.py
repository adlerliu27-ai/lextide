"""Listen layout changes preserve local playback, cue identity and bilingual content.

Qt offscreen assertions cover geometry and state, not real Cocoa playback or listening.
"""

import hashlib

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QUrl  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea  # noqa: E402

from lextide.desktop import controller, service  # noqa: E402
from lextide.desktop.bilingual_ui import CHINESE_ROLE  # noqa: E402
from lextide.desktop.i18n import configure_language, tr  # noqa: E402
from lextide.desktop.preferences import load_preferences, save_preferences  # noqa: E402
from lextide.desktop.window import MainWindow  # noqa: E402


@pytest.fixture(params=["en", "zh_CN"])
def desktop(request, tmp_path, monkeypatch):
    configure_language(request.param)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    preferences = load_preferences(tmp_path)
    preferences["language"] = request.param
    save_preferences(tmp_path, preferences)
    monkeypatch.setattr(controller.Controller, "refresh_history", lambda *_: None)
    monkeypatch.setattr(controller.Controller, "refresh_engines", lambda *_: None)
    window = MainWindow()
    control = controller.Controller(window, tmp_path)
    monkeypatch.setattr(control.player.media, "setSource", lambda *_: None)
    window.show_error = lambda *args: pytest.fail(f"Unexpected modal error: {args}")
    window.show_page("Listen")
    window.show()
    app.processEvents()
    yield app, window, control
    control.poll_timer.stop()
    control.player.media.stop()
    window.close_allowed = True
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    configure_language("en")


def load_session(window, control):
    job = control.root / "unchanged-english-job"
    job.mkdir()
    srt = (
        "1\n00:00:00,100 --> 00:00:02,000\nThe first sentence remains English.\n\n"
        "2\n00:00:02,100 --> 00:00:05,000\nThe second sentence keeps its original timing.\n"
    )
    (job / "subtitles.srt").write_text(srt, encoding="utf-8")
    (job / "audio.mp3").write_bytes(b"not decoded: local state fixture")
    (job / "manifest.json").write_text('{"preserve": true}')
    verified = {
        "job_path": str(job),
        "srt_path": str(job / "subtitles.srt"),
        "audio_path": str(job / "audio.mp3"),
        "cues": service.parse_srt(srt),
    }
    control._verified(verified, "English session")
    bilingual = control.bilingual
    bilingual.document = bilingual.store.save(
        bilingual.source,
        ("第一句仍然是英文。", "第二句保留原来的时间轴。"),
        expected_revision=None,
        origin="manual",
    )
    bilingual._display()
    window.subtitle_mode_combo.setCurrentIndex(window.subtitle_mode_combo.findData("bilingual"))
    return job


def file_hashes(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_unloaded_state_lives_in_transcript_and_library_is_actionable(desktop):
    app, w, c = desktop
    assert w.transcript_stack.currentWidget() is w.transcript_empty
    assert w.transcript_empty.isVisible()
    assert w.transcript_list.count() == 0
    labels = [child.text() for child in w.transcript_empty.findChildren(QLabel)]
    assert tr("No session loaded") in labels
    assert tr("Open a session from Library") in labels
    assert w.open_library_button.text() == tr("Open Library")
    assert not w.play_button.isEnabled()
    assert c.player.loaded_path is None
    assert not w.listen_title_label.isVisible()
    assert not w.subtitle_import_button.isVisible()
    assert not w.subtitle_status_label.isVisible()
    w.open_library_button.click()
    app.processEvents()
    assert w.page_stack.currentWidget() is w.pages["Library"]


def test_loading_removes_empty_state_without_repeating_title_in_header(desktop):
    app, w, c = desktop
    load_session(w, c)
    app.processEvents()
    assert w.transcript_stack.currentWidget() is w.transcript_list
    assert not w.transcript_empty.isVisible()
    assert not w.open_library_button.isVisible()
    assert w.transcript_list.count() == 2
    assert w.listen_title_label.text() == "English session"
    assert w.listen_aside.isAncestorOf(w.listen_title_label)
    assert not w.listen_title_label.isVisible()
    assert w.subtitle_import_button.isVisible()
    assert w.subtitle_status_label.isVisible()
    assert w.focus_check.text() == tr("Focus view")


@pytest.mark.parametrize("size", [(1440, 900), (1280, 800), (1024, 768)])
@pytest.mark.parametrize("loaded", [False, True])
def test_transcript_expands_with_viewport_without_page_scrolling(desktop, size, loaded):
    app, w, c = desktop
    if loaded:
        load_session(w, c)
    w.resize(*size)
    app.processEvents()
    page = w.pages["Listen"]
    # Reader includes its compact controls; the actual text viewport remains the
    # largest individual region, including at the smallest supported test size.
    assert w.listen_reader.height() / page.height() > 0.70
    assert w.transcript_stack.height() / page.height() > 0.50
    assert w.listen_header.height() <= 65
    assert w.transcript_stack.width() / page.width() > 0.90
    assert not page.findChildren(QScrollArea)
    assert w.transcript_list.horizontalScrollBar().maximum() == 0
    bottom = w.listen_reader.mapTo(w, QPoint(0, w.listen_reader.height())).y()
    player_top = w.play_button.parentWidget().mapTo(w, QPoint()).y()
    assert bottom < player_top
    for control in (w.focus_check, w.font_combo, w.subtitle_mode_combo, w.follow_check):
        point = control.mapTo(w, QPoint())
        assert control.isVisible()
        assert 0 <= point.x() <= w.width() - control.width()
        assert 0 <= point.y() <= w.height() - control.height()


@pytest.mark.parametrize("playing", [False, True])
@pytest.mark.parametrize("details_open", [False, True])
def test_focus_only_changes_layout_preserving_media_and_translation_state(
    desktop, monkeypatch, playing, details_open
):
    app, w, c = desktop
    job = load_session(w, c)
    w.font_combo.setCurrentIndex(w.font_combo.findData(24))
    w.session_details_toggle.setChecked(details_open)
    c.player._duration(5000)
    c.player._position(2500)
    state = (
        QMediaPlayer.PlaybackState.PlayingState
        if playing
        else QMediaPlayer.PlaybackState.PausedState
    )
    c.player._state(state)
    monkeypatch.setattr(c.player.media, "playbackState", lambda: state)
    monkeypatch.setattr(c.player.media, "position", lambda: 2500)
    source = QUrl.fromLocalFile(str(job / "audio.mp3"))
    monkeypatch.setattr(c.player.media, "source", lambda: source)
    before_files = file_hashes(c.root)
    cue_snapshot = list(c.player.cues)
    bilingual = c.bilingual.document
    app.processEvents()
    normal_area = w.transcript_stack.width() * w.transcript_stack.height()

    def forbidden(*_):
        pytest.fail("Focus view must not reload, stop, play or seek audio")

    with monkeypatch.context() as guarded:
        for method in ("setSource", "setPosition", "stop", "play", "pause"):
            guarded.setattr(c.player.media, method, forbidden)
        w.focus_check.click()
        app.processEvents()
        assert w.focus_check.text() == tr("Exit focus view")
        assert not w.sidebar.isVisible()
        assert not w.listen_subtitle.isVisible()
        assert not w.listen_aside.isVisible()
        assert not w.subtitle_import_button.isVisible()
        assert w.subtitle_status_label.isVisible()
        assert w.transcript_stack.width() * w.transcript_stack.height() > normal_area
        for widget in (w.play_button, w.seek_slider, w.font_combo, w.subtitle_mode_combo):
            assert widget.isVisible()
        # Changing bilingual presentation inside Focus never touches media.
        w.subtitle_mode_combo.setCurrentIndex(w.subtitle_mode_combo.findData("english"))
        w.subtitle_mode_combo.setCurrentIndex(w.subtitle_mode_combo.findData("bilingual"))
        w.focus_check.click()
        app.processEvents()

    assert w.sidebar.isVisible()
    assert w.listen_subtitle.isVisible()
    assert w.listen_aside.isVisible() == details_open
    assert w.session_details_toggle.isChecked() == details_open
    assert c.player.media.source() == source
    assert c.player.media.position() == 2500
    assert c.player.media.playbackState() == state
    assert c.player.cues == cue_snapshot
    assert c.player.active == 1
    assert w.transcript_list.currentRow() == 1
    assert w.seek_slider.value() == 2500
    assert w.font_combo.currentData() == 24
    assert w.subtitle_mode_combo.currentData() == "bilingual"
    assert c.bilingual.document is bilingual
    assert w.transcript_list.item(1).data(CHINESE_ROLE) == bilingual.texts[1]
    assert file_hashes(c.root) == before_files


def test_focus_exit_and_empty_state_translate_immediately_without_reconstruction(desktop):
    app, w, c = desktop
    w.focus_check.click()
    identity = (id(w.transcript_list), id(c.player), id(c.player.media))
    other = "zh_CN" if c.preferences["language"] == "en" else "en"
    c.refresh_localized_text(other)
    app.processEvents()
    assert w.focus_check.text() == tr("Exit focus view")
    assert w.open_library_button.text() == tr("Open Library")
    assert w.focus_check.isChecked()
    assert w.transcript_stack.currentWidget() is w.transcript_empty
    assert (id(w.transcript_list), id(c.player), id(c.player.media)) == identity
    w.focus_check.click()
    assert w.focus_check.text() == tr("Focus view")


def test_navigating_away_from_focus_restores_normal_navigation(desktop):
    app, w, _ = desktop
    w.focus_check.click()
    assert not w.sidebar.isVisible()
    w.show_page("Settings")
    app.processEvents()
    assert not w.focus_check.isChecked()
    assert w.sidebar.isVisible()
    w.show_page("Listen")
    assert w.focus_check.text() == tr("Focus view")
    assert w.transcript_empty.isVisible()
