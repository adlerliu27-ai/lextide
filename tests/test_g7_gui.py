"""Bilingual Listen is local presentation; English artifacts and TTS gates stay intact."""

import hashlib
import json

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtGui import QAccessible  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox  # noqa: E402

from lextide.desktop import bilingual_ui, controller, service  # noqa: E402
from lextide.desktop.bilingual import SubtitleSource  # noqa: E402
from lextide.desktop.bilingual_ui import (  # noqa: E402
    CHINESE_ROLE,
    TranslationEditDialog,
    TranslationPreviewDialog,
)
from lextide.desktop.i18n import configure_language, current_language, tr  # noqa: E402
from lextide.desktop.preferences import load_preferences, save_preferences  # noqa: E402
from lextide.desktop.window import MainWindow  # noqa: E402

SRT = (
    "1\n00:00:00,100 --> 00:00:01,000\nKeep the original English.\n\n"
    "2\n00:00:01,200 --> 00:00:03,000\nA second sentence stays linked.\n"
)


@pytest.fixture(params=["en", "zh_CN"])
def desktop(request, tmp_path, monkeypatch):
    configure_language(request.param)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    prefs = load_preferences(tmp_path)
    prefs["language"] = request.param
    save_preferences(tmp_path, prefs)
    monkeypatch.setattr(controller.Controller, "refresh_history", lambda *_: None)
    monkeypatch.setattr(controller.Controller, "refresh_engines", lambda *_: None)
    window = MainWindow()
    window.resize(980, 700)
    control = controller.Controller(window, tmp_path)
    monkeypatch.setattr(control.player.media, "setSource", lambda *_: None)
    window.show_error = lambda *args: pytest.fail(f"Unexpected modal error: {args}")
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
    job = control.root / "english-job"
    job.mkdir(exist_ok=True)
    (job / "subtitles.srt").write_text(SRT, encoding="utf-8")
    (job / "audio.mp3").write_bytes(b"unchanged audio fixture")
    (job / "manifest.json").write_text('{"preserve":true}')
    verified = {
        "srt_path": str(job / "subtitles.srt"),
        "audio_path": str(job / "audio.mp3"),
        "job_path": str(job),
        "cues": service.parse_srt(SRT),
    }
    control._verified(verified, "English session")
    return verified


def fingerprints(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}


