"""Studio paste handles only an explicit event in its own editing context."""

import hashlib
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g8_generation as g8  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QMimeData, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QImage, QKeyEvent, QKeySequence, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lextide.desktop import clipboard, controller  # noqa: E402
from lextide.desktop.i18n import tr  # noqa: E402

desktop = g8.desktop


def paste_key():
    sequence = QKeySequence.keyBindings(QKeySequence.StandardKey.Paste)[0][0]
    return QKeyEvent(QEvent.Type.KeyPress, sequence.key(), sequence.keyboardModifiers())


def make_image():
    image = QImage(240, 120, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    return image


def provide(monkeypatch, mime):
    reads = []
    board = SimpleNamespace(mimeData=lambda: reads.append(True) or mime)
    monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: board))
    return reads


def settle(condition):
    until = time.monotonic() + 3
    while not condition() and time.monotonic() < until:
        QApplication.processEvents()
        time.sleep(0.01)
    assert condition()


def test_text_at_caret_replaces_only_selection_one_undo(desktop, monkeypatch):
    w, c, requests, starts, _ = desktop
    w.show_page("Studio")
    mime = QMimeData()
    mime.setText("blue")
    reads = provide(monkeypatch, mime)
    w.source_edit.setPlainText("Original wording.")
    w.speech_edit.setPlainText("Read red words.")
    cursor = w.speech_edit.textCursor()
    cursor.setPosition(5)
    cursor.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
    w.speech_edit.setTextCursor(cursor)
    w.speech_edit.setFocus()
    assert not reads
    QApplication.sendEvent(w.speech_edit, paste_key())
    assert len(reads) == 1
    assert w.speech_edit.toPlainText() == "Read blue words."
    assert w.source_edit.toPlainText() == "Original wording."
    assert not requests and not starts
    w.speech_edit.undo()
    assert w.speech_edit.toPlainText() == "Read red words."
    assert mime.text() == "blue"


def test_paste_on_empty_studio_creates_source_and_editable_preview(desktop, monkeypatch):
    w, c, requests, starts, _ = desktop
    w.show_page("Studio")
    w.source_edit.clear()
    w.speech_edit.clear()
    mime = QMimeData()
    mime.setText("Plain words.")
    provide(monkeypatch, mime)
    w.paste_source_button.click()
    assert w.source_edit.toPlainText() == w.speech_edit.toPlainText() == mime.text()
    assert c.provenance["text"] == mime.text() and not requests and not starts


def test_other_pages_modals_and_ordinary_inputs_retain_native_paste(desktop, monkeypatch):
    w, c, requests, starts, _ = desktop
    reads = provide(monkeypatch, QMimeData())
    w.show_page("Library")
    assert not c.paste_keys.eventFilter(w.library_search, paste_key())
    c.paste_clipboard()
    w.show_page("Studio")
    w.title_edit.setFocus()
    assert not c.paste_keys.eventFilter(w.title_edit, paste_key())
    assert not reads and not requests and not starts


@pytest.mark.parametrize(
    "kind", ["empty", "file-url", "file-path", "relative-image", "oversize-text"]
)
def test_status_instead_of_import_for_noncontent(desktop, monkeypatch, kind):
    w, c, requests, starts, errors = desktop
    w.show_page("Studio")
    mime = QMimeData()
    if kind == "file-url":
        mime.setUrls([QUrl.fromLocalFile("/example/test.png")])
        mime.setText("/example/test.png")
    elif kind == "file-path":
        mime.setText("/example/test.png")
    elif kind == "relative-image":
        mime.setText("my figure.png")
    elif kind == "oversize-text":
        mime.setText("x" * (1024 * 1024 + 1))
    provide(monkeypatch, mime)
    before = w.speech_edit.toPlainText()
    w.paste_source_button.click()
    assert not requests and not starts and before == w.speech_edit.toPlainText()
    assert (
        errors
        if kind == "oversize-text"
        else w.status_label.text()
        == tr(
            "Copy an image or plain text first."
            if kind == "empty"
            else "To open a copied file, use Import file."
        )
    )


