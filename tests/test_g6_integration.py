"""Local presentation and consent tests; never call a TTS service."""

import asyncio
import hashlib
import json

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
)

from lextide.desktop import controller, service  # noqa: E402
from lextide.desktop.i18n import (  # noqa: E402
    TRANSLATIONS,
    _fields,
    configure_language,
    current_language,
    tr,
)
from lextide.desktop.messages import localize_error  # noqa: E402
from lextide.desktop.preferences import load_preferences, save_preferences  # noqa: E402
from lextide.desktop.window import MainWindow  # noqa: E402
from lextide.errors import StudioError  # noqa: E402


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
    w = MainWindow()
    c = controller.Controller(w, tmp_path)
    errors = []
    w.show_error = lambda title, message: errors.append(message)
    w.show()
    app.processEvents()
    yield app, w, c, errors
    c.poll_timer.stop()
    c.player.media.stop()
    w.close_allowed = True
    w.close()
    w.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    configure_language("en")


def review(w, c, text="A quiet sentence for a local test."):
    w.speech_edit.setPlainText(text)
    preview = service.preview({"text": text, "settings": c._settings()})
    c._prepared(preview, c.revision)
    w.approval_check.setChecked(True)
    return preview


def test_language_and_size_only_change_preferences(desktop):
    _, w, c, _ = desktop
    preview = review(w, c)
    signature = c._signature()
    settings = c._settings()
    language = current_language()
    w.language_combo.setCurrentIndex(
        w.language_combo.findData("zh_CN" if language == "en" else "en")
    )
    w.font_combo.setCurrentIndex(w.font_combo.findData(28))
    c.save_settings()
    assert current_language() == language  # Restart-required; no partial live translation.
    assert "Restart" in w.language_hint.text() or "重启" in w.language_hint.text()
    assert load_preferences(c.root)["language"] != language
    assert load_preferences(c.root)["transcript_font_size"] == 28
    assert c._signature() == signature and c._settings() == settings
    assert c.preview == preview and w.approval_check.isChecked()
    assert not {"allow_online", "approved_sha256", "text"} & load_preferences(c.root).keys()


def test_persistence_on_new_window(desktop, monkeypatch):
    _, w, c, _ = desktop
    w.font_combo.setCurrentIndex(w.font_combo.findData(40))
    w.language_combo.setCurrentIndex(w.language_combo.findData("zh_CN"))
    c.save_settings()
    configure_language(load_preferences(c.root)["language"])
    other = MainWindow()
    restored = controller.Controller(other, c.root)
    assert other.font_combo.currentData() == 40
    assert other.language_combo.currentData() == "zh_CN"
    assert other.nav_buttons["Studio"].text().strip() != "Studio"
    restored.poll_timer.stop()
    restored.player.media.stop()
    other.close_allowed = True
    other.close()
    other.deleteLater()


def test_chinese_and_english_consent_dialog(desktop):
    _, w, _, _ = desktop
    text = "**First**, keep this exact English sentence."
    sha = hashlib.sha256(text.encode()).hexdigest()
    observed = []

    def inspect():
        dialog = next(
            x for x in QApplication.topLevelWidgets() if isinstance(x, QDialog) and x.isVisible()
        )
        check = dialog.findChild(QCheckBox)
        buttons = dialog.findChild(QDialogButtonBox)
        editor = dialog.findChild(QPlainTextEdit)
        assert editor.toPlainText() == text
        assert not check.isChecked()
        assert not buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        observed.append(True)
        dialog.reject()

    QTimer.singleShot(10, inspect)
    assert not controller.confirm_transmission(
        w, text, {"voice": "en-US-GuyNeural", "rate": "-4%"}, sha
    )
    assert observed == [True]


