"""Presentation-only translation. Backend requests and saved artifacts stay unchanged.

English source strings are the fallback catalog. Select the language once, before
constructing the window; a saved preference takes effect on the next launch.
"""

from __future__ import annotations

import json
from pathlib import Path
from string import Formatter

LANGUAGES = ("en", "zh_CN")
TRANSLATIONS = Path(__file__).with_name("translations")
_language = "en"
_catalog: dict[str, str] = {}


def normalize_language(value: object) -> str:
    return value if isinstance(value, str) and value in LANGUAGES else "en"


def _fields(value: str) -> list[tuple[str, str, str | None]]:
    return sorted(
        (name, spec, conversion)
        for _, name, spec, conversion in Formatter().parse(value)
        if name is not None
    )


def configure_language(language: str, *, resource_dir: Path | None = None) -> bool:
    """Load local catalogs; absent/invalid entries safely use the English source."""
    global _language, _catalog
    _language = normalize_language(language)
    _catalog = {}
    if _language == "en":
        return True
    directory = resource_dir if resource_dir is not None else TRANSLATIONS
    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        files = []
    complete = bool(files)
    for path in files:
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(entries, dict):
                raise ValueError
            for source, translated in entries.items():
                if (
                    not isinstance(translated, str)
                    or not translated
                    or _fields(source) != _fields(translated)
                ):
                    complete = False
                    continue
                _catalog[source] = translated
        except (OSError, ValueError, UnicodeError):
            complete = False
    return complete


def current_language() -> str:
    return _language


def tr(source: str, **values: object) -> str:
    """Only call with UI strings, never with speech text, cues or identifiers."""
    translated = _catalog.get(source, source) if _language == "zh_CN" else source
    return translated.format(**values) if values else translated


def install_qt_translation(app) -> None:
    """Translate Qt's built-in buttons and dialogs with the shipped Qt catalog."""
    from PySide6.QtCore import QLibraryInfo, QTranslator

    old = getattr(app, "_lextide_translator", None)
    if old is not None:
        app.removeTranslator(old)
        app._lextide_translator = None
    if _language != "zh_CN":
        return
    translator = QTranslator(app)
    path = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    if translator.load(str(path / "qtbase_zh_CN.qm")):
        app.installTranslator(translator)
        app._lextide_translator = translator