@pytest.mark.parametrize("fmt", ["pixels", "png", "jpeg"])
def test_image_with_text_uses_one_local_worker_and_cleans_up(desktop, monkeypatch, fmt):
    w, c, requests, starts, errors = desktop
    w.show_page("Studio")
    mime = QMimeData()
    img = make_image()
    if fmt == "pixels":
        mime.setImageData(img)
    else:
        raw = QByteArray()
        buffer = QBuffer(raw)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert img.save(buffer, fmt.upper())
        mime.setData("image/" + fmt, raw)
    mime.setText("Do not read this alternate description.")
    original_formats = mime.formats()
    reads = provide(monkeypatch, mime)
    options = {"provider": "tesseract", "language": "mixed"}
    monkeypatch.setattr(controller, "choose_image_ocr", lambda *a, **k: options)
    w.speech_edit.setFocus()
    QApplication.sendEvent(w.speech_edit, paste_key())
    c.paste_clipboard()
    settle(lambda: len(requests) == 1)
    payload, done, _ = requests.pop()
    assert (
        payload["command"] == "image_ocr" and payload["provider"] == "tesseract" and len(reads) == 1
    )
    path = Path(payload["path"])
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    text = "One small moon. 小小的月亮。"
    done(
        {
            "text": text,
            "source_name": path.name,
            "source_path": str(path),
            "source_sha256": sha,
            "ocr": {
                "provider": "tesseract",
                "effective_languages": ["eng", "chi_sim"],
                "duration_ms": 30,
            },
        }
    )
    assert not path.parent.exists()
    assert c.provenance["source_path"] is None and c.provenance["source_sha256"] == sha
    assert not w.generate_button.isEnabled()
    assert text == w.speech_edit.toPlainText() and not errors
    assert (
        mime.formats() == original_formats
        and mime.text() == "Do not read this alternate description."
    )
    assert not starts and not requests


def test_cancel_pending_write_releases_late_snapshot_and_decline_preserves_draft(
    desktop, monkeypatch
):
    w, c, requests, starts, errors = desktop
    w.show_page("Studio")
    mime = QMimeData()
    mime.setImageData(make_image())
    provide(monkeypatch, mime)
    old = w.speech_edit.toPlainText()
    monkeypatch.setattr(controller, "choose_image_ocr", lambda *a, **k: None)
    w.paste_source_button.click()
    assert c.ocr_request is None and not requests
    monkeypatch.setattr(
        controller, "choose_image_ocr", lambda *a, **k: {"provider": "tesseract", "language": "en"}
    )
    held = []
    monkeypatch.setattr(c.image_stager, "start", lambda token, snap: held.append((token, snap)))
    c.paste_clipboard()
    c.cancel_image_ocr()
    token, snap = held.pop()
    folder, path = clipboard._stage(snap)
    c._clipboard_staged(token, folder, path)
    assert not os.path.exists(folder.name) and old == w.speech_edit.toPlainText()
    assert not requests and not starts and not errors


def test_bad_image_and_excess_pixels_are_rejected_before_recognition(desktop, monkeypatch):
    w, c, requests, starts, errors = desktop
    w.show_page("Studio")
    mime = QMimeData()
    mime.setData("image/png", QByteArray(b"damaged"))
    provide(monkeypatch, mime)
    monkeypatch.setattr(
        controller, "choose_image_ocr", lambda *a, **k: {"provider": "tesseract", "language": "en"}
    )
    c.paste_clipboard()
    settle(lambda: bool(errors))
    monkeypatch.setattr(clipboard, "MAX_PIXELS", 100)
    mime.clear()
    mime.setImageData(make_image())
    c.paste_clipboard()
    assert len(errors) == 2 and not requests and not starts


def test_pixels_take_priority_over_url_and_text():
    mime = QMimeData()
    mime.setImageData(make_image())
    mime.setUrls([QUrl.fromLocalFile("/example/image.png")])
    mime.setText("Alternative caption.")
    assert clipboard.read_mime(mime)[0] == "image"


@pytest.mark.parametrize(
    "text",
    [
        "I saved the chart as plot.png",
        "Use the image at ./images/plot.png",
        "The filename is a reference, not the sentence.",
    ],
)
def test_plain_prose_is_not_a_file_reference(text):
    mime = QMimeData()
    mime.setText(text)
    assert clipboard.read_mime(mime) == ("text", text)


def test_encoded_limit_checked_before_decode_and_after_encoding(monkeypatch):
    monkeypatch.setattr(clipboard, "MAX_SOURCE_BYTES", 8)
    mime = QMimeData()
    mime.setData("image/png", QByteArray(b"x" * 9))
    assert clipboard.read_mime(mime)[0] == "invalid"
    with pytest.raises(ValueError):
        clipboard._stage(("pixels", make_image()))