def test_no_permission_no_start_and_edit_revokes(desktop, monkeypatch):
    _, w, c, _ = desktop
    review(w, c)
    calls = []
    monkeypatch.setattr(c, "_start_synthesis", calls.append)
    monkeypatch.setattr(controller, "confirm_transmission", lambda *_: False)
    c.generate()
    assert calls == []
    w.speech_edit.insertPlainText("x")
    assert not w.approval_check.isChecked() and c.preview is None
    c.generate()
    assert calls == []


def test_clean_edit_preview_approved_snapshot(desktop, monkeypatch):
    _, w, c, _ = desktop
    source = "Learning Python is useful. 学习 Python 很有用。\n\nPractice every day.\n每天练习。"
    w.source_edit.setPlainText(source)
    w.speech_edit.setPlainText(source)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    data = asyncio.run(service.dispatch({"command": "clean", "mode": "english", "text": source}))
    c._cleaned(data, c.revision, source, "english")
    assert w.source_edit.toPlainText() == source
    assert "学习" not in w.speech_edit.toPlainText()
    assert c.preview is None and not w.approval_check.isChecked()
    edited = w.speech_edit.toPlainText() + "\nThis sentence was edited by the user."
    reviewed = review(w, c, edited)
    calls = []
    monkeypatch.setattr(c, "_start_synthesis", calls.append)
    monkeypatch.setattr(controller, "confirm_transmission", lambda *_: True)
    c.generate()
    assert calls[0]["text"] == reviewed["text"] == edited
    assert calls[0]["approved_sha256"] == hashlib.sha256(edited.encode()).hexdigest()
    original, record = service._provenance(calls[0], edited)
    assert original == source
    assert record["text_preparation"]["cleaned_text"] == data["cleaned_text"]
    assert record["text_preparation"]["version"] == "english-rules-v1"
    assert record["reviewed_sha256"] == calls[0]["approved_sha256"]


@pytest.mark.parametrize("engine", ["edge-tts", "kokoro"])
def test_enhanced_cleaning_is_explicit_and_reviewed(desktop, monkeypatch, engine):
    _, w, c, _ = desktop
    assert w.cleaning_strength_combo.currentData() == "conservative"
    assert not w.cleaning_strength_combo.isEnabled()
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    assert w.cleaning_strength_combo.isEnabled()
    w.cleaning_strength_combo.setCurrentIndex(1)
    source = "In 1921, Lewis Terman began a study.1921年，Lewis Terman开展了一项研究。"
    w.source_edit.setPlainText(source)
    w.speech_edit.setPlainText(source)

    def request(payload, callback):
        assert payload["cleaning_mode"] == "remove_chinese_translations"
        callback(asyncio.run(service.dispatch(payload)))

    monkeypatch.setattr(c, "_request", request)
    c.apply_cleaning()
    assert w.source_edit.toPlainText() == source
    assert w.speech_edit.toPlainText() == "In 1921, Lewis Terman began a study."
    assert c.cleaning["cleaning_mode"] == "remove_chinese_translations"
    assert c.preview is None and not w.approval_check.isChecked()
    c.capabilities["kokoro"] = {"available": True}
    w.engine_combo.setCurrentIndex(w.engine_combo.findData(engine))
    edited = w.speech_edit.toPlainText() + " This is my final edit."
    preview = review(w, c, edited)
    calls = []
    monkeypatch.setattr(c, "_start_synthesis", calls.append)
    monkeypatch.setattr(controller, "confirm_transmission", lambda *_: True)
    c.generate()
    assert calls[0]["text"] == edited == preview["text"]
    assert calls[0]["approved_sha256"] == hashlib.sha256(edited.encode()).hexdigest()
    assert calls[0]["allow_online"] is (engine == "edge-tts")
    original, record = service._provenance(calls[0], edited)
    assert original == source
    preparation = record["text_preparation"]
    assert preparation["version"] == "english-translations-v1"
    assert preparation["cleaning_mode"] == "remove_chinese_translations"
    assert preparation["reviewed_sha256"] == calls[0]["approved_sha256"]


