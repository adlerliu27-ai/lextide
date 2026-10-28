"""Per-click consent, async race and settings tests; never call a TTS service."""

import hashlib

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lextide.desktop import controller, service  # noqa: E402
from lextide.desktop.i18n import configure_language, current_language, tr  # noqa: E402
from lextide.desktop.preferences import load_preferences, save_preferences  # noqa: E402
from lextide.desktop.window import MainWindow  # noqa: E402


@pytest.fixture(params=["en", "zh_CN"])
def desktop(tmp_path, monkeypatch, request):
    configure_language(request.param)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    monkeypatch.setattr(controller.Controller, "refresh_engines", lambda *_: None)
    monkeypatch.setattr(controller.Controller, "refresh_history", lambda *_: None)
    preferences = load_preferences(tmp_path)
    preferences["language"] = request.param
    save_preferences(tmp_path, preferences)
    w = MainWindow()
    c = controller.Controller(w, tmp_path)
    errors, requests, starts = [], [], []
    w.show_error = lambda *args: errors.append(args)
    monkeypatch.setattr(
        c, "_request", lambda payload, callback=None, **kw: requests.append((payload, callback, kw))
    )
    monkeypatch.setattr(c, "_start_synthesis", starts.append)
    c.capabilities = {
        "edge-tts": {"available": True, "voices": ["en-US-GuyNeural"], "rate_range": [-50, 100]},
        "kokoro": {"available": True, "voices": ["af_heart"], "rate_range": [-50, 100]},
    }
    w.speech_edit.setPlainText('The teacher said, "Practice every day."')
    yield w, c, requests, starts, errors
    c.poll_timer.stop()
    c.player.media.stop()
    w.close_allowed = True
    w.close()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    configure_language("en")


def complete(request):
    payload, callback, _ = request
    callback(service.preview(payload))


@pytest.mark.parametrize("engine", ["edge-tts", "kokoro"])
def test_one_explicit_click_captures_current_text(desktop, engine, monkeypatch):
    w, c, requests, starts, _ = desktop
    w.engine_combo.setCurrentIndex(w.engine_combo.findData(engine))
    text = w.speech_edit.toPlainText()
    assert not requests and not starts
    assert w.generate_button.isEnabled()
    assert w.generate_button.text() == tr(
        "Send to Edge && Generate" if engine == "edge-tts" else "Generate Audio"
    )
    monkeypatch.setattr(
        controller, "confirm_transmission", lambda *_: pytest.fail("duplicate dialog")
    )
    w.generate_button.click()
    assert not starts  # Local preview is not a TTS request.
    assert len(requests) == 1 and requests[0][0]["command"] == "preview"
    complete(requests[0])
    assert len(starts) == 1
    payload = starts[0]
    assert payload["text"] == text
    assert payload["approved_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert payload["allow_online"] is (engine == "edge-tts")
    assert service._authorize(payload, text, engine) is payload["allow_online"]
    assert not {"allow_online", "approved_sha256"} & load_preferences(c.root).keys()


@pytest.mark.parametrize("mutation", ["text", "rate", "voice", "output", "cancel", "close"])
def test_late_preview_cannot_reuse_old_click(desktop, mutation):
    w, c, requests, starts, _ = desktop
    w.generate_button.click()
    {
        "text": lambda: w.speech_edit.insertPlainText("Changed."),
        "rate": lambda: w.rate_spin.setValue(3),
        "voice": lambda: w.voice_combo.setCurrentText("en-US-JennyNeural"),
        "output": lambda: w.output_dir_edit.setText("/another/destination"),
        "cancel": c.cancel_preparation,
        "close": lambda: setattr(c, "closing", True),
    }[mutation]()
    complete(requests[0])
    assert not starts
    assert c.pending_submission is None


def test_duplicate_click_and_callback_are_consumed_once(desktop):
    w, c, requests, starts, _ = desktop
    w.generate_button.click()
    c.generate()
    assert len(requests) == 1
    complete(requests[0])
    complete(requests[0])
    assert len(starts) == 1


def test_local_normalization_requires_new_explicit_click(desktop):
    w, c, requests, starts, _ = desktop
    w.speech_edit.setPlainText("  Read this exact sentence.  ")
    w.generate_button.click()
    complete(requests[0])
    assert not starts
    assert w.speech_edit.toPlainText() == "Read this exact sentence."
    w.generate_button.click()
    complete(requests[1])
    assert starts[0]["text"] == w.speech_edit.toPlainText()


def test_forged_preview_hash_fails(desktop):
    w, c, requests, starts, errors = desktop
    w.generate_button.click()
    data = service.preview(requests[0][0])
    data["sha256"] = "0" * 64
    requests[0][1](data)
    assert not starts and errors


def test_offline_missing_does_not_upload_or_probe(desktop):
    w, c, requests, starts, _ = desktop
    c.capabilities["kokoro"]["available"] = False
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("kokoro"))
    w.generate_button.click()
    c.generate()
    assert not starts and not requests
    assert not w.generate_button.isEnabled()


