"""Language changes update presentation without replacing controls or user state."""

import ast
import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QWidget  # noqa: E402

from lextide.desktop.i18n import (  # noqa: E402
    TranslatedText,
    configure_language,
    install_qt_translation,
    join_translated,
    tr,
)
from lextide.desktop.localized import (  # noqa: E402
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    bind_text,
    retranslate_ui,
)


@pytest.fixture
def translations(tmp_path):
    catalog = {
        "Review": "审阅",
        "Original": "原文",
        "English Only": "仅英文",
        "Review {count} sentences": "审阅 {count} 个句子",
        "Mode: {mode}": "模式：{mode}",
    }
    (tmp_path / "test.json").write_text(json.dumps(catalog), encoding="utf-8")
    configure_language("en")
    yield tmp_path
    configure_language("en")


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_sources_and_formatted_values_survive_repeated_translation(translations):
    count = tr("Review {count} sentences", count=8)
    nested = tr("Mode: {mode}", mode=tr("Original"))
    assert isinstance(count, str)
    assert count == "Review 8 sentences"
    configure_language("zh_CN", resource_dir=translations)
    assert tr(count) == "审阅 8 个句子"
    assert nested.render() == "模式：原文"
    configure_language("en")
    assert tr(tr(count)) == "Review 8 sentences"
    assert nested.render() == "Mode: Original"
    assert TranslatedText("Review").source == "Review"


def test_roundtrip_keeps_controls_editor_data_and_selection(app, translations):
    root = QWidget()
    title = QLabel(tr("Review"), root)
    dynamic = QLabel(tr("Review {count} sentences", count=8), root)
    edit = QLineEdit("/private/Review-English Only.txt", root)
    edit.setPlaceholderText(tr("Original"))
    edit.setAccessibleName(tr("Review"))
    edit.setSelection(9, 6)
    draft = QPlainTextEdit("Original\nEnglish Only\n原文不变", root)
    draft.setPlaceholderText(tr("Review"))
    cursor = draft.textCursor()
    cursor.setPosition(3)
    draft.setTextCursor(cursor)
    check = QCheckBox(tr("Review"), root)
    check.setChecked(True)
    slider = QSlider(Qt.Orientation.Horizontal, root)
    slider.setRange(0, 200)
    slider.setValue(83)
    slider.setAccessibleName(tr("Review"))
    disclosure = QToolButton(root)
    disclosure.setText(tr("Review"))
    disclosure.setCheckable(True)
    disclosure.setChecked(True)
    combo = QComboBox(root)
    combo.addItem(tr("Original"), "original-id")
    combo.addItem(tr("English Only"), {"engine": "edge-tts", "path": "/original"})
    combo.setCurrentIndex(1)
    cue_list = QListWidget(root)
    cue = QListWidgetItem("Original English Only 原文", cue_list)
    cue.setData(Qt.ItemDataRole.UserRole, 42)
    cue_list.setCurrentItem(cue)
    calls = []
    combo.currentIndexChanged.connect(lambda *_: calls.append("index"))
    combo.currentTextChanged.connect(lambda *_: calls.append("text"))
    combo.model().dataChanged.connect(lambda *_: calls.append("data"))
    draft.textChanged.connect(lambda: calls.append("draft"))
    edit.textChanged.connect(lambda *_: calls.append("edit"))
    for language, expected in (("zh_CN", "审阅"), ("en", "Review")):
        configure_language(language, resource_dir=translations)
        retranslate_ui(root)
        assert title.text() == expected
        assert dynamic.text() == tr("Review {count} sentences", count=8)
        assert edit.placeholderText() == tr("Original")
        assert edit.accessibleName() == expected
        assert edit.text() == "/private/Review-English Only.txt"
        assert edit.selectedText() == "Review"
        assert draft.toPlainText() == "Original\nEnglish Only\n原文不变"
        assert draft.textCursor().position() == 3
        assert check.isChecked() and check.text() == expected
        assert slider.value() == 83 and slider.accessibleName() == expected
        assert disclosure.isChecked() and disclosure.text() == expected
        assert combo.currentIndex() == 1 and combo.currentText() == tr("English Only")
        assert combo.currentData() == {"engine": "edge-tts", "path": "/original"}
        assert combo.itemData(0) == "original-id"
        assert cue_list.currentItem() is cue
        assert cue.text() == "Original English Only 原文"
        assert cue.data(Qt.ItemDataRole.UserRole) == 42
        assert not calls
    root.deleteLater()