def test_changing_cleaning_strength_preserves_edits_and_revokes_approval(desktop):
    _, w, c, errors = desktop
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    preview = review(w, c)
    revision = c.revision
    w.cleaning_strength_combo.setCurrentIndex(1)
    assert w.speech_edit.toPlainText() == preview["text"]
    assert c.preview is None and not w.approval_check.isChecked()
    c._cleaned({}, revision, "Old source", "english", "conservative")
    assert errors and w.speech_edit.toPlainText() == preview["text"]


def test_enhanced_mode_does_not_overwrite_manual_edits_without_confirmation(desktop, monkeypatch):
    _, w, c, _ = desktop
    w.source_edit.setPlainText("Original sentence.原始句子。")
    w.speech_edit.setPlainText("My carefully edited sentence.")
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    w.cleaning_strength_combo.setCurrentIndex(1)
    monkeypatch.setattr(
        controller.QMessageBox, "question", lambda *_: controller.QMessageBox.StandardButton.No
    )
    monkeypatch.setattr(c, "_request", lambda *_: pytest.fail("Unexpected cleaner request"))
    c.apply_cleaning()
    assert w.speech_edit.toPlainText() == "My carefully edited sentence."


def test_enhanced_empty_result_is_blocked_and_labels_are_translated(desktop):
    _, w, c, _ = desktop
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    w.cleaning_strength_combo.setCurrentIndex(1)
    assert w.cleaning_strength_combo.currentText() == tr("Remove Chinese Translations")
    assert w.cleaning_strength_combo.accessibleName() == tr("Cleaning Mode")
    source = "1921年，Lewis Terman开展了一项研究。"
    data = asyncio.run(
        service.dispatch(
            {
                "command": "clean",
                "mode": "english",
                "text": source,
                "cleaning_mode": "remove_chinese_translations",
            }
        )
    )
    c._cleaned(data, c.revision, source, "english", "remove_chinese_translations")
    assert w.speech_edit.toPlainText() == "" and not w.generate_button.isEnabled()
    with pytest.raises(StudioError):
        service.preview({"text": w.speech_edit.toPlainText()})


@pytest.mark.parametrize(
    "strength, version",
    [
        ("conservative", "english-translations-v1"),
        ("remove_chinese_translations", "english-rules-v1"),
        ("unknown", "english-translations-v1"),
    ],
)
def test_preparation_profile_mismatch_is_rejected(strength, version):
    source = "Keep all words."
    cleaning = {
        "mode": "english",
        "cleaning_mode": strength,
        "version": version,
        "original_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "cleaned_text": source,
    }
    with pytest.raises(StudioError):
        service._provenance({"provenance": {"text": source, "cleaning": cleaning}}, source)


def test_clean_mode_never_silently_overwrites_edit(desktop, monkeypatch):
    _, w, c, _ = desktop
    w.source_edit.setPlainText("Original words. 原文。")
    w.speech_edit.setPlainText("My important edit.")
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    monkeypatch.setattr(
        controller.QMessageBox, "question", lambda *_: controller.QMessageBox.StandardButton.No
    )
    monkeypatch.setattr(
        c, "_request", lambda *_a, **_k: pytest.fail("request after rejecting replacement")
    )
    c.apply_cleaning()
    assert w.speech_edit.toPlainText() == "My important edit."
    assert w.source_edit.toPlainText() == "Original words. 原文。"


