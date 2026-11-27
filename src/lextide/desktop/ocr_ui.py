"""Local options and explicit review for a partial image recognition result."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr
from .localized import QCheckBox, QComboBox, QDialog, QLabel, QPlainTextEdit, QPushButton
from .ocr_preview import ImageView

_LANGUAGE = {
    "mixed": "English and Simplified Chinese",
    "zh": "Simplified Chinese",
    "en": "English",
}


def _notice(text, name):
    label = QLabel(tr(text))
    label.setWordWrap(True)
    label.setObjectName(name)
    return label


def choose_image_ocr(
    parent, *, has_draft: bool, previous_options=None, edited_review: bool = False
) -> dict | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("Recognize image text"))
    dialog.resize(480, 250)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)
    title = QLabel(tr("Local image OCR"))
    title.setProperty("role", "heading")
    layout.addWidget(title)
    layout.addWidget(
        _notice(
            "Recognition runs on this Mac. The image and its text stay on this device.",
            "OcrLocalNotice",
        )
    )
    if has_draft:
        layout.addWidget(
            _notice("A successful import will replace your current Studio draft.", "OcrWarning")
        )
    if edited_review:
        layout.addWidget(
            _notice(
                "Starting recognition again will discard your edits to this result.", "OcrWarning"
            )
        )
    fields = QFormLayout()
    language = QComboBox()
    for key, caption in _LANGUAGE.items():
        language.addItem(tr(caption), key)
    language.setFixedWidth(230)
    language.setAccessibleName(tr("Text in the image"))
    fields.addRow(tr("Text in the image"), language)
    provider = QComboBox()
    provider.addItem("Tesseract", "tesseract")
    provider.addItem("Apple Vision", "apple-vision")
    provider.setFixedWidth(230)
    provider.setAccessibleName(tr("Recognition engine"))
    fields.addRow(tr("Recognition engine"), provider)
    if previous_options:
        language.setCurrentIndex(language.findData(previous_options["language"]))
        provider.setCurrentIndex(provider.findData(previous_options["provider"]))
    layout.addLayout(fields)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
    recognize = buttons.addButton(
        tr("Discard edits and recognize again") if edited_review else tr("Recognize locally"),
        QDialogButtonBox.ButtonRole.AcceptRole,
    )
    recognize.setProperty("primary", True)
    recognize.setDefault(True)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return {"language": language.currentData(), "provider": provider.currentData()}
        return None
    finally:
        dialog.deleteLater()


class _ReviewDialog(QDialog):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "splitter"):
            direction = (
                Qt.Orientation.Horizontal if self.width() >= 780 else Qt.Orientation.Vertical
            )
            if self.splitter.orientation() != direction:
                self.splitter.setOrientation(direction)
                span = (
                    self.splitter.width()
                    if direction == Qt.Orientation.Horizontal
                    else self.splitter.height()
                )
                self.splitter.setSizes([int(0.42 * span), int(0.58 * span)])


def dismiss_partial_review(window):
    """Cancel this window's nested language chooser before closing the review."""
    for review in window.findChildren(
        _ReviewDialog, options=Qt.FindChildOption.FindDirectChildrenOnly
    ):
        if review.isVisible():
            for options in review.findChildren(QDialog):
                if options.isVisible():
                    options.reject()
            review.reject()


def _comparison(dialog, layout, editor, source):
    header = QHBoxLayout()
    title = QLabel(tr("Original OCR image"))
    header.addWidget(title)
    header.addStretch()
    toggle = QPushButton(tr("Hide image"))
    toggle.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    toggle.setAutoDefault(False)
    header.addWidget(toggle)
    layout.addLayout(header)
    split = dialog.splitter = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)
    pane = QWidget()
    column = QVBoxLayout(pane)
    column.setContentsMargins(0, 0, 0, 0)
    toolbar = QHBoxLayout()
    image = ImageView(pane)
    controls = []
    for caption, mode, description in (
        ("Fit", "fit", "Fit image in pane"),
        ("100%", "actual", "Show at original pixel scale"),
        ("−", "out", "Zoom out"),
        ("+", "in", "Zoom in"),
    ):
        control = QPushButton(tr(caption))
        control.setAutoDefault(False)
        control.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        control.setAccessibleName(tr(description))
        control.setToolTip(tr(description))
        control.setEnabled(False)
        control.clicked.connect(lambda _, action=mode: image.set_zoom(action))
        controls.append(control)
        toolbar.addWidget(control)
    toolbar.addStretch()
    column.addLayout(toolbar)
    column.addWidget(image, 1)
    split.addWidget(pane)
    split.addWidget(editor)
    split.setSizes([400, 550])
    split.setStretchFactor(0, 42)
    split.setStretchFactor(1, 58)
    layout.addWidget(split, 1)

    def availability(ready):
        if not ready:
            title.setText(tr("Local image preview unavailable. You can still review the text."))
            title.setWordWrap(True)
            toggle.hide()
            pane.hide()
        for control in controls:
            control.setEnabled(ready)

    def toggle_pane():
        visible = pane.isHidden()
        pane.setVisible(visible)
        toggle.setText(tr("Hide image") if visible else tr("Show image"))

    image.available.connect(availability)
    toggle.clicked.connect(toggle_pane)
    dialog.finished.connect(lambda _: image.release())
    image.load(source)


