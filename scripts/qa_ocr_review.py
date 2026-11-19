"""Prepare self-authored local OCR samples or open one in an isolated source QA window."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

EN = "We review each lesson before reading aloud."
ZH = "请仔细检查这段文字。"
SAMPLES = {
    "english": [EN],
    "chinese": [ZH],
    "mixed": [EN, ZH],
    "faint-caption": [EN, ZH],
    "blank": [],
}


def prepare(output):
    """Create new PNGs and an answer key. The last caption simulates faded small type."""
    output.mkdir(parents=True, exist_ok=False)
    records = {}
    for sample, lines in SAMPLES.items():
        image = QImage(1200, 310, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        for i, line in enumerate(lines):
            faint = sample == "faint-caption" and i == 1
            font = QFont("Helvetica" if line == EN else "Heiti SC")
            font.setPixelSize(11 if faint else 44)
            painter.setFont(font)
            painter.setPen(QColor("#e5e5e5" if faint else "#141414"))
            painter.drawText(60, 100 + 100 * i, line)
        painter.end()
        path = output / f"{sample}.png"
        if not image.save(str(path), "PNG"):
            raise OSError(f"Could not write {path}")
        records[sample] = {
            "text": "\n".join(lines),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (output / "expected.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def open_sample(app, image, root):
    """Skip the platform file picker only. Use the production options, worker and review."""
    from lextide.desktop.controller import Controller
    from lextide.desktop.i18n import configure_language, install_qt_translation
    from lextide.desktop.window import MainWindow

    configure_language("en")
    install_qt_translation(app)
    window = MainWindow()
    controller = Controller(window, root)
    window.controller = controller
    window.show_page("Studio")
    window.setWindowTitle("Lextide · Local OCR QA")
    window.speech_edit.setPlainText("Preserve this QA draft.")
    window.show()
    QTimer.singleShot(0, lambda: controller._import_image(str(image)))
    return app.exec()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    ops = parser.add_subparsers(dest="command", required=True)
    ops.add_parser("prepare").add_argument("directory", type=Path)
    review = ops.add_parser("review")
    review.add_argument("image", type=Path)
    review.add_argument("--scratch-parent", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication([sys.argv[0]])
    if args.command == "prepare":
        prepare(args.directory.resolve())
        return 0
    image = args.image.resolve(strict=True)
    with TemporaryDirectory(prefix="lextide-review-", dir=args.scratch_parent) as scratch:
        print(f"Isolated Lextide QA directory: {scratch}", flush=True)
        return open_sample(app, image, Path(scratch))


if __name__ == "__main__":
    raise SystemExit(main())
