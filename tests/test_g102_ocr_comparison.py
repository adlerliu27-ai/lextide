"""Carbon OCR image/text comparison. All inputs are synthetic and all worker paths are local."""

import hashlib
import inspect
import time
from threading import Event

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g8_generation as generation  # noqa: E402
import test_g1011_ocr_lifecycle as lifecycle  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
)
from test_g10_partial_review_gui import partial  # noqa: E402

from lextide.desktop import controller, ocr_ui  # noqa: E402
from lextide.desktop.i18n import tr  # noqa: E402
from lextide.desktop.ocr_preview import PREVIEW_EDGE, ImageView, read_preview  # noqa: E402

desktop = generation.desktop
post_dialog_action = lifecycle.post_dialog_action


def png(path, *, width=2400, height=300, color=Qt.GlobalColor.darkCyan):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(color)
    assert image.save(str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def button(widget, source):
    return next(b for b in widget.findChildren(QPushButton) if b.text() == tr(source))


@pytest.mark.parametrize("extension", [".jpg", ".png"])
def test_decode_bounds_and_input_identity(tmp_path, extension):
    path = tmp_path / ("source" + extension)
    digest = png(path)
    preview, original = read_preview(path, digest, Event())
    assert (preview.width(), preview.height()) == (PREVIEW_EDGE, 200)
    assert (original.width(), original.height()) == (2400, 300)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    png(path, color=Qt.GlobalColor.red)
    assert read_preview(path, digest, Event())[0].isNull()


@pytest.mark.parametrize("error", ["gone", "bad_decode", "cancelled"])
def test_preview_failure_is_not_a_review_result(tmp_path, error):
    path = tmp_path / "source.png"
    digest = png(path)
    cancel = Event()
    if error == "gone":
        path.unlink()
    elif error == "bad_decode":
        path.write_bytes(b"Not a PNG")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        cancel.set()
    assert read_preview(path, digest, cancel)[0].isNull()


@pytest.mark.parametrize("end", ["accept", "cancel", "cancel_hidden", "close"])
def test_review_controls_preserve_edited_text_and_checkbox(
    desktop, tmp_path, post_dialog_action, end
):
    w, c, requests, starts, errors = desktop
    path = tmp_path / "fixture.png"
    digest = png(path)
    before = w.speech_edit.toPlainText()
    c._import_image(str(path), options={"language": "mixed", "provider": "tesseract"})

    def interact():
        dialog = QApplication.activeModalWidget()
        image = dialog.findChild(ImageView)
        deadline = time.monotonic() + 4
        while image.item.pixmap().isNull() and time.monotonic() < deadline:
            QTest.qWait(10)
        assert image.item.pixmap().width() == 1600
        edit = dialog.findChild(QPlainTextEdit)
        check = dialog.findChild(QCheckBox)
        ok = button(dialog, "Use checked text")
        assert not ok.isEnabled()
        edit.setPlainText("My corrected text.")
        check.click()
        assert ok.isEnabled()
        for action in ["100%", "−", "+", "Fit", "Hide image"]:
            button(dialog, action).click()
            assert check.isChecked() and edit.toPlainText() == "My corrected text."
        assert not image.isVisible() and edit.isVisible()
        if end == "cancel_hidden":
            dialog.reject()
            return
        button(dialog, "Show image").click()
        assert image.isVisible() and check.isChecked()
        for width, direction in [(735, Qt.Orientation.Vertical), (920, Qt.Orientation.Horizontal)]:
            dialog.resize(width, 690)
            QTest.qWait(1)
            assert dialog.findChild(QSplitter).orientation() == direction
            assert edit.isVisible() and ok.isVisible() and ok.isEnabled()
        edit.insertPlainText("More. ")
        assert not check.isChecked() and not ok.isEnabled()
        if end == "accept":
            check.click()
            ok.click()
        elif end == "close":
            dialog.close()
        else:
            dialog.reject()

    post_dialog_action(interact)
    requests[0][1](partial() | {"source_sha256": digest})
    assert w._ocr_preview_source is None and c.ocr_request is None and not starts and not errors
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert w.speech_edit.toPlainText() == (
        "More. My corrected text." if end == "accept" else before
    )


def test_stale_and_clipboard_paths_are_request_scoped(desktop, monkeypatch, tmp_path):
    w, c, requests, starts, errors = desktop
    options = {"provider": "tesseract", "language": "mixed"}
    seen = []
    assert tuple(inspect.signature(ocr_ui.review_partial_ocr).parameters) == (
        "parent",
        "text",
        "options",
    )

    def review(window, text, opts):
        assert window is w and opts == options
        source = window._ocr_preview_source
        path, digest = source.path, source.sha256
        assert source.languages == ("chi_sim", "eng")
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        seen.append(path)
        return None

    monkeypatch.setattr(controller, "review_partial_ocr", review)
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    old = partial() | {"source_sha256": png(first)}
    new = partial() | {"source_sha256": png(second, color=Qt.GlobalColor.blue)}
    c._import_image(str(first), options=options)
    c.cancel_image_ocr()
    monkeypatch.setattr(c.image_stager, "start", lambda *_: None)

    class OwnedStage:
        def cleanup(self):
            second.unlink()

    c._import_image(snapshot=object(), options=options)
    c._clipboard_staged(c.ocr_request, OwnedStage(), str(second))
    requests[0][1](old)
    assert not seen
    requests[1][1](new)
    requests[0][1](old)
    assert seen == [second] and first.is_file() and not second.exists()
    assert w._ocr_preview_source is None and not errors and not starts


def test_unavailable_source_keeps_review_and_gate(desktop, post_dialog_action, tmp_path):
    w, c, requests, *_ = desktop
    before = w.speech_edit.toPlainText()
    c._import_image(
        str(tmp_path / "gone.png"), options={"language": "mixed", "provider": "tesseract"}
    )
    data = partial()

    def interact():
        d = QApplication.activeModalWidget()
        image = d.findChild(ImageView)
        deadline = time.monotonic() + 4
        while image.isVisible() and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not image.isVisible()
        assert d.findChild(QPlainTextEdit).toPlainText() == data["text"]
        assert not button(d, "Use checked text").isEnabled()
        d.reject()

    post_dialog_action(interact)
    requests[0][1](data)
    assert w.speech_edit.toPlainText() == before


@pytest.mark.parametrize("operation", ["cancel_image_ocr", "shutdown"])
def test_external_cancel_dismisses_nested_options_and_preview(
    desktop, post_dialog_action, tmp_path, operation
):
    w, c, requests, *_ = desktop
    path = tmp_path / "image.png"
    digest = png(path)
    before = w.speech_edit.toPlainText()
    c._import_image(str(path), options={"language": "mixed", "provider": "tesseract"})

    def interact():
        dialog = QApplication.activeModalWidget()
        post_dialog_action(lambda: getattr(c, operation)())
        button(dialog, "Change language and retry").click()

    post_dialog_action(interact)
    requests[0][1](partial() | {"source_sha256": digest})
    assert QApplication.activeModalWidget() is None
    assert w._ocr_preview_source is None and w.speech_edit.toPlainText() == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