def review_partial_ocr(parent, text: str, options: dict) -> dict | None:
    """No draft or consent changes until an explicit review action is returned."""
    dialog = _ReviewDialog(parent)
    dialog.setWindowTitle(tr("Check partial recognition"))
    source = getattr(parent, "_ocr_preview_source", None)
    dialog.resize(960 if source else 680, 620 if source else 540)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    heading = QHBoxLayout()
    title = QLabel(tr("Check partial recognition"))
    title.setProperty("role", "heading")
    heading.addWidget(title)
    heading.addStretch()
    language = tr(_LANGUAGE.get(options.get("language"), "Unknown language"))
    if source:
        # Tesseract can load both chi_sim and eng for a Chinese-only request.
        # Reflect the languages the worker actually used in this recognition.
        scripts = frozenset(source.languages)
        caption = {
            frozenset({"chi_sim", "eng"}): _LANGUAGE["mixed"],
            frozenset({"zh-Hans", "en-US"}): _LANGUAGE["mixed"],
            frozenset({"zh-Hans"}): _LANGUAGE["zh"],
            frozenset({"eng"}): _LANGUAGE["en"],
            frozenset({"en-US"}): _LANGUAGE["en"],
        }.get(scripts)
        if caption:
            language = tr(caption)
    provider = {"tesseract": "Tesseract", "apple-vision": "Apple Vision"}.get(
        options.get("provider"), ""
    )
    context = QLabel(tr("{language} · {provider}", language=language, provider=provider))
    context.setObjectName("OcrContext")
    heading.addWidget(context)
    layout.addLayout(heading)
    layout.addWidget(
        _notice("Recognition may be incomplete. Check the text or recognize again.", "OcrWarning")
    )
    editor = QPlainTextEdit()
    editor.setPlainText(text)
    editor.setAccessibleName(tr("Partial OCR text to review and edit"))
    if source:
        _comparison(dialog, layout, editor, source)
    else:
        layout.addWidget(
            _notice(
                "Local image preview unavailable. You can still review the text.",
                "OcrPreviewUnavailable",
            )
        )
        layout.addWidget(editor, 1)
    gate_panel = QFrame()
    gate_panel.setObjectName("OcrGate")
    gate_column = QVBoxLayout(gate_panel)
    gate_column.setContentsMargins(12, 6, 12, 6)
    gate_column.setSpacing(2)
    checked = QCheckBox(tr("I checked this text against the image"))
    gate_column.addWidget(checked)
    hint = QLabel(tr("Check after reviewing the text."))
    hint.setProperty("role", "muted")
    hint_policy = hint.sizePolicy()
    hint_policy.setRetainSizeWhenHidden(True)
    hint.setSizePolicy(hint_policy)
    gate_column.addWidget(hint)
    layout.addWidget(gate_panel)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
    retry = QPushButton(tr("Change language and retry"))
    retry.setAutoDefault(False)
    accept = buttons.addButton(tr("Use checked text"), QDialogButtonBox.ButtonRole.AcceptRole)
    accept.setProperty("primary", True)
    accept.setEnabled(False)
    retry_options = None

    def gate():
        accept.setEnabled(checked.isChecked() and bool(editor.toPlainText().strip()))
        hint.setVisible(not checked.isChecked())

    def edited():
        checked.setChecked(False)
        hint.setText(tr("Text edited. Check the box again when finished."))
        gate()

    def retry_recognition():
        nonlocal retry_options
        retry_options = choose_image_ocr(
            dialog,
            has_draft=False,
            previous_options=options,
            edited_review=editor.toPlainText() != text,
        )
        if retry_options:
            dialog.accept()

    editor.textChanged.connect(edited)
    checked.toggled.connect(gate)
    retry.clicked.connect(retry_recognition)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    footer = QHBoxLayout()
    footer.addWidget(retry)
    footer.addStretch()
    footer.addWidget(buttons)
    layout.addLayout(footer)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        if retry_options:
            return {"action": "retry", "options": retry_options}
        if checked.isChecked() and editor.toPlainText().strip():
            return {"action": "accept", "text": editor.toPlainText()}
        return None
    finally:
        dialog.deleteLater()
