"""Explicit, presentation-only translation bindings for existing Qt controls.

Import these controls in presentation modules. Only TranslatedText values bind;
ordinary strings remove a previous binding. Editors never bind their contents.
Refreshing changes properties in place, keeping widgets, models and user data.
"""

from __future__ import annotations

from contextlib import ExitStack

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QObject, QPersistentModelIndex, QSignalBlocker, Qt

from .i18n import TranslatedText, tr


def _remember(target, setter: str, text: str) -> None:
    bindings = getattr(target, "_ui_translations", None)
    if bindings is None:
        bindings = target._ui_translations = {}
    if isinstance(text, TranslatedText):
        bindings[setter] = text
    else:
        bindings.pop(setter, None)


def bind_text(target, text: str, *, setter: str = "setText") -> None:
    """Bind a native control, such as a QDialogButtonBox standard button."""
    getattr(target, setter)(text)
    _remember(target, setter, text)


def _refresh_properties(target) -> None:
    for setter, source in tuple(getattr(target, "_ui_translations", {}).items()):
        # Keep metadata even though Qt accepts only the rendered string.
        getattr(target, setter)(tr(source))


def _translated_setter(name):
    def set_value(self, value):
        getattr(super(_WidgetTranslations, self), name)(value)
        _remember(self, name, value)

    set_value.__name__ = name
    return set_value


class _WidgetTranslations:
    _constructor_setter = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self._constructor_setter:
            text = next((value for value in args if isinstance(value, str)), None)
            if text is not None:
                _remember(self, self._constructor_setter, text)
        for key in ("text", "windowTitle", "toolTip", "accessibleName", "placeholderText"):
            if key == "text" and self._constructor_setter != "setText":
                continue
            if key in kwargs:
                _remember(self, "set" + key[0].upper() + key[1:], kwargs[key])

    setWindowTitle = _translated_setter("setWindowTitle")
    setToolTip = _translated_setter("setToolTip")
    setStatusTip = _translated_setter("setStatusTip")
    setWhatsThis = _translated_setter("setWhatsThis")
    setAccessibleName = _translated_setter("setAccessibleName")
    setAccessibleDescription = _translated_setter("setAccessibleDescription")
    setPlaceholderText = _translated_setter("setPlaceholderText")


class _TextTranslations(_WidgetTranslations):
    _constructor_setter = "setText"
    setText = _translated_setter("setText")


class QLabel(_TextTranslations, QtWidgets.QLabel):
    def clear(self):
        _remember(self, "setText", "")
        super().clear()


class QPushButton(_TextTranslations, QtWidgets.QPushButton):
    pass


class QCheckBox(_TextTranslations, QtWidgets.QCheckBox):
    pass


class QToolButton(_TextTranslations, QtWidgets.QToolButton):
    pass


class QAction(_TextTranslations, QtGui.QAction):
    pass


class QMainWindow(_WidgetTranslations, QtWidgets.QMainWindow):
    pass


class QDialog(_WidgetTranslations, QtWidgets.QDialog):
    pass


class QLineEdit(_WidgetTranslations, QtWidgets.QLineEdit):
    pass


class QPlainTextEdit(_WidgetTranslations, QtWidgets.QPlainTextEdit):
    pass


class QSlider(_WidgetTranslations, QtWidgets.QSlider):
    pass


class QSpinBox(_WidgetTranslations, QtWidgets.QSpinBox):
    pass


