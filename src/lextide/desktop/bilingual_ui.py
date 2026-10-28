"""Local Chinese subtitle presentation, review and editing; never touches speech text."""

from __future__ import annotations

from math import ceil
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPalette, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFileDialog,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
)

from lextide.errors import StudioError

from .bilingual import (
    SubtitleSource,
    TranslationStore,
    import_translation,
    validate_translation_text,
)
from .i18n import join_translated, tr
from .localized import QCheckBox, QDialog, QLabel, QPlainTextEdit, bind_text

CHINESE_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def _text_layout(text, font, width):
    layout = QTextLayout(text.replace("\n", "\u2028"), font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    height = 0.0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        line.setPosition(QPointF(0, height))
        height += line.height()
    layout.endLayout()
    return layout, height


class TranscriptDelegate(QStyledItemDelegate):
    """Draw a separate time column and aligned, wrapping English/Chinese text."""

    LEFT_PADDING = 24
    RIGHT_PADDING = 24
    TIME_WIDTH = 58
    COLUMN_GAP = 14
    TOP_PADDING = 10
    BOTTOM_PADDING = 10

    def _chinese(self, index):
        if self.parent().property("subtitle_mode") == "bilingual":
            return index.data(CHINESE_ROLE) or ""
        return ""

    @staticmethod
    def chinese_font(font):
        smaller = QFont(font)
        if font.pixelSize() > 0:
            smaller.setPixelSize(max(1, round(font.pixelSize() * 0.8)))
        else:
            smaller.setPointSizeF(font.pointSizeF() * 0.8)
        return smaller

    @staticmethod
    def _parts(text):
        # The source item remains intact for accessibility, cue identity and seek.
        timestamp, separator, body = text.partition("   ")
        if separator and ":" in timestamp:
            return timestamp, body
        return "", text

    def _body_width(self):
        widget = self.parent()
        return max(
            1,
            widget.viewport().width()
            - 2 * widget.spacing()
            - self.LEFT_PADDING
            - self.RIGHT_PADDING
            - self.TIME_WIDTH
            - self.COLUMN_GAP,
        )

    @staticmethod
    def time_font(font):
        value = QFont(font)
        value.setPixelSize(12)
        return value

    def sizeHint(self, option, index):
        self.initStyleOption(option, index)
        widget = self.parent()
        timestamp, body = self._parts(option.text)
        width = self._body_width()
        _, height = _text_layout(body, option.font, width)
        _, time_height = _text_layout(timestamp, self.time_font(option.font), self.TIME_WIDTH)
        height = max(height, time_height)
        chinese = self._chinese(index)
        if chinese:
            _, secondary = _text_layout(chinese, self.chinese_font(option.font), width)
            height += secondary + 6
        return QSize(
            widget.viewport().width() - 2 * widget.spacing(),
            ceil(height) + self.TOP_PADDING + self.BOTTOM_PADDING,
        )

    def paint(self, painter, option, index):
        chinese = self._chinese(index)
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        original = styled.text
        styled.text = ""
        widget = self.parent()
        widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, widget)
        timestamp, body = self._parts(original)
        width = self._body_width()
        english, height = _text_layout(body, styled.font, width)
        time_layout, _ = _text_layout(timestamp, self.time_font(styled.font), self.TIME_WIDTH)
        painter.save()
        painter.setClipRect(option.rect)
        origin = QPointF(
            option.rect.left() + self.LEFT_PADDING + self.TIME_WIDTH + self.COLUMN_GAP,
            option.rect.top() + self.TOP_PADDING,
        )
        painter.setPen(styled.palette.color(QPalette.ColorRole.Text))
        english.draw(painter, origin)
        colors = getattr(widget.window(), "_colors", {})
        painter.setPen(
            QColor(colors["muted"])
            if "muted" in colors
            else styled.palette.color(QPalette.ColorRole.PlaceholderText)
        )
        time_layout.draw(
            painter,
            QPointF(option.rect.left() + self.LEFT_PADDING, origin.y()),
        )
        if chinese:
            secondary, _ = _text_layout(chinese, self.chinese_font(styled.font), width)
            secondary.draw(painter, origin + QPointF(0, height + 6))
        painter.restore()