@pytest.mark.parametrize("outcome", ["missing-engine", "timeout", "cancel", "edit"])
def test_clipboard_failure_cancellation_and_edit_keep_original_draft(desktop, monkeypatch, outcome):
    w, c, requests, starts, errors = desktop
    w.show_page("Studio")
    old = w.speech_edit.toPlainText()
    mime = QMimeData()
    mime.setImageData(make_image())
    provide(monkeypatch, mime)
    monkeypatch.setattr(
        controller, "choose_image_ocr", lambda *a, **k: {"provider": "tesseract", "language": "en"}
    )
    interrupted = []

    def request(payload, callback, **kw):
        requests.append((payload, callback, kw))
        return SimpleNamespace(interrupt=lambda: interrupted.append(True))

    monkeypatch.setattr(c, "_request", request)
    c.paste_clipboard()
    settle(lambda: bool(requests))
    payload, done, kw = requests.pop()
    folder = Path(payload["path"]).parent
    if outcome in ("missing-engine", "timeout"):
        kw["on_error"](
            "OCR_UNAVAILABLE: Missing provider"
            if outcome == "missing-engine"
            else "WORKER_TIMEOUT: Timed out"
        )
    else:
        if outcome == "cancel":
            c.cancel_image_ocr()
            assert interrupted
        else:
            w.speech_edit.setPlainText("Keep these manual edits.")
        done(
            {
                "text": "Replacement text.",
                "ocr": {"provider": "tesseract", "effective_languages": ["eng"], "duration_ms": 10},
            }
        )
    assert w.speech_edit.toPlainText() == ("Keep these manual edits." if outcome == "edit" else old)
    assert not folder.exists() and not starts
    assert bool(errors) == (outcome != "cancel")
    assert c.ocr_request is None and c.clipboard_scratch is None


def test_paste_invalidates_pending_online_approval(desktop, monkeypatch):
    w, c, requests, starts, errors = desktop
    w.show_page("Studio")
    w.generate_button.click()
    pending = requests.pop()
    mime = QMimeData()
    mime.setText(" Another sentence.")
    provide(monkeypatch, mime)
    w.speech_edit.moveCursor(QTextCursor.MoveOperation.End)
    c.paste_clipboard()
    g8.complete(pending)
    assert not starts and c.pending_submission is None
    final = w.speech_edit.toPlainText()
    w.generate_button.click()
    g8.complete(requests.pop())
    assert starts[0]["text"] == final
    assert starts[0]["approved_sha256"] == hashlib.sha256(final.encode()).hexdigest()


def test_repeated_success_paste_does_not_reuse_worker_or_temporary_source(desktop, monkeypatch):
    w, c, requests, starts, errors = desktop
    w.show_page("Studio")
    mime = QMimeData()
    mime.setImageData(make_image())
    provide(monkeypatch, mime)
    monkeypatch.setattr(
        controller, "choose_image_ocr", lambda *a, **k: {"provider": "tesseract", "language": "en"}
    )
    paths = []
    for index in range(2):
        c.paste_clipboard()
        settle(lambda: bool(requests))
        payload, done, _ = requests.pop()
        paths.append(payload["path"])
        done(
            {
                "text": f"Example {index}.",
                "ocr": {"provider": "tesseract", "effective_languages": ["eng"], "duration_ms": 10},
            }
        )
        assert w.speech_edit.toPlainText() == f"Example {index}."
        assert c.provenance["source_path"] is None
        assert not Path(payload["path"]).parent.exists()
    assert paths[0] != paths[1] and not starts and not errors


def test_modal_ocr_editor_owns_its_paste_key(desktop, monkeypatch):
    w, c, requests, starts, errors = desktop
    w.show_page("Studio")
    reads = provide(monkeypatch, QMimeData())
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: w))
    assert not c.paste_keys.eventFilter(w.speech_edit, paste_key())
    assert not reads and not requests and not starts


def test_multiline_selection_uses_actual_newline_byte_count(desktop, monkeypatch):
    w, c, requests, starts, errors = desktop
    w.show_page("Studio")
    original = "A\nB" + "x" * (1024 * 1024 - 3)
    w.speech_edit.setPlainText(original)
    cursor = w.speech_edit.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
    w.speech_edit.setTextCursor(cursor)
    mime = QMimeData()
    mime.setText("CCCC")
    provide(monkeypatch, mime)
    c.paste_clipboard()
    assert errors and w.speech_edit.toPlainText() == original
    assert not starts and not requests


def test_invalid_unicode_clipboard_is_rejected_without_replacement(desktop, monkeypatch):
    w, c, requests, starts, errors = desktop
    w.show_page("Studio")
    provide(monkeypatch, QMimeData())
    monkeypatch.setattr(controller, "read_mime", lambda _: ("text", "\ud800"))
    original = w.speech_edit.toPlainText()
    c.paste_clipboard()
    assert errors and w.speech_edit.toPlainText() == original
    assert not starts and not requests
