"""Image import explicitly invokes a local worker and preserves edits/consent."""

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g8_generation as g8  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QPushButton  # noqa: E402

from lextide.desktop import controller, ocr_ui, service  # noqa: E402
from lextide.desktop.i18n import tr  # noqa: E402

desktop = g8.desktop
SAMPLE = "Maya counted 1,500 birds.\n玛雅数过一千五百只鸟。"


def result():
    return {
        "text": SAMPLE,
        "source_name": "birds.PNG",
        "format": "png",
        "changes": [],
        "warnings": [],
        "ocr": {
            "provider": "tesseract",
            "requested_languages": ["chi_sim", "eng"],
            "effective_languages": ["chi_sim", "eng"],
            "duration_ms": 30,
        },
    }


def select_image(monkeypatch, options):
    monkeypatch.setattr(
        controller.QFileDialog, "getOpenFileName", lambda *_: ("/tmp/birds.PNG", "")
    )
    monkeypatch.setattr(controller, "choose_image_ocr", lambda *_args, **_kwargs: options)


@pytest.mark.parametrize("accept", [False, True])
def test_dialog_offers_local_provider_and_explicit_cancel(desktop, accept):
    w, *_ = desktop

    def respond():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        language, provider = dialog.findChildren(QComboBox)
        assert language.currentData() == "mixed" and provider.currentData() == "tesseract"
        language.setCurrentIndex(language.findData("zh"))
        provider.setCurrentIndex(provider.findData("apple-vision"))
        if accept:
            next(
                b for b in dialog.findChildren(QPushButton) if b.text() == tr("Recognize locally")
            ).click()
        else:
            dialog.reject()

    QTimer.singleShot(0, respond)
    options = ocr_ui.choose_image_ocr(w, has_draft=True)
    assert options == ({"language": "zh", "provider": "apple-vision"} if accept else None)


def test_decline_local_recognition_keeps_existing_draft(desktop, monkeypatch):
    w, c, requests, starts, _ = desktop
    select_image(monkeypatch, None)
    before = w.speech_edit.toPlainText()
    c.import_file()
    assert before == w.speech_edit.toPlainText() and not requests and not starts


@pytest.mark.parametrize("outcome", ["success", "cancel", "edit", "failure"])
def test_ocr_callbacks_cannot_submit_or_replace_late_edits(desktop, monkeypatch, outcome):
    w, c, requests, starts, errors = desktop
    old_text = w.speech_edit.toPlainText()
    interrupted = []
    worker = SimpleNamespace(interrupt=lambda: interrupted.append(True))

    def request(payload, callback, **kwargs):
        requests.append((payload, callback, kwargs))
        return worker

    monkeypatch.setattr(c, "_request", request)
    select_image(monkeypatch, {"provider": "tesseract", "language": "mixed"})
    c.import_file()
    assert len(requests) == 1 and not starts
    payload, completed, kwargs = requests.pop()
    assert payload == {
        "command": "image_ocr",
        "path": "/tmp/birds.PNG",
        "provider": "tesseract",
        "language": "mixed",
    }
    assert not w.ocr_progress.isHidden() and not w.generate_button.isEnabled()
    c.generate()
    assert not requests and not starts
    if outcome == "cancel":
        w.cancel_ocr_button.click()
        completed(result())
        kwargs["on_error"]("INTERRUPTED: Operation interrupted safely.")
        assert not errors and interrupted == [True]
        assert w.speech_edit.toPlainText() == old_text
    elif outcome == "edit":
        w.speech_edit.setPlainText("A newer draft.")
        completed(result())
        assert errors and w.speech_edit.toPlainText() == "A newer draft."
    elif outcome == "failure":
        kwargs["on_error"]("OCR_UNAVAILABLE: Missing provider")
        assert errors and w.speech_edit.toPlainText() == old_text
    else:
        completed(result())
        assert not errors
        assert w.source_edit.isReadOnly()
        assert w.speech_edit.toPlainText() == w.source_edit.toPlainText() == SAMPLE
        assert not w.generate_button.isEnabled()
        assert "tesseract" in w.ocr_diagnostic.text()
        assert not requests and not starts
        corrected = SAMPLE.replace("1,500 birds", "1,505 birds")
        w.speech_edit.setPlainText(corrected)
        w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
        clean, cleaned, _ = requests.pop()
        assert clean["text"] == corrected
        cleaned(asyncio.run(service.dispatch(clean)))
        c.apply_cleaning()  # Adopt the visible preview without another pass of the cleaner.
        assert not requests
        assert c.cleaning["input_text"] == corrected and c.provenance["text"] == SAMPLE
        assert c.cleaning["input_sha256"] == hashlib.sha256(corrected.encode()).hexdigest()
        assert c.cleaning["original_sha256"] == hashlib.sha256(SAMPLE.encode()).hexdigest()
        assert c.cleaning["positions_relative_to"] == "input_text"
        assert w.speech_edit.toPlainText() == "Maya counted 1,505 birds."
        assert w.source_edit.toPlainText() == SAMPLE
        final = "Maya counted many birds."
        w.speech_edit.setPlainText(final)
        assert not starts and not requests
        w.generate_button.click()
        g8.complete(requests.pop())
        assert starts[0]["text"] == final and starts[0]["allow_online"]
        assert starts[0]["approved_sha256"] == hashlib.sha256(final.encode()).hexdigest()
    assert w.ocr_progress.isHidden()
    assert not requests