def _plain_label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class TranslationPreviewDialog(QDialog):
    """Full, plain-text paired preview. Confirmation always starts unchecked."""

    def __init__(self, parent, source, texts):
        super().__init__(parent)
        self.setWindowTitle(tr("Review Chinese subtitle mapping"))
        self.resize(760, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _plain_label(
                tr(
                    "Review every English–Chinese pair below. Matching line counts or timestamps "
                    "cannot prove that translations are in the right semantic order. "
                    "Confirm the mapping yourself before saving."
                )
            )
        )
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName(tr("Complete English–Chinese subtitle pairs"))
        bind_text(
            self.preview,
            join_translated(
                "\n\n",
                (
                    tr(
                        "Sentence {index}\nEnglish: {english}\nChinese: {chinese}",
                        index=cue["index"],
                        english=cue["text"],
                        chinese=text,
                    )
                    for cue, text in zip(source.cues, texts, strict=True)
                ),
            ),
            setter="setPlainText",
        )
        layout.addWidget(self.preview, 1)
        self.confirmation = QCheckBox(tr("I checked every pair and confirm this mapping"))
        self.confirmation.setChecked(False)
        layout.addWidget(self.confirmation)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        bind_text(save, tr("Confirm and save Chinese"))
        save.setEnabled(False)
        bind_text(self.buttons.button(QDialogButtonBox.StandardButton.Cancel), tr("Cancel"))
        self.confirmation.toggled.connect(save.setEnabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def accept(self):
        if self.confirmation.isChecked():
            super().accept()


class TranslationEditDialog(QDialog):
    """Edit one selected cue; empty or invalid translations cannot be saved."""

    def __init__(self, parent, cue, text):
        super().__init__(parent)
        self.setWindowTitle(tr("Edit Chinese for sentence {index}", index=cue["index"]))
        self.resize(680, 470)
        self.translation = None
        layout = QVBoxLayout(self)
        layout.addWidget(_plain_label(tr("Original English")))
        self.english = QPlainTextEdit(cue["text"])
        self.english.setReadOnly(True)
        self.english.setAccessibleName(tr("Original English"))
        layout.addWidget(self.english, 1)
        layout.addWidget(_plain_label(tr("Chinese translation")))
        self.editor = QPlainTextEdit(text or "")
        self.editor.setAccessibleName(tr("Chinese translation"))
        layout.addWidget(self.editor, 1)
        self.warning = _plain_label("")
        layout.addWidget(self.warning)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        bind_text(self.buttons.button(QDialogButtonBox.StandardButton.Save), tr("Save Chinese"))
        bind_text(self.buttons.button(QDialogButtonBox.StandardButton.Cancel), tr("Cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.editor.textChanged.connect(self._validate)
        self._validate()
        self.editor.setFocus()

    def _validate(self):
        try:
            self.translation = validate_translation_text(self.editor.toPlainText())
        except (StudioError, ValueError):
            self.translation = None
        valid = self.translation is not None
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(valid)
        self.warning.setText(
            ""
            if valid
            else tr(
                "Enter a nonblank translation. Control characters and text "
                "over 16 KiB are not accepted."
            )
        )
        return valid

    def accept(self):
        if self._validate():
            super().accept()


class BilingualController(QObject):
    """Bind verified subtitle snapshots to local translations, separate from TTS approval."""

    def __init__(self, window, root):
        super().__init__(window)
        self.w = window
        self.store = TranslationStore(root)
        self.source = None
        self.document = None
        self.corrupt = False
        self.w.subtitle_import_button.clicked.connect(self.import_file)
        self.w.subtitle_edit_button.clicked.connect(self.edit_selected)
        self.w.transcript_list.itemSelectionChanged.connect(self._buttons)
        self.w.subtitle_mode_combo.currentIndexChanged.connect(self._mode_changed)
        self._buttons()

    def load(self, verified):
        self.source = None
        self.document = None
        self.corrupt = False
        try:
            self.source = SubtitleSource.from_verified(verified)
            self.document = self.store.load(self.source)
        except (StudioError, OSError, ValueError, KeyError):
            self.corrupt = True
        self._display()

    def _buttons(self):
        self.w.subtitle_import_button.setEnabled(self.source is not None)
        self.w.subtitle_edit_button.setEnabled(
            self.source is not None
            and not self.corrupt
            and bool(self.w.transcript_list.selectedItems())
        )

    def _mode_changed(self):
        self.w.set_subtitle_mode(self.w.subtitle_mode_combo.currentData())

    def _display(self):
        self.w.set_chinese_translations(self.document.texts if self.document else ())
        self._buttons()
        if self.corrupt:
            text = tr(
                "Chinese subtitles could not be loaded safely. English playback remains "
                "available. Reopen the session or import a checked translation to replace it."
            )
        elif self.source is None:
            text = tr("Open a verified session to import or edit Chinese subtitles.")
        else:
            count = sum(text is not None for text in self.document.texts) if self.document else 0
            text = (
                tr(
                    "Chinese: {count}/{total} sentences. Missing translations show English only.",
                    count=count,
                    total=len(self.source.cues),
                )
                if count
                else tr(
                    "No Chinese translation yet. Import a local SRT/TXT "
                    "or select a sentence to edit."
                )
            )
        self.w.subtitle_status_label.setText(text)

    def _failed(self, error, *, importing=False):
        code = getattr(error, "code", "")
        if code == "SUBTITLE_SOURCE_CHANGED":
            message = tr(
                "English subtitles changed. Reopen and verify the session before saving Chinese."
            )
        elif code == "TRANSLATION_CONFLICT":
            message = tr(
                "Chinese subtitles changed elsewhere. Reopen the session and review them again."
            )
        elif code in {"STORAGE_IO", "TASK_BUSY"} or isinstance(error, OSError):
            message = tr(
                "Could not save or read Chinese subtitles. Check local folder permissions "
                "and disk space, then try again."
            )
        elif importing:
            message = tr(
                "Could not import Chinese subtitles. Use UTF-8 TXT with one nonblank line "
                "per sentence, or SRT with identical cue numbers and timestamps. "
                "Files must be at most 16 MiB and each translation at most 16 KiB. "
                "Nothing was saved."
            )
        else:
            message = tr(
                "Chinese subtitles could not be saved. "
                "Reopen the session and check the translation."
            )
        self.w.subtitle_status_label.setText(message)

    def import_file(self):
        source = self.source
        if source is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self.w, tr("Import Chinese subtitles"), "", tr("Chinese subtitles (*.srt *.txt)")
        )
        if not path:
            return
        try:
            source.assert_current()
            revision = self.store.current_revision(source)
            texts = import_translation(Path(path), source)
            dialog = TranslationPreviewDialog(self.w, source, texts)
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.confirmation.isChecked():
                return
            if self.source is not source:
                self.w.subtitle_status_label.setText(
                    tr("Session changed. Import the translation again.")
                )
                return
            self.document = self.store.save(
                source,
                texts,
                expected_revision=revision,
                origin=f"import:{Path(path).suffix.lower().lstrip('.')}",
            )
            self.corrupt = False
            self._display()
        except (StudioError, OSError, ValueError) as error:
            self._failed(error, importing=True)

    def edit_selected(self):
        source = self.source
        index = self.w.transcript_list.currentRow()
        if (
            source is None
            or self.corrupt
            or not self.w.transcript_list.selectedItems()
            or not 0 <= index < len(source.cues)
        ):
            return
        try:
            source.assert_current()
            texts = list(self.document.texts if self.document else (None,) * len(source.cues))
            revision = self.document.revision if self.document else None
            dialog = TranslationEditDialog(self.w, source.cues[index], texts[index])
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.translation is None:
                return
            if self.source is not source:
                self.w.subtitle_status_label.setText(
                    tr("Session changed. Select a sentence again.")
                )
                return
            texts[index] = dialog.translation
            self.document = self.store.save(
                source, texts, expected_revision=revision, origin="manual"
            )
            self._display()
        except (StudioError, OSError, ValueError) as error:
            self._failed(error)