def test_plain_dynamic_text_and_clear_remove_old_binding(app, translations):
    root = QWidget()
    path = QLabel(tr("Original"), root)
    path.setText("/Original/English Only")
    cleared = QLabel(tr("Review"), root)
    cleared.clear()
    tooltip = QLabel(tr("Review"), root)
    tooltip.setToolTip(tr("Original"))
    tooltip.setToolTip("Original")
    configure_language("zh_CN", resource_dir=translations)
    retranslate_ui(root)
    assert path.text() == "/Original/English Only"
    assert cleared.text() == ""
    assert tooltip.text() == "审阅" and tooltip.toolTip() == "Original"
    root.deleteLater()


def test_composed_errors_and_nested_fragments_retranslate_only_explicit_ui(app, translations):
    root = QWidget()
    error_text = "ERR_Review: " + tr("Review") + " [Original]"
    error = QLabel(error_text, root)
    nested = tr("Mode: {mode}", mode=tr("Original"))
    details = QLabel(
        join_translated(
            "\n{separator}", [error_text, nested, "/Original/Review {private}" + "\nOriginal"]
        ),
        root,
    )
    for language, expected, mode in (
        ("zh_CN", "审阅", "模式：原文"),
        ("en", "Review", "Mode: Original"),
    ):
        configure_language(language, resource_dir=translations)
        retranslate_ui(root)
        assert error.text() == f"ERR_Review: {expected} [Original]"
        assert details.text() == (
            f"ERR_Review: {expected} [Original]\n{{separator}}{mode}"
            "\n{separator}/Original/Review {private}\nOriginal"
        )
    root.deleteLater()


def test_join_keeps_plain_body_unbound_and_composition_ignores_catalog_keys(app, translations):
    root = QWidget()
    plain = join_translated("\n", ["Review", "Original", "English Only"])
    assert type(plain) is str
    literal = QLabel(plain, root)
    assert join_translated("\n", []) == ""
    catalog_path = translations / "test.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["{left}{right}"] = "错误：{left} / {right}"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    composed = "ID: " + tr("Review")
    configure_language("zh_CN", resource_dir=translations)
    retranslate_ui(root)
    assert composed.render() == "ID: 审阅"
    assert literal.text() == "Review\nOriginal\nEnglish Only"
    root.deleteLater()


def test_combo_edits_removals_and_insertions_keep_bindings_attached(app, translations):
    root = QWidget()
    combo = QComboBox(root)
    combo.addItem(tr("Original"), "original")
    combo.addItem(tr("English Only"), "english")
    combo.insertItem(0, "untouched", "raw")
    combo.removeItem(1)
    combo.setCurrentIndex(1)
    combo.setEditable(True)
    combo.setEditText("my English Only draft")
    combo.lineEdit().setSelection(3, 7)
    changed = []
    combo.lineEdit().textChanged.connect(lambda *_: changed.append(True))
    configure_language("zh_CN", resource_dir=translations)
    retranslate_ui(root)
    assert combo.itemText(0) == "untouched"
    assert combo.itemText(1) == "仅英文"
    assert combo.currentData() == "english"
    assert combo.currentText() == "my English Only draft"
    assert combo.lineEdit().selectedText() == "English"
    assert not changed
    combo.setItemText(1, "English Only")
    retranslate_ui(root)
    assert combo.itemText(1) == "English Only"
    combo.clear()
    combo.addItem("Original", "new")
    retranslate_ui(root)
    assert combo.count() == 1 and combo.itemText(0) == "Original"
    root.deleteLater()


