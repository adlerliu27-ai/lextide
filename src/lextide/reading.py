"""Local, mutation-free checks on the exact text about to be synthesized.

This is a narrow Chinese/English workflow guard, not a language detector. It
never deletes characters or replaces the reviewed text, and doesn't impose an
English-only policy on other voice locales.
"""

from .config import Settings
from .errors import InputError

CHINESE_TEXT_MESSAGE = (
    "Chinese text remains in the reading text. Choose English Only and review or edit "
    "the preview before generating. No text was sent."
)
EMPTY_TEXT_MESSAGE = "Reading text is empty. Enter or extract English text before generating."


def contains_chinese(text: str) -> bool:
    """Include Han extensions/compatibility forms, not just the common BMP block."""
    return any(
        char == "\u3007"  # Ideographic zero, also used in Chinese names/dates.
        or "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        or "\U00020000" <= char <= "\U0002fa1f"
        or "\U00030000" <= char <= "\U000323af"
        for char in text
    )


def validate_reading_text(text: str, settings: Settings, *, english_only: bool = False) -> None:
    """Reject unresolved Chinese for English Only or a known English voice.

    Settings validation remains the caller's responsibility. Unknown/non-English
    voices are not classified as English here; this does not add new voice support.
    """
    if not isinstance(text, str) or not text.strip():
        raise InputError(EMPTY_TEXT_MESSAGE)
    if type(english_only) is not bool:
        raise InputError("English Only must be a boolean.")
    english_voice = (
        settings.engine == "edge-tts" and settings.voice.lower().startswith("en-")
    ) or (
        settings.engine == "kokoro"
        and len(settings.voice) > 3
        and settings.voice[:3] in {"af_", "am_", "bf_", "bm_"}
    )
    if (english_only or english_voice) and contains_chinese(text):
        raise InputError(CHINESE_TEXT_MESSAGE)
