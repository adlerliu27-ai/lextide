"""Studio paste routing and bounded, private image staging for the existing OCR worker."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QObject, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from .importing import MAX_SOURCE_BYTES

MAX_PIXELS = 24_000_000
MAX_RASTER_BYTES = 128 * 1024 * 1024


class StudioPasteKeys(QObject):
    """Claim the platform paste key only on the active Studio page and its nav button."""

    def __init__(self, window, callback):
        super().__init__(window)
        self.w = window
        self.callback = callback
        self.page = window.pages["Studio"]
        for target in (
            window,
            window.nav_buttons["Studio"],
            self.page,
            *self.page.findChildren(QWidget),
        ):
            target.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
        if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        if not event.matches(QKeySequence.StandardKey.Paste):
            return False
        if self.w.page_stack.currentWidget() != self.page or QApplication.activeModalWidget():
            return False
        focus = QApplication.focusWidget() or watched
        if focus not in (
            self.w,
            self.w.nav_buttons["Studio"],
            self.page,
        ) and not self.page.isAncestorOf(focus):
            return False
        if isinstance(
            focus, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)
        ) and focus not in (
            self.w.speech_edit,
            self.w.source_edit,
            self.w.subtitle_edit,
        ):
            return False
        event.accept()
        if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            self.callback()
        return True


def file_reference(text):
    """File paths in a clipboard are references, never implicitly readable speech."""
    lines = [s.strip().strip("'\"") for s in text.splitlines() if s.strip()]
    return bool(lines) and all(
        p.startswith(("file:", "/", "~/", "./", "../"))
        or (p.lower().endswith((".png", ".jpg", ".jpeg", ".tiff")) and len(p.split()) <= 3)
        or (len(p) > 2 and p[1:3] in (":\\", ":/"))
        for p in lines
    )


def read_mime(mime):
    """Snapshot one explicit paste. Do not retain the platform-owned QMimeData."""
    if mime is None:
        return "empty", None
    for fmt in ("image/png", "image/jpeg", "image/tiff"):
        if mime.hasFormat(fmt):
            data = mime.data(fmt)
            if not 0 < data.size() <= MAX_SOURCE_BYTES:
                return "invalid", None
            return "image", (fmt.split("/")[1], QByteArray(data))
    if mime.hasImage():
        image = mime.imageData()
        if isinstance(image, QPixmap):
            if image.width() * image.height() > MAX_PIXELS:
                return "invalid", None
            image = image.toImage()
        if not isinstance(image, QImage) or not _valid_image(image):
            return "invalid", None
        return "image", ("pixels", QImage(image))  # Implicitly shared, read-only snapshot.
    if mime.hasUrls():
        return "file", None
    if mime.hasText():
        text = mime.text()
        if file_reference(text):
            return "file", None
        if text:
            return "text", text
    return "empty", None


def _valid_image(image):
    return (
        not image.isNull()
        and image.width() * image.height() <= MAX_PIXELS
        and image.sizeInBytes() <= MAX_RASTER_BYTES
    )


def _stage(snapshot):
    fmt, data = snapshot
    if fmt != "pixels":
        if not 0 < data.size() <= MAX_SOURCE_BYTES:
            raise ValueError
        buffer = QBuffer()
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, fmt.encode("ascii"))
        size = reader.size()
        if (
            not size.isValid()
            or not 0 < size.width() * size.height() <= MAX_PIXELS
            or reader.imageCount() != 1
        ):
            raise ValueError
        image = reader.read()
    else:
        image = data
    if not _valid_image(image):
        raise ValueError
    folder = tempfile.TemporaryDirectory(prefix="lextide-paste-")  # 0700
    path = Path(folder.name) / ("Clipboard.jpg" if fmt == "jpeg" else "Clipboard.png")
    try:
        with open(path, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
            if fmt in ("png", "jpeg"):
                stream.write(memoryview(data))
        # Native Qt encoding avoids Python QIODevice virtual callbacks in a pool thread.
        # Raster/pixel caps bound encoding work; reject encoded output before OCR.
        if fmt not in ("png", "jpeg") and not image.save(str(path), "PNG"):
            raise ValueError
        if not 0 < path.stat().st_size <= MAX_SOURCE_BYTES:
            raise ValueError
    except BaseException:
        folder.cleanup()
        raise
    return folder, str(path)


class ImageStager(QObject):
    ready = Signal(object, object, object)

    def start(self, token, snapshot):
        def run():
            try:
                folder, path = _stage(snapshot)
            except (OSError, ValueError, MemoryError, RuntimeError):
                folder, path = None, None
            try:
                self.ready.emit(token, folder, path)
            except RuntimeError:  # The window may have closed while encoding.
                if folder is not None:
                    folder.cleanup()

        QThreadPool.globalInstance().start(run)
