"""Listen-only keyboard routing, before Qt buttons consume Space on release."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from .i18n import tr


def _text_input(widget):
    """Walk ancestors: focus can be a viewport, editor child, or custom input."""
    while widget is not None:
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)) and not widget.isReadOnly():
            return True
        if isinstance(widget, QAbstractSpinBox):
            return True
        if isinstance(widget, QComboBox) and widget.isEditable():
            return True
        if widget.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled):
            return True
        widget = widget.parentWidget()
    return False


class ListenKeys(QObject):
    """Application filter, strictly scoped to this active Listen window.

    A Space QShortcut would swallow text-editor input before its callback can
    decline it, and a KeyPress-only handler leaves button KeyRelease activation.
    Claim ShortcutOverride, press AND release for non-editing Listen contexts.
    Auto-repeat is consumed without triggering. No media state is owned here.
    """

    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.page = window.pages["Listen"]
        QApplication.instance().installEventFilter(self)
        self.focus_shortcut = QShortcut(QKeySequence("Ctrl+Shift+F"), window)
        self.focus_shortcut.setAutoRepeat(False)
        self.focus_shortcut.activated.connect(self._focus_view)
        window.page_changed.connect(self._page_changed)
        self._page_changed("")
        shortcut = self.focus_shortcut.key().toString(QKeySequence.SequenceFormat.NativeText)
        hint = tr(
            "Toggle focus view ({shortcut}); Space plays or pauses in Listen.", shortcut=shortcut
        )
        window.focus_check.setToolTip(hint)
        window.focus_check.setAccessibleName(tr("Focus view"))
        window.focus_check.setAccessibleDescription(hint)

    def _page_changed(self, _):
        self.focus_shortcut.setEnabled(self.w.page_stack.currentWidget() is self.page)

    def _context(self):
        return (
            self.w.isActiveWindow()
            and self.w.page_stack.currentWidget() is self.page
            and QApplication.activeModalWidget() is None
            and QApplication.activePopupWidget() is None
        )

    def _focus_view(self):
        if self._context():
            self.w.focus_check.toggle()

    def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
        if event.type() not in (
            QEvent.Type.ShortcutOverride,
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
        ):
            return False
        if event.key() != Qt.Key.Key_Space or event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        if (
            not isinstance(watched, QWidget)
            or watched.window() is not self.w
            or not self._context()
        ):
            return False
        focus = QApplication.focusWidget() or watched
        if focus.window() is not self.w or _text_input(focus):
            return False
        event.accept()
        if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            self.w.play_button.click()  # Disabled/no media: Qt deliberately does nothing.
        return True
