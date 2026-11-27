"""One native local player, with sentence cues in the existing SRT time base."""

from __future__ import annotations

from bisect import bisect_right
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from .i18n import tr
from .listen_keys import ListenKeys


def clock_text(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def cue_at(cues: list[dict], starts: list[int], position: int) -> int | None:
    index = bisect_right(starts, position) - 1
    if index >= 0 and position < cues[index]["end_ms"]:
        return index
    return None


class LocalPlayer(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.media = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.media.setAudioOutput(self.audio)
        self.audio.setVolume(window.volume_slider.value() / 100)
        self.cues: list[dict] = []
        self.starts: list[int] = []
        self.active: int | None = None
        self.loaded_path: Path | None = None
        window.play_button.clicked.connect(self.toggle)
        self.listen_keys = ListenKeys(window)
        window.seek_slider.setSingleStep(1000)
        window.seek_slider.setPageStep(5000)
        window.seek_slider.valueChanged.connect(self.media.setPosition)
        window.seek_slider.sliderReleased.connect(
            lambda: self.media.setPosition(window.seek_slider.value())
        )
        window.volume_slider.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        window.previous_button.clicked.connect(lambda: self.step(-1))
        window.next_button.clicked.connect(lambda: self.step(1))
        window.playback_rate_combo.currentIndexChanged.connect(
            lambda: self.media.setPlaybackRate(float(window.playback_rate_combo.currentData()))
        )
        window.transcript_list.itemClicked.connect(
            lambda item: self.seek_cue(window.transcript_list.row(item))
        )
        window.transcript_list.itemActivated.connect(
            lambda item: self.seek_cue(window.transcript_list.row(item))
        )
        window.transcript_list.viewport().installEventFilter(self)
        window.transcript_list.installEventFilter(self)
        self.media.positionChanged.connect(self._position)
        self.media.durationChanged.connect(self._duration)
        self.media.playbackStateChanged.connect(self._state)
        self.media.mediaStatusChanged.connect(self._media_status)
        self.media.errorOccurred.connect(self._error)
        self._enable(False)

    def _enable(self, enabled):
        w = self.window
        for control in (w.play_button, w.previous_button, w.next_button, w.seek_slider):
            control.setEnabled(enabled)

    def load(self, verified: dict, title: str = ""):
        self.media.stop()
        self._enable(False)
        self.cues = verified["cues"]
        self.starts = [c["start_ms"] for c in self.cues]
        self.active = None
        self.loaded_path = Path(verified["audio_path"])
        self._duration(0)
        self.window.set_transcript(self.cues)
        self.window.listen_title_label.setText(title or self.loaded_path.parent.name)
        self.window.player_title_label.setText(title or self.loaded_path.parent.name)
        self.window.player_detail_label.setText(tr("Local MP3 · Loading"))
        self.window.listen_detail_label.setText(
            tr(
                "Verified MP3 + SRT · {count} sentence cues\n{path}",
                count=len(self.cues),
                path=self.loaded_path,
            )
        )
        self.media.setSource(QUrl.fromLocalFile(str(self.loaded_path)))
        self._position(0)

    def toggle(self):
        if self.media.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media.pause()
        else:
            if self.media.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
                self.media.setPosition(0)
            self.media.play()

    def seek_cue(self, index):
        if 0 <= index < len(self.cues):
            self.media.setPosition(self.cues[index]["start_ms"])
            self._position(self.cues[index]["start_ms"])

    def step(self, direction):
        index = bisect_right(self.starts, self.media.position()) - 1
        self.seek_cue(min(max(index + direction, 0), len(self.cues) - 1))

    def _position(self, position):
        w = self.window
        if not w.seek_slider.isSliderDown():
            with QSignalBlocker(w.seek_slider):
                w.seek_slider.setValue(position)
        w.position_label.setText(clock_text(position))
        index = cue_at(self.cues, self.starts, position)
        if index != self.active:
            self.active = index
            w.set_active_cue(index if index is not None else -1)

    def _duration(self, duration):
        with QSignalBlocker(self.window.seek_slider):
            self.window.seek_slider.setRange(0, max(0, duration))
        self.window.duration_label.setText(clock_text(duration) if duration > 0 else "--:--")

    def _state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.window.set_playing(playing)
        self.window.play_button.setAccessibleName(
            tr("Pause audio") if playing else tr("Play audio")
        )

    def _media_status(self, status):
        if status == QMediaPlayer.MediaStatus.NoMedia and self.media.source().isEmpty():
            self._enable(False)
            self.window.set_player_loaded(False)
            self.loaded_path = None
            self.cues = []
            self.starts = []
            self.active = None
            self.window.set_transcript([])
            self.window.player_title_label.setText(tr("No audio loaded"))
            self.window.player_detail_label.setText(tr("Open a session in Library"))
            self._duration(0)
            self._position(0)
            return
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.EndOfMedia,
        ):
            self.window.set_player_loaded(True)
            self._enable(True)
            self.window.player_detail_label.setText(tr("Local MP3 · Sentence subtitles"))
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._position(self.media.duration())

    def _error(self, *_):
        self._enable(False)
        self.window.set_player_loaded(False)
        self.window.player_detail_label.setText(tr("Playback failed"))
        messages = {
            QMediaPlayer.Error.ResourceError: (
                "The local audio file could not be read. Check that it exists and is accessible."
            ),
            QMediaPlayer.Error.FormatError: (
                "This audio format could not be decoded. Verify the session in Library again."
            ),
            QMediaPlayer.Error.NetworkError: (
                "The media backend could not access the audio. Reopen the local session in Library."
            ),
            QMediaPlayer.Error.AccessDeniedError: (
                "Access to the audio file was denied. Check the file's read permissions."
            ),
        }
        message = messages.get(
            self.media.error(),
            "Audio playback failed. Verify the session in Library and try again.",
        )
        self.window.show_error(tr("Unable to play audio"), tr(message))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            self.window.follow_check.setChecked(False)
        if watched is self.window.transcript_list and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.seek_cue(self.window.transcript_list.currentRow())
                return True
        return super().eventFilter(watched, event)
