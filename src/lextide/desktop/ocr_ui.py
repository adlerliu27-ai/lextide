"""Local image import choices for Studio; opening this dialog starts no worker."""

from PySide6.QtWidgets import QDialogButtonBox, QFormLayout, QVBoxLayout

from .i18n import tr
from .localized import QCheckBox, QComboBox, QDialog, QLabel, QPlainTextEdit


def choose_image_ocr(
    parent, *, has_draft: bool, previous_options=None, edited_review: bool = False
) -> dict | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("Recognize image text"))
    layout = QVBoxLayout(dialog)
    notice = QLabel(tr("Recognition runs on this Mac. The image and its text stay on this device."))
    notice.setWordWrap(True)
    layout.addWidget(notice)
    if has_draft:
        replacement = QLabel(tr("A successful import will replace your current Studio draft."))
        replacement.setWordWrap(True)
        layout.addWidget(replacement)
    if edited_review:
        warning = QLabel(tr("Starting recognition again will discard your edits to this result."))
        warning.setWordWrap(True)
        layout.addWidget(warning)
    fields = QFormLayout()
    language = QComboBox()
    for caption, key in (
        ("English and Simplified Chinese", "mixed"),
        ("Simplified Chinese", "zh"),
        ("English", "en"),
    ):
        language.addItem(tr(caption), key)
    language.setAccessibleName(tr("Text in the image"))
    fields.addRow(tr("Text in the image"), language)
    provider = QComboBox()
    provider.addItem("Tesseract", "tesseract")
    provider.addItem("Apple Vision", "apple-vision")
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


def review_partial_ocr(parent, text: str, options: dict) -> dict | None:
    """No draft or consent changes until an explicit review action is returned."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("Check partial recognition"))
    dialog.resize(680, 480)
    layout = QVBoxLayout(dialog)
    warning = QLabel(tr("Recognition may be incomplete. Check the text or recognize again."))
    warning.setWordWrap(True)
    warning.setProperty("role", "heading")
    layout.addWidget(warning)
    note = QLabel(
        tr("Compare with the image and correct missing or incorrect words. Nothing is sent online.")
    )
    note.setWordWrap(True)
    layout.addWidget(note)
    editor = QPlainTextEdit()
    editor.setPlainText(text)
    editor.setAccessibleName(tr("Partial OCR text to review and edit"))
    layout.addWidget(editor, 1)
    checked = QCheckBox(tr("I checked this text against the image"))
    layout.addWidget(checked)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
    retry = buttons.addButton(
        tr("Change language and retry"), QDialogButtonBox.ButtonRole.ActionRole
    )
    accept = buttons.addButton(tr("Use checked text"), QDialogButtonBox.ButtonRole.AcceptRole)
    accept.setEnabled(False)
    retry_options = None

    def gate():
        accept.setEnabled(checked.isChecked() and bool(editor.toPlainText().strip()))

    def edited():
        checked.setChecked(False)
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
    layout.addWidget(buttons)
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
