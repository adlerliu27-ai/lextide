"""Real Qt key dispatch; fake media state keeps this suite offline and deterministic."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lextide.desktop.i18n import configure_language
from lextide.desktop.player import LocalPlayer
from lextide.desktop.window import MainWindow


@pytest.fixture(params=["en", "zh_CN"])
def desktop(request, monkeypatch):
    configure_language(request.param)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    w = MainWindow()
    player = LocalPlayer(w)
    calls = []
    state = [QMediaPlayer.PlaybackState.PausedState]

    def change(name, value):
        calls.append(name)
        state[0] = value
        player._state(value)

    monkeypatch.setattr(player.media, "playbackState", lambda: state[0])
    monkeypatch.setattr(player.media, "mediaStatus", lambda: QMediaPlayer.MediaStatus.LoadedMedia)
    monkeypatch.setattr(
        player.media, "play", lambda: change("play", QMediaPlayer.PlaybackState.PlayingState)
    )
    monkeypatch.setattr(
        player.media, "pause", lambda: change("pause", QMediaPlayer.PlaybackState.PausedState)
    )
    player._enable(True)
    w.show_page("Listen")
    w.show()
    w.activateWindow()
    app.processEvents()
    yield app, w, player, calls
    w.close_allowed = True
    w.close()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    configure_language("en")


def focus(app, target):
    target.setFocus()
    app.processEvents()
    actual = QApplication.focusWidget()
    assert actual is target or actual is target.focusProxy()
    return actual


@pytest.mark.parametrize(
    "control",
    ["focus_check", "play_button", "transcript_list", "follow_check", "font_combo", "seek_slider"],
)
def test_space_routes_once_without_toggling_other_controls(desktop, control):
    app, w, _, calls = desktop
    target = getattr(w, control)
    if control == "transcript_list":
        w.set_transcript([{"start_ms": 0, "end_ms": 5000, "text": "A sample sentence."}])
    focus(app, target)
    before = (w.focus_check.isChecked(), w.follow_check.isChecked(), w.font_combo.currentIndex())
    for expected in ("play", "pause", "play", "pause"):
        QTest.keyClick(target, Qt.Key.Key_Space)
        assert calls[-1:] == [expected]
    assert calls == ["play", "pause", "play", "pause"]
    assert before == (
        w.focus_check.isChecked(),
        w.follow_check.isChecked(),
        w.font_combo.currentIndex(),
    )
    assert w.sidebar.isVisible()


def test_autorepeat_consumed_and_unloaded_disabled_safe(desktop):
    app, w, player, calls = desktop
    focus(app, w.focus_check)
    QTest.keyPress(w.focus_check, Qt.Key.Key_Space)
    for _ in range(5):
        for kind in (QEvent.Type.KeyRelease, QEvent.Type.KeyPress):
            QApplication.sendEvent(
                w.focus_check,
                QKeyEvent(kind, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ", True),
            )
    QTest.keyRelease(w.focus_check, Qt.Key.Key_Space)
    assert calls == ["play"]
    assert not w.focus_check.isChecked()
    for status in ("unloaded", "loading", "failed"):
        player._enable(False)
        QTest.keyClick(w.focus_check, Qt.Key.Key_Space)
        assert calls == ["play"], status
        assert not w.focus_check.isChecked()


@pytest.mark.parametrize(
    "factory", [QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox]
)
def test_editable_descendants_keep_native_space(desktop, factory):
    app, w, player, calls = desktop
    holder = QWidget(w.pages["Listen"])
    holder.setGeometry(10, 150, 250, 100)
    edit = factory(holder)
    edit.resize(200, 60)
    if isinstance(edit, QComboBox):
        edit.setEditable(True)
    holder.show()
    edit.show()
    target = edit.lineEdit() if isinstance(edit, (QSpinBox, QDoubleSpinBox, QComboBox)) else edit
    focus(app, edit)
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ")
    assert not player.listen_keys.eventFilter(target, key)
    QTest.keyClick(target, Qt.Key.Key_Space)
    assert not calls
    if not isinstance(edit, (QSpinBox, QDoubleSpinBox)):
        assert (target.text() if isinstance(target, QLineEdit) else target.toPlainText()) == " "


@pytest.mark.parametrize(
    "page,control",
    [("Studio", "speech_edit"), ("Library", "library_search"), ("Settings", "model_dir_edit")],
)
def test_other_pages_keep_text_input(desktop, page, control):
    app, w, _, calls = desktop
    w.show_page(page)
    target = getattr(w, control)
    target.setText("") if isinstance(target, QLineEdit) else target.setPlainText("")
    focus(app, target)
    QTest.keyClick(target, Qt.Key.Key_Space)
    assert not calls
    assert (target.text() if isinstance(target, QLineEdit) else target.toPlainText()) == " "


def test_modal_and_other_windows_not_intercepted(desktop):
    app, w, _, calls = desktop
    d = QDialog(w)
    d.setModal(True)
    layout = QVBoxLayout(d)
    button = QPushButton("Modal action", d)
    layout.addWidget(button)
    clicked = []
    button.clicked.connect(lambda: clicked.append(True))
    d.show()
    d.activateWindow()
    app.processEvents()
    focus(app, button)
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert clicked == [True] and not calls
    d.close()
    other = QLineEdit()
    other.show()
    other.activateWindow()
    app.processEvents()
    focus(app, other)
    QTest.keyClick(other, Qt.Key.Key_Space)
    assert other.text() == " " and not calls
    other.close()


def test_focus_shortcut_mouse_and_page_scope(desktop):
    app, w, _, calls = desktop
    focus(app, w.play_button)
    QTest.keySequence(w.play_button, QKeySequence("Ctrl+Shift+F"))
    assert w.focus_check.isChecked() and not w.sidebar.isVisible()
    assert not calls
    QTest.keyClick(w.play_button, Qt.Key.Key_Space)
    assert calls == ["play"] and w.focus_check.isChecked()
    QTest.keySequence(w.play_button, QKeySequence("Ctrl+Shift+F"))
    assert not w.focus_check.isChecked() and w.sidebar.isVisible()
    QTest.mouseClick(w.focus_check, Qt.MouseButton.LeftButton)
    assert w.focus_check.isChecked()
    assert w.focus_check.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert w.focus_check.accessibleDescription() and w.focus_check.toolTip()
    w.show_page("Studio")
    focus(app, w.speech_edit)
    QTest.keySequence(w.speech_edit, QKeySequence("Ctrl+Shift+F"))
    assert not w.focus_check.isChecked() and calls == ["play"]


def test_end_of_media_uses_existing_restart_behavior(desktop, monkeypatch):
    app, w, player, calls = desktop
    positions = []
    monkeypatch.setattr(player.media, "mediaStatus", lambda: QMediaPlayer.MediaStatus.EndOfMedia)
    monkeypatch.setattr(player.media, "setPosition", positions.append)
    focus(app, w.focus_check)
    QTest.keyClick(w.focus_check, Qt.Key.Key_Space)
    assert calls == ["play"] and positions == [0]


def test_popup_and_modified_space_keep_native_behavior(desktop):
    app, w, player, calls = desktop
    focus(app, w.font_combo)
    w.font_combo.showPopup()
    app.processEvents()
    popup = QApplication.activePopupWidget()
    assert popup is not None
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ")
    assert not player.listen_keys.eventFilter(popup, event)
    w.font_combo.hidePopup()
    focus(app, w.focus_check)
    modified = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier, " "
    )
    assert not player.listen_keys.eventFilter(w.focus_check, modified)
    assert not calls


def test_custom_input_method_child_and_text_viewport_are_excluded(desktop):
    from lextide.desktop.listen_keys import _text_input

    _, w, _, _ = desktop
    edit = QTextEdit(w.pages["Listen"])
    child = QWidget(edit.viewport())
    assert _text_input(child)
    custom = QWidget(w.pages["Listen"])
    custom.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
    assert _text_input(custom)