def test_tabs_and_table_keep_pages_items_and_hidden_data(app, translations):
    root = QWidget()
    tabs = QTabWidget(root)
    first, second = QWidget(), QWidget()
    tabs.addTab(first, tr("Original"))
    tabs.addTab(second, tr("English Only"))
    tabs.setCurrentIndex(1)
    table = QTableWidget(1, 2, root)
    table.setHorizontalHeaderLabels([tr("Review"), "ID"])
    translated = QTableWidgetItem(tr("Original"))
    literal = QTableWidgetItem("Original")
    literal.setData(Qt.ItemDataRole.UserRole, {"id": "Original", "path": "/Original"})
    table.setItem(0, 0, translated)
    table.setItem(0, 1, literal)
    table.setCurrentItem(literal)
    header = table.horizontalHeaderItem(0)
    changes = []
    table.itemChanged.connect(lambda *_: changes.append(True))
    for language in ("zh_CN", "en"):
        configure_language(language, resource_dir=translations)
        retranslate_ui(root)
        assert tabs.currentWidget() is second
        assert tabs.tabText(0) == tr("Original")
        assert tabs.tabText(1) == tr("English Only")
        assert table.horizontalHeaderItem(0) is header
        assert header.text() == tr("Review")
        assert table.item(0, 0) is translated and translated.text() == tr("Original")
        assert table.currentItem() is literal and literal.text() == "Original"
        assert literal.data(Qt.ItemDataRole.UserRole) == {"id": "Original", "path": "/Original"}
        assert not changes
    root.deleteLater()


def test_native_buttons_use_explicit_binding(app, translations):
    root = QWidget()
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save, root)
    save = buttons.button(QDialogButtonBox.StandardButton.Save)
    bind_text(save, tr("Review"))
    configure_language("zh_CN", resource_dir=translations)
    retranslate_ui(root)
    assert buttons.button(QDialogButtonBox.StandardButton.Save) is save
    assert save.text() == "审阅"
    bind_text(save, "Original")
    retranslate_ui(root)
    assert save.text() == "Original"
    root.deleteLater()


def test_qt_builtin_translation_is_replaced_and_removed(app, translations):
    configure_language("zh_CN", resource_dir=translations)
    install_qt_translation(app)
    chinese = getattr(app, "_lextide_translator", None)
    assert chinese is not None
    assert app.translate("QPlatformTheme", "Cancel") != "Cancel"
    install_qt_translation(app)
    assert app._lextide_translator is not chinese
    configure_language("en")
    install_qt_translation(app)
    assert app._lextide_translator is None
    assert app.translate("QPlatformTheme", "Cancel") == "Cancel"


def test_ui_catalog_covers_literal_sources_and_preserves_placeholders():
    from lextide.desktop.i18n import TRANSLATIONS, _fields

    catalog = {}
    for path in TRANSLATIONS.glob("*.json"):
        for source, translated in json.loads(path.read_text(encoding="utf-8")).items():
            assert translated and _fields(source) == _fields(translated), (path.name, source)
            catalog[source] = translated
    # Brand spelling, empty icon labels and clock digits are intentionally literal.
    literals = {"", "Lextide", "00:00"}
    missing = []
    for path in TRANSLATIONS.parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"tr", "label", "button", "disclosure"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value not in literals | catalog.keys()
            ):
                missing.append((path.name, node.lineno, node.args[0].value))
    assert not missing


def test_dynamic_engine_and_diagnostic_sources_have_chinese_catalog_entries():
    from lextide.desktop import engine_policy, messages
    from lextide.desktop.i18n import TRANSLATIONS

    catalog = {
        source
        for path in TRANSLATIONS.glob("*.json")
        for source in json.loads(path.read_text(encoding="utf-8"))
    }
    sources = set(messages._NOTES.values()) | set(messages._CODES.values())
    for edge in (True, False):
        for kokoro in (True, False):
            status = {
                "engines": [
                    {"engine_id": "edge-tts", "available": edge},
                    {"engine_id": "kokoro", "available": kokoro},
                ],
                "offline_runtime": {"ready": kokoro},
                "model": {"ready": kokoro},
            }
            for engine in ("auto", "edge-tts", "kokoro"):
                for discovery in (None, status):
                    for probe in (None, {"reachable": True}, {"reachable": False}):
                        decision = engine_policy.resolve_engine(engine, discovery, probe)
                        sources.add(decision["reason"])
    # Player error messages are selected by QMediaPlayer error enums before tr().
    player_tree = ast.parse((Path(messages.__file__).with_name("player.py")).read_text())
    error_handler = next(
        node
        for node in ast.walk(player_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_error"
    )
    sources.update(
        node.value
        for node in ast.walk(error_handler)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and " " in node.value
    )
    assert not sources - catalog
