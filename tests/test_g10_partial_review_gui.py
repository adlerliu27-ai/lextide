"""Partial output is quarantined until explicit image review, then normal TTS gates apply."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import test_g8_generation as g8
import test_g10_ocr_gui as ocr
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QPlainTextEdit, QPushButton

from lextide.desktop import controller, ocr_ui, service
from lextide.desktop.i18n import tr
from lextide.errors import StudioError
from lextide.longform import tasks

desktop = g8.desktop


def partial():
    return ocr.result() | {
        "text": "Partial English text.",
        "review_required": True,
        "review_reason": "OCR_LANGUAGE_MISMATCH",
        "ocr": {
            "provider": "tesseract",
            "language": "mixed",
            "requested_languages": ["chi_sim", "eng"],
            "effective_languages": ["chi_sim", "eng"],
            "duration_ms": 12,
            "confidence": None,
        },
    }


@pytest.mark.parametrize("action", ["accept", "retry", "cancel_retry", "cancel", "empty"])
def test_real_dialog_needs_explicit_checked_text(desktop, action):
    w, *_ = desktop

    def interact():
        d = QApplication.activeModalWidget()
        edit = d.findChild(QPlainTextEdit)
        check = d.findChild(QCheckBox)
        accept = next(b for b in d.findChildren(QPushButton) if b.text() == tr("Use checked text"))
        assert not check.isChecked() and not accept.isEnabled()
        edit.setPlainText("Corrected text.")
        assert not accept.isEnabled()
        check.setChecked(True)
        assert accept.isEnabled()
        edit.insertPlainText(" Changed.")
        assert not check.isChecked() and not accept.isEnabled()
        if action == "accept":
            check.setChecked(True)
            accept.click()
        elif action in ("retry", "cancel_retry"):

            def choose_language():
                options = QApplication.activeModalWidget()
                assert options.parent() is d
                if action == "cancel_retry":
                    options.reject()
                    return
                language = options.findChild(QComboBox)
                language.setCurrentIndex(language.findData("en"))
                next(
                    b
                    for b in options.findChildren(QPushButton)
                    if b.text() == tr("Discard edits and recognize again")
                ).click()

            QTimer.singleShot(0, choose_language)
            next(
                b
                for b in d.findChildren(QPushButton)
                if b.text() == tr("Change language and retry")
            ).click()
            if action == "cancel_retry":
                assert QApplication.activeModalWidget() is d
                assert edit.toPlainText() == " Changed.Corrected text."
                assert not accept.isEnabled()
                d.reject()
        else:
            if action == "empty":
                edit.clear()
                check.setChecked(True)
                assert not accept.isEnabled()
            d.reject()

    QTimer.singleShot(0, interact)
    result = ocr_ui.review_partial_ocr(
        w, "Partial text.", {"provider": "tesseract", "language": "mixed"}
    )
    assert (result is None) is (action in ("cancel", "empty", "cancel_retry"))
    if result:
        assert result["action"] == action
        if action == "retry":
            assert result["options"] == {"provider": "tesseract", "language": "en"}


@pytest.mark.parametrize(
    "action", ["accept", "cancel", "edit_old_draft", "retry", "external_cancel"]
)
def test_partial_controller_preserves_draft_until_checked(desktop, monkeypatch, action):
    w, c, requests, starts, errors = desktop
    before = w.speech_edit.toPlainText()
    interrupted = []
    worker = SimpleNamespace(interrupt=lambda: interrupted.append(True))
    monkeypatch.setattr(
        c,
        "_request",
        lambda payload, callback=None, **kw: requests.append((payload, callback, kw)) or worker,
    )
    ocr.select_image(monkeypatch, {"provider": "tesseract", "language": "mixed"})

    def review(parent, text, options):
        assert c.ocr_request is not None and not w.generate_button.isEnabled()
        assert w.speech_edit.toPlainText() == before
        c.generate()
        assert not starts and len(requests) == 1
        if action == "edit_old_draft":
            w.speech_edit.setPlainText("A newer draft.")
        if action == "external_cancel":
            c.cancel_image_ocr()
        return (
            None
            if action == "cancel"
            else {
                "action": "retry" if action == "retry" else "accept",
                "text": "Checked corrected text.",
                "options": options,
            }
        )

    monkeypatch.setattr(controller, "review_partial_ocr", review)
    c.import_file()
    callback = requests[0][1]
    data = partial()
    callback(data)
    if action == "retry":
        assert len(requests) == 2 and c.ocr_request is not None
        callback(data)  # Old completion cannot contaminate the retry.
        assert w.speech_edit.toPlainText() == before
        c.cancel_image_ocr()
    elif action in ("cancel", "external_cancel"):
        assert w.speech_edit.toPlainText() == before and c.provenance == {}
    elif action == "edit_old_draft":
        assert errors and w.speech_edit.toPlainText() == "A newer draft."
    else:
        assert c.provenance["text"] == w.source_edit.toPlainText() == data["text"]
        assert w.speech_edit.toPlainText() == "Checked corrected text."
        assert c.provenance["review_required"] is False
        original, record = service._provenance(
            {"provenance": c.provenance}, w.speech_edit.toPlainText()
        )
        assert original == data["text"]
        assert (
            record["source"]["ocr_review"]["corrected_sha256"]
            == hashlib.sha256(w.speech_edit.toPlainText().encode()).hexdigest()
        )
        w.speech_edit.setPlainText("English with 中文.")
        assert not w.generate_button.isEnabled()
    assert not starts
    assert c.ocr_request is None


@pytest.mark.parametrize(
    "tamper", ["pending", "missing_confirmation", "wrong_original", "wrong_corrected"]
)
def test_service_rejects_unreviewed_or_forged_partial_provenance(tamper):
    data = partial()
    if tamper != "pending":
        data["review_required"] = False
    if tamper in ("wrong_original", "wrong_corrected"):
        data["ocr_review"] = {
            "original_sha256": hashlib.sha256(data["text"].encode()).hexdigest(),
            "corrected_text": "Checked text.",
            "corrected_sha256": hashlib.sha256(b"Checked text.").hexdigest(),
        }
        data["ocr_review"][
            "original_sha256" if tamper == "wrong_original" else "corrected_sha256"
        ] = "0" * 64
    with pytest.raises(StudioError):
        service._provenance({"provenance": data}, "Checked text.")


def test_dispatch_explicitly_requests_partial_but_never_synthesizes(monkeypatch, tmp_path):
    from lextide.desktop import image_ocr

    calls = []

    async def recognize(path, **kwargs):
        calls.append((path, kwargs))
        return partial()

    monkeypatch.setattr(image_ocr, "recognize", recognize)
    result = asyncio.run(
        service.dispatch(
            {
                "command": "image_ocr",
                "path": str(tmp_path / "image.png"),
                "provider": "tesseract",
                "language": "mixed",
            }
        )
    )
    assert result["review_required"] is True and calls[0][1]["allow_partial"] is True


def test_gui_defense_blocks_pending_partial_even_if_latin(desktop):
    w, c, requests, starts, _ = desktop
    c.provenance = partial()
    w.speech_edit.setPlainText("A plausible but unchecked Latin result.")
    assert not w.generate_button.isEnabled()
    c.generate()
    assert not starts and not requests
    assert w.studio_status_label.text() == tr("Check the partial OCR result before using it.")


def test_finished_worker_can_be_cancelled_during_nested_review(desktop):
    _, c, _, starts, _ = desktop

    class FinishedWorker:
        def deleteLater(self):
            self.deleted = True

        def interrupt(self):
            raise AssertionError("Deleted worker cannot be interrupted")

    worker = FinishedWorker()
    token = object()
    c.ocr_worker, c.ocr_request = worker, token
    c.workers.add(worker)
    c._worker_finished(worker)
    assert worker.deleted and c.ocr_worker is None and c.ocr_request is token
    c.cancel_image_ocr()
    assert c.ocr_request is None and not starts


def test_confirmed_partial_clean_edit_and_send_use_final_sha(desktop, monkeypatch):
    w, c, requests, starts, _ = desktop
    data = partial()
    corrected = "Goodwin 博士: 我是 Fred Goodwin 博士。\nA corrected English sentence."
    data.update(
        review_required=False,
        ocr_review={
            "original_sha256": hashlib.sha256(data["text"].encode()).hexdigest(),
            "corrected_text": corrected,
            "corrected_sha256": hashlib.sha256(corrected.encode()).hexdigest(),
        },
    )
    c._imported(data, c.revision, reading_text=corrected)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    payload, callback, _ = requests.pop()
    callback(asyncio.run(service.dispatch(payload)))
    assert w.speech_edit.toPlainText() == "A corrected English sentence."
    w.speech_edit.setPlainText("A final reviewed English sentence.")
    c.generate()
    g8.complete(requests.pop())
    submitted = starts[0]
    assert submitted["text"] == w.speech_edit.toPlainText()
    assert submitted["approved_sha256"] == hashlib.sha256(submitted["text"].encode()).hexdigest()
    original, record = service._provenance(submitted, submitted["text"])
    assert (
        original == data["text"] and record["approved_tts_sha256"] == submitted["approved_sha256"]
    )

    # Exercise the real task/review writers up to the synthesis boundary.
    # This needs neither FFmpeg nor an online connection.
    monkeypatch.setattr(tasks, "resolve_media_tools", lambda *_: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(tasks, "_decoder_version", lambda *_: "offline-test")
    monkeypatch.setattr(service, "_run", AsyncMock(return_value={}))
    events = []
    out_dir = Path(submitted["out_dir"])
    assert out_dir.is_relative_to(c.root) and not list(out_dir.glob("task-*"))
    asyncio.run(service.start(submitted, events.append))
    task = Path(events[0]["task_path"])
    persisted = json.loads((task / "document-review.json").read_text())
    assert persisted == record
    assert (task / "source-extracted.txt").read_text() == data["text"]
    assert (task / "tts.txt").read_text() == submitted["text"]
    assert persisted["source"]["ocr_review"]["corrected_text"] == corrected


@pytest.mark.parametrize(
    "engine,voice", [("edge-tts", "en-US-GuyNeural"), ("kokoro", "am_michael")]
)
def test_service_start_rejects_pending_partial_before_engine_or_task(monkeypatch, engine, voice):
    def forbidden(*args, **kwargs):
        pytest.fail("unreviewed OCR reached synthesis or task creation")

    monkeypatch.setattr(service, "LongEngine", forbidden)
    monkeypatch.setattr(service, "create_task", forbidden)
    text = "Unchecked Latin output."
    payload = {
        "text": text,
        "settings": {"engine": engine, "voice": voice},
        "allow_online": engine == "edge-tts",
        "approved_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "provenance": partial(),
    }
    with pytest.raises(StudioError, match="partial OCR"):
        asyncio.run(service.start(payload, lambda _: None))


def test_clipboard_partial_retry_retains_captured_image_and_new_token(desktop, monkeypatch):
    w, c, requests, starts, _ = desktop
    before = w.speech_edit.toPlainText()
    captured = object()  # No clipboard read occurs during retry.
    staged = []
    monkeypatch.setattr(c.image_stager, "start", lambda token, image: staged.append((token, image)))
    monkeypatch.setattr(
        controller,
        "choose_image_ocr",
        lambda *_, **kw: {"provider": "tesseract", "language": "mixed"},
    )
    monkeypatch.setattr(
        controller, "review_partial_ocr", lambda *args: {"action": "retry", "options": args[2]}
    )
    worker = SimpleNamespace(interrupt=lambda: None)
    monkeypatch.setattr(
        c,
        "_request",
        lambda payload, callback=None, **kw: requests.append((payload, callback, kw)) or worker,
    )
    c._import_image(snapshot=captured)
    first_token = c.ocr_request
    cleaned = []
    c._clipboard_staged(
        first_token,
        SimpleNamespace(cleanup=lambda: cleaned.append("first")),
        "/tmp/private-first.png",
    )
    first_done = requests[0][1]
    first_done(partial())
    assert cleaned == ["first"] and c.ocr_request is not first_token
    assert len(staged) == 2 and all(image is captured for _, image in staged)
    first_done(partial())
    assert w.speech_edit.toPlainText() == before and not starts
    c.cancel_image_ocr()
    assert c.ocr_request is None


@pytest.mark.parametrize("code", ["OCR_TIMEOUT", "OCR_FAILED", "OCR_EMPTY"])
def test_local_recognition_error_does_not_offer_partial_review(desktop, monkeypatch, code):
    from lextide.desktop.image_ocr import ERRORS

    w, c, requests, starts, errors = desktop
    before = w.speech_edit.toPlainText()
    ocr.select_image(monkeypatch, {"provider": "tesseract", "language": "mixed"})
    monkeypatch.setattr(
        c,
        "_request",
        lambda payload, callback, **kw: requests.append((payload, callback, kw)),
    )
    monkeypatch.setattr(
        controller, "review_partial_ocr", lambda *_: pytest.fail("unexpected review")
    )
    c.import_file()
    _, callback, kw = requests.pop()
    kw["on_error"](f"{code}: {ERRORS[code]}")
    callback(partial())  # Even a late nonempty result cannot recover a failed attempt.
    assert errors == [(tr("Operation not completed"), f"{code}: {tr(ERRORS[code])}")]
    assert c.ocr_request is None and w.ocr_progress.isHidden()
    assert w.speech_edit.toPlainText() == before and not starts
