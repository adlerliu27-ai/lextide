"""BUG-C10-1 punctuation variants; all dialogue fixtures are self-authored."""

from dataclasses import asdict

import pytest

from lextide.cleaning import clean_english
from lextide.config import Settings
from lextide.errors import InputError
from lextide.reading import validate_reading_text

MODE = "remove_chinese_translations"
BODY = "我是 Fred Goodwin 博士。"


@pytest.mark.parametrize(
    "separator",
    ["：", ":", ": ", " ： ", " : ", "\t:\t", "\u00a0：\u3000"],
)
def test_speaker_colon_and_whitespace_variants_remove_only_translation(separator):
    translation = f"Goodwin 博士{separator}{BODY}"
    source = f"The session begins.\n{translation}\nWe take notes."
    result = clean_english(source, mode=MODE)
    assert result.original_text == source
    assert result.cleaned_text == "The session begins.\nWe take notes."
    assert result.removed_chinese_lines == 1
    assert clean_english(result.cleaned_text, mode=MODE).cleaned_text == result.cleaned_text
    assert not any(note["reason"] == "mixed_chinese_retained" for note in result.warnings)
    assert asdict(result) == asdict(clean_english(source, mode=MODE))
    validate_reading_text(result.cleaned_text, Settings(), english_only=True)


@pytest.mark.parametrize("colon", ["：", ":", " ：", " :"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_split_speaker_label_removed_with_adjacent_translation(colon, newline):
    source = newline.join(["The session begins.", f"Goodwin 博士{colon}", BODY, "We take notes."])
    result = clean_english(source, mode=MODE)
    assert result.original_text == source
    assert result.cleaned_text == "The session begins.\nWe take notes."
    assert result.removed_chinese_lines == 2
    removals = [c for c in result.changes if c["reason"] == "chinese_translation_segment_removed"]
    assert {c["line"] for c in removals} == {2, 3}
    assert all(c["removed_characters"] > 0 for c in removals)
    assert not any(note["reason"] == "mixed_chinese_retained" for note in result.warnings)
    assert not any("Goodwin" in str(note) for note in result.changes + result.warnings)
    assert clean_english(result.cleaned_text, mode=MODE).cleaned_text == result.cleaned_text


@pytest.mark.parametrize("body", ["好的。", "我是 Fred Goodwin 博士。", "我叫 Clara Bennett。"])
def test_split_speaker_keeps_existing_body_evidence(body):
    source = f"Bennett 教授:\n{body}"
    assert clean_english(source, mode=MODE).cleaned_text == ""


@pytest.mark.parametrize("between", [" ", "\n"])
@pytest.mark.parametrize("split_label", ["", "\n"])
def test_two_speakers_are_classified_independently(between, split_label):
    # QA reported a two-speaker failure but did not retain its original fixture.
    source = (
        f"Goodwin 博士:{split_label}{BODY}{between}Bennett 教授：{split_label}我叫 Clara Bennett。"
    )
    result = clean_english(source, mode=MODE)
    assert result.cleaned_text == ""
    assert result.original_text == source
    assert clean_english(result.cleaned_text, mode=MODE).cleaned_text == ""


@pytest.mark.parametrize("colon", ["：", ":"])
def test_english_speaker_turn_survives_next_chinese_turn(colon):
    source = f"Goodwin 博士{colon} Keep every English word. Bennett 教授{colon}我叫 Clara Bennett。"
    expected = f"Goodwin 博士{colon} Keep every English word."
    assert clean_english(source, mode=MODE).cleaned_text == expected
    assert clean_english(expected, mode=MODE).cleaned_text == expected


@pytest.mark.parametrize(
    "source",
    [
        "Goodwin 博士:",
        "Goodwin 博士：",
        "Goodwin 博士:\nKeep every English word.",
        "Goodwin 博士：\nKeep every English word.",
        "Goodwin 博士:\n\n我是 Fred Goodwin 博士。",
        "Goodwin 博士:\nI studied at 北京大学 in 2025.",
        "Goodwin 博士: I use Python 和 AI。",
        "I studied at 北京大学 in 2025.",
        "Visit https://example.invalid/北京大学 at 12:30.",
        'He wrote, "Goodwin 博士: 我是 Fred Goodwin 博士。"',
        "`Goodwin 博士: 我是 Fred Goodwin 博士。`",
        "```text\nGoodwin 博士:\n我是 Fred Goodwin 博士。\n```",
        "    Goodwin 博士:\n    我是 Fred Goodwin 博士。",
        "Goodwin 博士:\n    我是 Fred Goodwin 博士。",
    ],
)
def test_uncertain_labels_english_names_urls_and_code_keep_review_guard(source):
    result = clean_english(source, mode=MODE)
    expected = "Goodwin 博士:" if "\n\n" in source else source
    assert result.cleaned_text == expected
    assert clean_english(expected, mode=MODE).cleaned_text == expected
    with pytest.raises(InputError, match="Chinese text remains"):
        validate_reading_text(result.cleaned_text, Settings(), english_only=True)


@pytest.mark.parametrize(
    "source",
    [
        "Host: Keep every English word.",
        "Host:\nKeep every English word.",
        "Open https://example.invalid/a:b at 12:30.",
        "12:30: Keep this time and all words.",
        "```text\nHost: Keep this code.\n```",
    ],
)
def test_english_colons_are_not_generic_translation_separators(source):
    assert clean_english(source, mode=MODE).cleaned_text == source


@pytest.mark.parametrize("colon", ["：", ":", " ： ", " : "])
@pytest.mark.parametrize("split_label", ["", "\n"])
def test_conservative_mode_retains_historical_speaker_behavior(colon, split_label):
    source = f"Goodwin 博士{colon}{split_label}{BODY}"
    expected = "\n".join(line.strip() for line in source.splitlines())
    default = clean_english(source)
    explicit = clean_english(source, mode="conservative")
    assert default.cleaned_text == expected
    assert asdict(default) == asdict(explicit)
    assert default.version == "english-rules-v2"
    assert any(note["reason"] == "mixed_chinese_retained" for note in default.warnings)
