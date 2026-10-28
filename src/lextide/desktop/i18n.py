"""Presentation-only translation. Backend requests and saved artifacts stay unchanged.

English source strings are the fallback catalog. TranslatedText retains the
explicit UI source so existing controls can refresh without touching user data.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
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


class TranslatedText(str):
    """A normal rendered string with the source needed for a later UI refresh."""

    def __new__(
        cls,
        source: str,
        values: dict[str, object] | None = None,
        *,
        translate_source: bool = True,
    ):
        values = dict(values or {})
        translated = (
            _catalog.get(source, source) if translate_source and _language == "zh_CN" else source
        )
        rendered_values = {
            key: value.render() if isinstance(value, TranslatedText) else value
            for key, value in values.items()
        }
        rendered = translated.format(**rendered_values) if values else translated
        result = super().__new__(cls, rendered)
        result.source = source
        result.values = values
        result.translate_source = translate_source
        return result

    def render(self) -> str:
        return str(tr(self))

    def __add__(self, other):
        if not isinstance(other, str):
            return NotImplemented
        return TranslatedText(
            "{left}{right}", {"left": self, "right": other}, translate_source=False
        )

    def __radd__(self, other):
        if not isinstance(other, str):
            return NotImplemented
        return TranslatedText(
            "{left}{right}", {"left": other, "right": self}, translate_source=False
        )


def tr(source: str, **values: object) -> TranslatedText:
    """Only call with UI strings, never with speech text, cues or identifiers."""
    if isinstance(source, TranslatedText):
        return TranslatedText(
            source.source, source.values | values, translate_source=source.translate_source
        )
    return TranslatedText(source, values)


def join_translated(separator: str, values: Iterable[str]) -> str:
    """Join UI fragments, retaining only explicitly translated fragments as bindings."""
    parts = tuple(values)
    if not any(isinstance(part, TranslatedText) for part in parts):
        return separator.join(parts)
    # Place user text and separators only in values, never in a format/catalog key.
    source = "{separator}".join("{" + f"part_{index}" + "}" for index in range(len(parts)))
    fields = {f"part_{index}": part for index, part in enumerate(parts)}
    fields["separator"] = str(separator)
    return TranslatedText(source, fields, translate_source=False)


def install_qt_translation(app) -> None:
    """Translate Qt's built-in buttons and dialogs with the shipped Qt catalog."""
    from PySide6.QtCore import QLibraryInfo, QTranslator

    old = getattr(app, "_lextide_translator", None)
    if old is not None:
        app.removeTranslator(old)
        app._lextide_translator = None
        old.deleteLater()
    if _language != "zh_CN":
        return
    translator = QTranslator(app)
    path = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    if translator.load(str(path / "qtbase_zh_CN.qm")):
        app.installTranslator(translator)
        app._lextide_translator = translator