def test_auto_late_probe_ignored_and_no_text_in_probe(desktop):
    w, c, requests, starts, _ = desktop
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("auto"))
    assert requests[-1][0] == {"command": "auto_probe", "runtime": c._runtime()}
    assert not w.generate_button.isEnabled()
    callback = requests[-1][1]
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("kokoro"))
    callback({"decision": {"engine": "edge-tts", "ready": True}, "engines": []})
    assert c._effective_engine() == "kokoro" and not starts


def test_auto_requires_explicit_compatible_voice_selection(desktop):
    w, c, requests, starts, _ = desktop
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("auto"))
    requests[-1][1](
        {
            "decision": {"engine": "kokoro", "ready": True, "reason": "Will use Kokoro offline"},
            "engines": [{"engine_id": "kokoro", **c.capabilities["kokoro"]}],
        }
    )
    assert w.voice_combo.currentText() == "en-US-GuyNeural"
    assert not w.generate_button.isEnabled()
    w.voice_combo.setCurrentText("af_heart")
    assert w.generate_button.isEnabled()
    assert not starts


def test_language_save_preserves_pending_consent_and_editor(desktop):
    w, c, requests, starts, _ = desktop
    w.generate_button.click()
    signature = c._signature()
    snapshot = c.pending_submission
    language = "zh_CN" if current_language() == "en" else "en"
    w.language_combo.setCurrentIndex(w.language_combo.findData(language))
    assert w.settings_save_button.isEnabled()
    c.save_settings()
    assert current_language() == language
    assert c._signature() == signature and c.pending_submission is snapshot
    assert not w.settings_save_button.isEnabled()
    assert w.nav_buttons["Studio"].text().strip() == tr("Studio")
    complete(requests[0])
    assert len(starts) == 1


def test_language_only_save_preserves_manual_output_and_pending_snapshot(desktop):
    w, c, requests, starts, errors = desktop
    settings_output = w.settings_output_dir_edit.text()
    studio_output = str(c.root / "manually selected export")
    # Typing in Studio does not also change the saved Settings default.
    w.output_dir_edit.setText(studio_output)
    assert studio_output != settings_output
    w.generate_button.click()
    assert len(requests) == 1 and not starts
    snapshot = c.pending_submission
    revision, signature = c.revision, c._signature()

    language = "zh_CN" if current_language() == "en" else "en"
    w.language_combo.setCurrentIndex(w.language_combo.findData(language))
    c.save_settings()

    assert current_language() == language
    assert w.output_dir_edit.text() == studio_output
    assert w.settings_output_dir_edit.text() == settings_output
    assert load_preferences(c.root)["out_dir"] == settings_output
    assert c.revision == revision and c._signature() == signature
    assert c.pending_submission is snapshot and c.confirming
    assert len(requests) == 1 and not starts and not errors

    complete(requests[0])
    assert len(starts) == 1
    assert starts[0]["out_dir"] == studio_output
    assert starts[0]["text"] == snapshot["text"]
    assert starts[0]["settings"] == snapshot["settings"]
    assert starts[0]["approved_sha256"] == snapshot["sha256"]
    assert c.pending_submission is None and not c.confirming


def test_save_failure_does_not_switch_language_or_mark_saved(desktop, monkeypatch):
    w, c, requests, starts, errors = desktop
    old = current_language()
    w.language_combo.setCurrentIndex(w.language_combo.findData("zh_CN" if old == "en" else "en"))

    def denied(*_):
        raise OSError("test")

    monkeypatch.setattr(controller, "save_preferences", denied)
    c.save_settings()
    assert current_language() == old
    assert w.settings_save_button.isEnabled() and errors
    assert not starts and not requests
