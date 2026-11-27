"""OCR lifecycle through Studio, including real local recognition when installed."""

import subprocess
import time
from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g8_generation as g8  # noqa: E402
import test_g10_ocr_gui as ocr  # noqa: E402
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QDialog,
    QPlainTextEdit,
    QPushButton,
)
from test_g10_partial_review_gui import partial  # noqa: E402

from lextide.desktop import controller, image_ocr  # noqa: E402
from lextide.desktop.i18n import tr  # noqa: E402
from lextide.errors import StudioError  # noqa: E402

desktop = g8.desktop


def button(dialog, caption):
    return next(b for b in dialog.findChildren(QPushButton) if b.text() == tr(caption))


@pytest.fixture
def post_dialog_action():
    """Convert errors in Qt timer slots to pytest failures and unwind nested exec loops."""
    failures = []

    def abort_dialogs():
        window = QApplication.activeModalWidget()
        while isinstance(window, QDialog):
            parent = window.parentWidget()
            window.reject()
            window = parent

    def post(callback):
        def invoke():
            try:
                callback()
            except Exception as exc:
                failures.append(exc)
                abort_dialogs()

        QTimer.singleShot(0, invoke)

    timeout = QTimer()
    timeout.setSingleShot(True)

    def expired():
        failures.append(AssertionError("Timed out waiting for the OCR dialogs"))
        abort_dialogs()

    timeout.timeout.connect(expired)
    timeout.start(25_000)
    yield post
    timeout.stop()
    if failures:
        raise failures[0]


def test_old_result_and_error_after_newer_ocr(desktop, monkeypatch):
    w, c, requests, starts, errors = desktop
    ocr.select_image(monkeypatch, {"provider": "tesseract", "language": "mixed"})
    c.import_file()
    first, failed = requests[0][1], requests[0][2]["on_error"]
    c.cancel_image_ocr()
    c.import_file()
    latest_token = c.ocr_request
    draft = w.speech_edit.toPlainText()
    monkeypatch.setattr(controller, "review_partial_ocr", lambda *_: pytest.fail("stale review"))
    first(partial())
    failed("OCR_FAILED: superseded request")
    assert latest_token is not None and c.ocr_request is latest_token
    assert w.speech_edit.toPlainText() == draft and not errors
    newest = ocr.result() | {"text": "Newer local result."}
    requests[1][1](newest)
    first(partial())
    failed("OCR_FAILED: delayed error")
    assert c.provenance == newest
    assert w.speech_edit.toPlainText() == w.source_edit.toPlainText() == newest["text"]
    assert c.ocr_request is None and not starts and not errors


def test_window_shutdown_interrupts_owned_ocr_and_ignores_late_completion(desktop, monkeypatch):
    w, c, requests, starts, errors = desktop
    before = w.speech_edit.toPlainText()
    worker = Mock(request={"command": "image_ocr"})
    c.workers.add(worker)

    def request(payload, callback, **kwargs):
        requests.append((payload, callback, kwargs))
        return worker

    monkeypatch.setattr(c, "_request", request)
    ocr.select_image(monkeypatch, {"provider": "tesseract", "language": "mixed"})
    c.import_file()
    c.shutdown()
    worker.interrupt.assert_called_once_with()
    _, done, options = requests[0]
    done(partial())
    options["on_error"]("OCR_FAILED: delayed error")
    c._worker_finished(worker)
    assert not c.workers and w.close_allowed
    assert w.speech_edit.toPlainText() == before and not starts and not errors