def test_preparation_preserves_exact_imported_original(desktop, monkeypatch):
    _, w, c, _ = desktop
    original = "First English line.\r\n第二行中文。\r\nFinal English line.\r\n"
    c.provenance = {"text": original, "source_type": "txt"}
    w.source_edit.setPlainText(original)
    w.speech_edit.setPlainText(original)
    assert w.source_edit.toPlainText() != original  # Qt normalizes line endings.
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))

    def request(payload, callback):
        assert payload["text"] == original
        callback(asyncio.run(service.dispatch(payload)))

    monkeypatch.setattr(c, "_request", request)
    c.apply_cleaning()
    assert c.provenance["text"] == original
    sha = hashlib.sha256(original.encode()).hexdigest()
    assert c.cleaning["original_sha256"] == sha
    text = w.speech_edit.toPlainText()
    raw, record = service._provenance(
        {"provenance": {**c.provenance, "cleaning": c.cleaning}}, text
    )
    assert raw == original and record["extracted_sha256"] == sha


def test_stale_cleaner_result_keeps_new_edit(desktop):
    _, w, c, errors = desktop
    source = "English text. 中文。"
    w.speech_edit.setPlainText(source)
    revision = c.revision
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    data = asyncio.run(service.dispatch({"command": "clean", "mode": "english", "text": source}))
    w.speech_edit.setPlainText("New edit.")
    c._cleaned(data, revision, source, "english")
    assert w.speech_edit.toPlainText() == "New edit." and errors


def test_empty_cleaning_cannot_preview_or_start(desktop):
    _, w, c, _ = desktop
    source = "只有中文。"
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    data = asyncio.run(service.dispatch({"command": "clean", "mode": "english", "text": source}))
    c._cleaned(data, c.revision, source, "english")
    assert not w.generate_button.isEnabled()
    with pytest.raises(StudioError):
        service.preview({"text": w.speech_edit.toPlainText()})


def test_kokoro_receives_edited_text_without_online_consent(desktop, monkeypatch):
    _, w, c, _ = desktop
    c.capabilities["kokoro"] = {"available": True}
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("kokoro"))
    preview = review(w, c, "Python is useful. This is my final edit.")
    monkeypatch.setattr(
        controller, "confirm_transmission", lambda *_: pytest.fail("online request")
    )
    calls = []
    monkeypatch.setattr(c, "_start_synthesis", calls.append)
    c.generate()
    assert calls[0]["allow_online"] is False
    assert calls[0]["text"] == preview["text"] and calls[0]["audio_filter"] == "on"


def test_missing_or_invalid_translation_falls_back(tmp_path):
    try:
        assert not configure_language("zh_CN", resource_dir=tmp_path)
        assert tr("Review {count} sentences", count=2) == "Review 2 sentences"
        (tmp_path / "bad.json").write_text("{broken")
        (tmp_path / "keys.json").write_text(
            json.dumps({"Review {count} sentences": "审核 {missing} 句"})
        )
        assert not configure_language("zh_CN", resource_dir=tmp_path)
        assert tr("Review {count} sentences", count=2) == "Review 2 sentences"
        configure_language("invalid")
        assert current_language() == "en"
    finally:
        configure_language("en")


def test_catalog_placeholders_are_complete():
    for path in TRANSLATIONS.glob("*.json"):
        for source, target in json.loads(path.read_text()).items():
            assert target and _fields(source) == _fields(target), (path.name, source)


def test_old_preferences_safe_defaults(tmp_path):
    (tmp_path / "preferences.json").write_text(
        '{"theme":"dark","language":"bad","transcript_font_size":true}'
    )
    prefs = load_preferences(tmp_path)
    assert (
        prefs["theme"] == "dark"
        and prefs["language"] == "en"
        and prefs["transcript_font_size"] == 18
    )


def test_backend_marker_diagnostic_keeps_numeric_location_only():
    configure_language("zh_CN")
    try:
        result = localize_error(
            "VALIDATION_FAILED: Sentence 2: Edge metadata does not match the reviewed text "
            "at character 42 (body_or_order_mismatch). Check for omitted or changed text "
            "or sentence order."
        )
        assert "第 2 句" in result and "42" in result and "正文" in result
        assert "Sentence" not in result
        assert "SECRET" not in localize_error("UNKNOWN_ERROR: SECRET PRIVATE BODY")
    finally:
        configure_language("en")
