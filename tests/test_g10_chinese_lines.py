"""BUG-C10-1. Remove the bounded translation, preserving English around it."""

import asyncio
import hashlib
from dataclasses import asdict

import pytest

from lextide.cleaning import clean_english
from lextide.config import Settings
from lextide.errors import InputError
from lextide.reading import validate_reading_text

MODE = "remove_chinese_translations"
SPEAKER = "Goodwin 博士：我是 Fred Goodwin 博士。"


@pytest.mark.parametrize(
    "translation",
    [
        SPEAKER,
        "Bennett 教授：我叫 Clara Bennett。",
        "Bennett 教授：“我叫 Clara Bennett。”",
        "他们采访了 Alexandra Monroe。",
        "我们使用 Python 和 AI。",
        "在 2025 年，研究人员记录了 1,500 个样本。",
        "这是自撰的中文译文。",
    ],
)
def test_translation_removed_on_its_own_and_between_english_lines(translation):
    assert clean_english(translation, mode=MODE).cleaned_text == ""
    original = f"The session begins.\n{translation}\nWe take notes."
    result = clean_english(original, mode=MODE)
    assert result.original_text == original
    assert result.cleaned_text == "The session begins.\nWe take notes."
    assert result.removed_chinese_lines == 1
    assert any(c["line"] == 2 for c in result.changes)
    assert all(translation not in str(note) for note in result.changes + result.warnings)


def test_speaker_sentence_consumed_only_to_its_own_boundary():
    source = "We met Dr. Reed. " + SPEAKER + ' We said, "Check the API."'
    assert (
        clean_english(source, mode=MODE).cleaned_text
        == 'We met Dr. Reed. We said, "Check the API."'
    )


@pytest.mark.parametrize(
    "source",
    [
        "I studied at 北京大学 in 2025.",
        "The students met in 北京 yesterday.",
        "Goodwin 博士：Keep every English word.",
        'He wrote, "Goodwin 博士：我是 Fred Goodwin 博士。"',
        "They printed the label `Goodwin 博士` twice.",
        "`Goodwin 博士：我是 Fred Goodwin 博士。`",
        "‘Goodwin 博士’ is the literal string in this API.",
        "Goodwin 博士：I use Python 和 AI。",
        "北京大学 held an English debate in 2025.",
        "他们介绍了“Practice English Daily”。",
        "Goodwin 博士：他们介绍了“Practice English Daily”。",
    ],
)
def test_ambiguous_names_and_real_english_require_review(source):
    result = clean_english(source, mode=MODE)
    # Keep Chinese literal code marked so a second cleanup cannot misclassify it.
    assert result.cleaned_text == source
    assert clean_english(result.cleaned_text, mode=MODE).cleaned_text == source
    assert any(note["reason"] == "mixed_chinese_retained" for note in result.warnings)
    with pytest.raises(InputError):
        validate_reading_text(result.cleaned_text, Settings(), english_only=True)


def test_conservative_mode_still_flags_the_speaker_line():
    result = clean_english(SPEAKER)
    assert result.cleaned_text == SPEAKER
    assert any(note["reason"] == "mixed_chinese_retained" for note in result.warnings)


@pytest.mark.parametrize("settings", [Settings(), Settings(engine="kokoro", voice="af_heart")])
def test_uncertain_cleaned_text_is_blocked_before_engine_or_job(settings, tmp_path, monkeypatch):
    from lextide.desktop import service

    source = "I studied at 北京大学 in 2025."
    result = clean_english(source, mode=MODE)
    assert result.original_text == result.cleaned_text == source
    monkeypatch.setattr(service, "LongEngine", lambda *_a, **_k: pytest.fail("Engine reached"))
    with pytest.raises(InputError, match="Chinese text remains"):
        asyncio.run(
            service.start(
                {
                    "text": result.cleaned_text,
                    "settings": asdict(settings),
                    "english_only": True,
                    "approved_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "allow_online": settings.engine == "edge-tts",
                    "out_dir": str(tmp_path / "jobs"),
                },
                lambda *_: None,
            )
        )
    assert not (tmp_path / "jobs").exists()