@pytest.mark.parametrize("cancel_options", [False, True])
def test_second_retry_click_cannot_start_another_worker(
    desktop, monkeypatch, post_dialog_action, cancel_options
):
    w, c, requests, starts, errors = desktop
    ocr.select_image(monkeypatch, {"provider": "tesseract", "language": "mixed"})
    c.import_file()
    first_done = requests[0][1]
    recorded = []
    before = w.speech_edit.toPlainText()

    def choose_language():
        options = QApplication.activeModalWidget()
        language = options.findChild(QComboBox)
        language.setCurrentIndex(language.findData("en"))
        # Deliver the second click to the original window while options are modal.
        # Window-level input uses Qt's modal dispatcher, unlike button.click().
        parent = options.parentWidget()
        retry = button(parent, "Change language and retry")
        center = retry.mapTo(parent, retry.rect().center())
        QTest.mouseClick(parent.windowHandle(), Qt.MouseButton.LeftButton, pos=center)
        recorded.append(QApplication.activeModalWidget() is options)
        first_done(partial())  # Duplicate worker result during the nested event loop.
        if cancel_options:
            options.reject()
        else:
            button(options, "Discard edits and recognize again").click()

    def retry():
        review = QApplication.activeModalWidget()
        edit = review.findChild(QPlainTextEdit)
        edit.setPlainText("Keep my corrections.")
        post_dialog_action(choose_language)
        button(review, "Change language and retry").click()

        if cancel_options:
            assert edit.toPlainText() == "Keep my corrections."
            review.reject()

    post_dialog_action(retry)
    first_done(partial())
    assert recorded == [True]
    if cancel_options:
        assert len(requests) == 1 and c.ocr_request is None
        assert w.speech_edit.toPlainText() == before and not starts and not errors
        return
    assert [payload["language"] for payload, *_ in requests] == ["mixed", "en"]
    requests[1][1](ocr.result() | {"text": "New English result."})
    assert w.speech_edit.toPlainText() == "New English result." and c.ocr_request is None
    assert not starts and not errors


def test_real_local_worker_recognizes_again_in_selected_language(
    desktop, monkeypatch, tmp_path, post_dialog_action
):
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    w, c, _, starts, errors = desktop
    try:
        tool = image_ocr._tool("tesseract")
    except StudioError:
        pytest.skip("Local Tesseract unavailable")
    langs = subprocess.check_output([tool, "--list-langs"], text=True, timeout=8).splitlines()
    if not {"eng", "chi_sim"} <= set(langs):
        pytest.skip("Local English and Simplified Chinese tessdata required")

    path = tmp_path / "english.png"
    expected = "We read this sentence aloud."
    image = QImage(900, 140, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    font = QFont("Helvetica")
    font.setPixelSize(42)
    painter.setFont(font)
    painter.drawText(40, 84, expected)
    painter.end()
    assert image.save(str(path))
    image_bytes = path.read_bytes()
    before = w.speech_edit.toPlainText()
    observed = []

    # Both passes use the production QProcess/worker and Tesseract. Drive the
    # same two dialogs the user sees; the file picker is outside this test.
    def interact(dialog, raw, options):
        def choose_english():
            choices = QApplication.activeModalWidget()
            combo = choices.findChild(QComboBox)
            combo.setCurrentIndex(combo.findData("en"))
            button(choices, "Discard edits and recognize again").click()

        def edit_and_retry():
            review = QApplication.activeModalWidget()
            edit = review.findChild(QPlainTextEdit)
            observed.append((edit.toPlainText(), w.speech_edit.toPlainText()))
            edit.setPlainText("Uncommitted correction.")
            post_dialog_action(choose_english)
            button(review, "Change language and retry").click()

        post_dialog_action(edit_and_retry)
        return review_partial(dialog, raw, options)

    review_partial = controller.review_partial_ocr
    monkeypatch.setattr(controller, "review_partial_ocr", interact)
    monkeypatch.delattr(c, "_request")  # Release the generation fixture's request recorder.
    c._import_image(str(path), options={"provider": "tesseract", "language": "mixed"})
    deadline = time.monotonic() + 20
    while (c.ocr_request is not None or c.workers) and time.monotonic() < deadline:
        QTest.qWait(20)
    assert c.ocr_request is None and not c.workers and not errors
    assert len(observed) == 1 and observed[0][0].strip() == expected and observed[0][1] == before
    assert w.source_edit.toPlainText().strip() == w.speech_edit.toPlainText().strip() == expected
    assert c.provenance["ocr"]["language"] == "en" and not starts
    assert path.read_bytes() == image_bytes and not list((c.root / "requests").glob("*.json"))