class QComboBox(_WidgetTranslations, QtWidgets.QComboBox):
    def __init__(self, *args, **kwargs):
        self._item_translations = {}
        super().__init__(*args, **kwargs)

    def _remember_item(self, index, text):
        key = QPersistentModelIndex(self.model().index(index, self.modelColumn()))
        if isinstance(text, TranslatedText):
            self._item_translations[key] = text
        else:
            self._item_translations.pop(key, None)

    def addItem(self, *args, **kwargs):
        index = self.count()
        super().addItem(*args, **kwargs)
        text = args[0] if args and isinstance(args[0], str) else args[1] if args else ""
        self._remember_item(index, kwargs.get("text", text))

    def addItems(self, texts):
        for text in texts:
            self.addItem(text)

    def insertItem(self, index, *args, **kwargs):
        actual = max(0, min(index, self.count()))
        super().insertItem(index, *args, **kwargs)
        text = args[0] if args and isinstance(args[0], str) else args[1] if args else ""
        self._remember_item(actual, kwargs.get("text", text))

    def insertItems(self, index, texts):
        actual = max(0, min(index, self.count()))
        for offset, text in enumerate(texts):
            self.insertItem(actual + offset, text)

    def setItemText(self, index, text):
        super().setItemText(index, text)
        self._remember_item(index, text)

    def setItemData(self, index, value, role=Qt.ItemDataRole.UserRole):
        super().setItemData(index, value, role)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            self._remember_item(index, value)

    def clear(self):
        self._item_translations.clear()
        super().clear()

    def _retranslate_items(self):
        # Neither a presentation label change nor model dataChanged is a choice.
        with ExitStack() as blockers:
            blockers.enter_context(QSignalBlocker(self))
            blockers.enter_context(QSignalBlocker(self.model()))
            edit = self.lineEdit()
            if edit:
                blockers.enter_context(QSignalBlocker(edit))
            state = (
                (edit.text(), edit.cursorPosition(), edit.selectionStart(), edit.selectedText())
                if edit
                else None
            )
            for index, source in tuple(self._item_translations.items()):
                if index.isValid() and index.model() == self.model():
                    QtWidgets.QComboBox.setItemText(self, index.row(), source.render())
                else:
                    self._item_translations.pop(index, None)
            if edit and state:
                edit.setText(state[0])
                edit.setCursorPosition(state[1])
                if state[2] >= 0:
                    edit.setSelection(state[2], len(state[3]))
        self.update()
        self.view().viewport().update()


class QTabWidget(_WidgetTranslations, QtWidgets.QTabWidget):
    def __init__(self, *args, **kwargs):
        self._tab_translations = {}
        super().__init__(*args, **kwargs)

    def addTab(self, widget, *args):
        index = super().addTab(widget, *args)
        self._remember_tab(index, args[-1])
        return index

    def insertTab(self, index, widget, *args):
        actual = super().insertTab(index, widget, *args)
        self._remember_tab(actual, args[-1])
        return actual

    def _remember_tab(self, index, text):
        widget = self.widget(index)
        if isinstance(text, TranslatedText):
            self._tab_translations[widget] = text
        else:
            self._tab_translations.pop(widget, None)

    def setTabText(self, index, text):
        super().setTabText(index, text)
        self._remember_tab(index, text)

    def removeTab(self, index):
        self._tab_translations.pop(self.widget(index), None)
        super().removeTab(index)

    def clear(self):
        self._tab_translations.clear()
        super().clear()

    def _retranslate_items(self):
        for widget, source in self._tab_translations.items():
            index = self.indexOf(widget)
            if index >= 0:
                QtWidgets.QTabWidget.setTabText(self, index, source.render())


class QTableWidgetItem(_TextTranslations, QtWidgets.QTableWidgetItem):
    pass


class QListWidgetItem(_TextTranslations, QtWidgets.QListWidgetItem):
    pass


class QTableWidget(_WidgetTranslations, QtWidgets.QTableWidget):
    def setHorizontalHeaderLabels(self, labels):
        for index, text in enumerate(labels):
            if index >= self.columnCount():
                break
            item = self.horizontalHeaderItem(index)
            if item is None:
                self.setHorizontalHeaderItem(index, QTableWidgetItem(text))
            else:
                bind_text(item, text)

    def _retranslate_items(self):
        with QSignalBlocker(self), QSignalBlocker(self.model()):
            for column in range(self.columnCount()):
                item = self.horizontalHeaderItem(column)
                if item is not None:
                    _refresh_properties(item)
            for row in range(self.rowCount()):
                for column in range(self.columnCount()):
                    item = self.item(row, column)
                    if item is not None:
                        _refresh_properties(item)
        self.horizontalHeader().viewport().update()
        self.viewport().update()


class QListWidget(_WidgetTranslations, QtWidgets.QListWidget):
    def _retranslate_items(self):
        with QSignalBlocker(self), QSignalBlocker(self.model()):
            for index in range(self.count()):
                _refresh_properties(self.item(index))
        self.viewport().update()


def retranslate_ui(root: QObject) -> None:
    """Refresh explicit bindings only; never infer sources from displayed text."""
    for target in (root, *root.findChildren(QObject)):
        _refresh_properties(target)
        refresh_items = getattr(target, "_retranslate_items", None)
        if refresh_items is not None:
            refresh_items()