def import_path(monkeypatch, root, text="保留英文原句。\n第二句保持对应。\n"):
    path = root / "translation.txt"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(bilingual_ui.QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
    return path


def accept_import(dialog):
    dialog.confirmation.setChecked(True)
    dialog.accept()
    return dialog.result()


def accept_edit(text):
    def accepted(dialog):
        dialog.editor.setPlainText(text)
        dialog.accept()
        return dialog.result()

    return accepted


def test_mode_defaults_and_validated_preferences_are_independent(tmp_path):
    assert load_preferences(tmp_path)["subtitle_mode"] == "english"
    (tmp_path / "preferences.json").write_text(
        json.dumps({"language": "zh_CN", "subtitle_mode": "bad"})
    )
    assert load_preferences(tmp_path)["subtitle_mode"] == "english"
    prefs = load_preferences(tmp_path)
    prefs["subtitle_mode"] = "bilingual"
    save_preferences(tmp_path, prefs)
    assert load_preferences(tmp_path)["subtitle_mode"] == "bilingual"
    assert load_preferences(tmp_path)["language"] == "zh_CN"


def test_unloaded_controls_and_mode_preserve_tts_approval(desktop):
    _, w, c = desktop
    assert not w.subtitle_import_button.isEnabled()
    assert not w.subtitle_edit_button.isEnabled()
    w.speech_edit.setPlainText("Exact reviewed English speech.")
    result = service.preview({"text": w.speech_edit.toPlainText(), "settings": c._settings()})
    c._prepared(result, c.revision)
    w.approval_check.setChecked(True)
    signature, settings = c._signature(), c._settings()
    language = current_language()
    w.subtitle_mode_combo.setCurrentIndex(1)
    assert c.preview == result and w.approval_check.isChecked()
    assert c._signature() == signature and c._settings() == settings
    assert current_language() == load_preferences(c.root)["language"] == language
    assert load_preferences(c.root)["subtitle_mode"] == "bilingual"
    assert not {"allow_online", "approved_sha256", "text"} & load_preferences(c.root).keys()


def test_full_preview_is_unchecked_and_plain_text(desktop):
    _, w, c = desktop
    verified = load_session(w, c)
    source = SubtitleSource.from_verified(verified)
    dialog = TranslationPreviewDialog(w, source, ("第一句 <b>原样</b>", "末句"))
    assert not dialog.confirmation.isChecked()
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    assert "Keep the original English." in dialog.preview.toPlainText()
    assert "A second sentence stays linked." in dialog.preview.toPlainText()
    assert "第一句 <b>原样</b>" in dialog.preview.toPlainText()
    assert "末句" in dialog.preview.toPlainText()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.confirmation.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_cancel_import_and_cancel_edit_write_nothing(desktop, monkeypatch):
    _, w, c = desktop
    load_session(w, c)
    import_path(monkeypatch, c.root)
    monkeypatch.setattr(TranslationPreviewDialog, "exec", lambda *_: QDialog.DialogCode.Rejected)
    c.bilingual.import_file()
    assert c.bilingual.store.current_revision(c.bilingual.source) is None
    w.set_active_cue(0)
    monkeypatch.setattr(TranslationEditDialog, "exec", lambda *_: QDialog.DialogCode.Rejected)
    c.bilingual.edit_selected()
    assert c.bilingual.store.current_revision(c.bilingual.source) is None
    assert not list(c.root.glob("translations/*.json"))


def test_import_save_reload_and_mode_preserve_cue_and_artifacts(desktop, monkeypatch):
    app, w, c = desktop
    verified = load_session(w, c)
    original = fingerprints(c.root / "english-job")
    source_texts = [w.transcript_list.item(i).text() for i in range(2)]
    import_path(monkeypatch, c.root)
    monkeypatch.setattr(TranslationPreviewDialog, "exec", accept_import)
    c.bilingual.import_file()
    assert c.bilingual.document.texts == ("保留英文原句。", "第二句保持对应。")
    w.set_active_cue(1)
    c.player.active = 1
    position = c.player.media.position()
    monkeypatch.setattr(c.player, "load", lambda *_: pytest.fail("Mode reloaded media"))
    for mode in (1, 0, 1):
        w.subtitle_mode_combo.setCurrentIndex(mode)
        app.processEvents()
        assert w.transcript_list.currentRow() == 1 and c.player.active == 1
        assert c.player.media.position() == position
    assert [w.transcript_list.item(i).text() for i in range(2)] == source_texts
    assert w.transcript_list.item(1).data(Qt.ItemDataRole.UserRole) == 1
    c.bilingual.load(verified)
    assert c.bilingual.document.texts == ("保留英文原句。", "第二句保持对应。")
    assert fingerprints(c.root / "english-job") == original
    assert w.transcript_list.currentRow() == 1


def test_subtitle_mode_updates_screen_reader_text_without_losing_translations(desktop):
    app, w, c = desktop
    load_session(w, c)
    interface = QAccessible.queryAccessibleInterface(w.transcript_list)
    for chinese in ("原有译文。", "修改后的译文。"):
        w.set_chinese_translations((chinese, None))
        for mode in ("english", "bilingual", "english"):
            w.subtitle_mode_combo.setCurrentIndex(w.subtitle_mode_combo.findData(mode))
            app.processEvents()
            for index in range(2):
                item = w.transcript_list.item(index)
                expected = item.text()
                if mode == "bilingual" and index == 0:
                    expected += f"\n{chinese}"
                assert item.data(Qt.ItemDataRole.AccessibleTextRole) == expected
                assert interface.child(index).text(QAccessible.Text.Name) == expected
            assert w.transcript_list.item(0).data(CHINESE_ROLE) == chinese
            assert w.transcript_list.item(1).data(CHINESE_ROLE) is None


def test_single_cue_edit_allows_partial_and_rejects_blank(desktop, monkeypatch):
    _, w, c = desktop
    verified = load_session(w, c)
    w.set_active_cue(1)
    dialog = TranslationEditDialog(w, c.bilingual.source.cues[1], None)
    assert dialog.english.isReadOnly()
    dialog.editor.setPlainText(" \n ")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    monkeypatch.setattr(TranslationEditDialog, "exec", accept_edit("第二句的新译文。"))
    c.bilingual.edit_selected()
    assert c.bilingual.document.texts == (None, "第二句的新译文。")
    assert w.transcript_list.currentRow() == 1
    c.bilingual.load(verified)
    assert c.bilingual.document.texts == (None, "第二句的新译文。")


@pytest.mark.parametrize("changed", ["source", "translation"])
def test_save_rechecks_source_and_sidecar_after_preview(desktop, monkeypatch, changed):
    _, w, c = desktop
    load_session(w, c)
    import_path(monkeypatch, c.root)
    source = c.bilingual.source

    def mutate_then_confirm(dialog):
        if changed == "source":
            source.path.write_text(SRT.replace("Keep the original", "Changed the original"))
        else:
            c.bilingual.store.save(
                source, ("其他窗口译文。", None), expected_revision=None, origin="manual"
            )
        return accept_import(dialog)

    monkeypatch.setattr(TranslationPreviewDialog, "exec", mutate_then_confirm)
    c.bilingual.import_file()
    assert c.bilingual.document is None
    assert w.subtitle_status_label.text() == tr(
        "English subtitles changed. Reopen and verify the session before saving Chinese."
        if changed == "source"
        else "Chinese subtitles changed elsewhere. Reopen the session and review them again."
    )
    if changed == "translation":
        assert c.bilingual.store.load(source).texts == ("其他窗口译文。", None)
    else:
        assert not list(c.root.glob("translations/*.json"))


def test_corrupt_sidecar_and_import_failure_keep_english(desktop, monkeypatch):
    _, w, c = desktop
    verified = load_session(w, c)
    saved = c.bilingual.store.save(
        c.bilingual.source, ("旧译文。", None), expected_revision=None, origin="manual"
    )
    assert saved.revision
    path = next(c.root.glob("translations/*.json"))
    path.write_text("corrupt")
    c.bilingual.load(verified)
    w.set_active_cue(0)
    assert c.bilingual.corrupt and c.bilingual.document is None
    assert w.subtitle_import_button.isEnabled() and not w.subtitle_edit_button.isEnabled()
    assert w.transcript_list.item(0).text().endswith("Keep the original English.")
    assert w.transcript_list.item(0).data(CHINESE_ROLE) is None
    import_path(monkeypatch, c.root, "仅一行\n")
    c.bilingual.import_file()
    assert path.read_text() == "corrupt"
    assert "UTF-8" in w.subtitle_status_label.text()


def test_failed_edit_save_preserves_existing_translation(desktop, monkeypatch):
    _, w, c = desktop
    load_session(w, c)
    w.set_active_cue(0)
    monkeypatch.setattr(TranslationEditDialog, "exec", accept_edit("原有译文。"))
    c.bilingual.edit_selected()
    old = c.bilingual.document
    monkeypatch.setattr(TranslationEditDialog, "exec", accept_edit("尚未保存。"))

    def fail(*args, **kwargs):
        raise OSError("private path details")

    monkeypatch.setattr(c.bilingual.store, "save", fail)
    c.bilingual.edit_selected()
    assert c.bilingual.document == old
    assert w.transcript_list.item(0).data(CHINESE_ROLE) == "原有译文。"
    assert "private path" not in w.subtitle_status_label.text()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_bilingual_delegate_wraps_and_missing_translation_has_no_gap(desktop, theme):
    app, w, c = desktop
    load_session(w, c)
    w.set_theme(theme)
    w.set_chinese_translations(("中文译文应当自动换行且完整显示，末尾文字不可裁切。" * 3, None))
    for size in (12, 18, 40):
        w.font_combo.setCurrentIndex(w.font_combo.findData(size))
        w.subtitle_mode_combo.setCurrentIndex(0)
        app.processEvents()
        english = [
            w.transcript_list.visualItemRect(w.transcript_list.item(i)).height() for i in range(2)
        ]
        w.subtitle_mode_combo.setCurrentIndex(1)
        app.processEvents()
        bilingual = [
            w.transcript_list.visualItemRect(w.transcript_list.item(i)).height() for i in range(2)
        ]
        assert bilingual[0] > english[0]
        assert bilingual[1] == english[1]
        assert w.transcript_list.horizontalScrollBar().maximum() == 0
        delegate = w.transcript_list.itemDelegate()
        assert delegate.chinese_font(w.transcript_list.font()).pixelSize() == round(size * 0.8)
