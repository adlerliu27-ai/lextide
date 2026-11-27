"""Request-scoped, display-only view of the image that produced a partial OCR result."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from PySide6.QtCore import (
    QBuffer,
    QIODevice,
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
    Slot,
)
from PySide6.QtGui import QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from .i18n import tr
from .importing import DocumentImportError, _read_source

PREVIEW_EDGE = 1600


@dataclass(frozen=True)
class ReviewImage:
    path: Path
    sha256: str
    languages: tuple[str, ...]


def read_preview(path: Path, digest: str, cancelled: Event) -> tuple[QImage, QSize]:
    """Use only the bounded bytes identified by the OCR worker; never reopen during decode."""
    unavailable = QImage(), QSize()
    if not digest or cancelled.is_set():
        return unavailable
    try:
        data = _read_source(path)
    except DocumentImportError:
        return unavailable
    if hashlib.sha256(data).hexdigest() != digest or cancelled.is_set():
        return unavailable
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    original = reader.size()
    if (
        bytes(reader.format()) not in (b"png", b"jpeg")
        or not original.isValid()
        or not 0 < original.width() * original.height() <= 24_000_000
        or reader.imageCount() != 1
    ):
        return unavailable
    if max(original.width(), original.height()) > PREVIEW_EDGE:
        target = original.scaled(PREVIEW_EDGE, PREVIEW_EDGE, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(target.expandedTo(QSize(1, 1)))
    image = reader.read()
    return (image, original) if not cancelled.is_set() and not image.isNull() else unavailable


class _PreviewResult(QObject):
    ready = Signal(QImage, QSize)


class _Decode(QRunnable):
    def __init__(self, source: ReviewImage, cancelled: Event):
        super().__init__()
        self.source = source
        self.cancelled = cancelled
        self.result = _PreviewResult()

    def run(self):
        image, size = read_preview(self.source.path, self.source.sha256, self.cancelled)
        if not self.cancelled.is_set():
            self.result.ready.emit(image, size)


class ImageView(QGraphicsView):
    """Scale a single bounded pixmap at paint time, including on Retina displays."""

    available = Signal(bool)

    def __init__(self, parent):
        super().__init__(parent)
        scene = QGraphicsScene(self)
        self.setScene(scene)
        self.item = QGraphicsPixmapItem()
        scene.addItem(self.item)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setAccessibleName(tr("Original OCR image"))
        self.setMinimumSize(90, 72)
        self.fit = True
        self.zoom = 1.0
        self.original = QSize()
        self.cancelled = Event()

    def load(self, source):
        work = _Decode(source, self.cancelled)
        work.result.ready.connect(self._loaded, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(work)

    @Slot(QImage, QSize)
    def _loaded(self, image, original):
        if self.cancelled.is_set():
            return
        if not image.isNull():
            self.original = original
            self.item.setPixmap(QPixmap.fromImage(image))
            self.setSceneRect(self.item.boundingRect())
            self._scale()
        self.available.emit(not image.isNull())

    def release(self):
        self.cancelled.set()
        self.item.setPixmap(QPixmap())

    def _scale(self):
        if self.item.pixmap().isNull():
            return
        if self.fit:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.resetTransform()
            self.scale(self.zoom, self.zoom)

    def set_zoom(self, mode):
        # 100% maps one source pixel to one physical display pixel. Interpolation
        # beyond the bounded preview's resolution adds no recognition detail.
        actual = (
            self.original.width() / max(1, self.item.pixmap().width()) / self.devicePixelRatioF()
        )
        current = self.transform().m11()
        self.fit = mode == "fit"
        if mode == "actual":
            self.zoom = actual
        elif mode in ("out", "in"):
            self.zoom = min(8 * actual, max(0.02, current * (1.25 if mode == "in" else 0.8)))
        self._scale()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit:
            self._scale()
