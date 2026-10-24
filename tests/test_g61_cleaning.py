"""Self-authored translation fixtures; never store the private reproduction article."""

from dataclasses import asdict

import pytest

from lextide.cleaning import clean_english
from lextide.errors import InputError

MODE = "remove_chinese_translations"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Practice every day.每天练习。", "Practice every day."),
        (
            "In 1921, Lewis Terman conducted a study.1921年，Lewis Terman开展了一项研究。",
            "In 1921, Lewis Terman conducted a study.",
        ),
        (
            "Maya Stone recorded 1,500 birds.玛雅记录了1,500只鸟。",
            "Maya Stone recorded 1,500 birds.",
        ),
        (
            "Maya Stone recorded 1,500 birds.玛雅记录了1,500只鸟。"
            "She returned in 1921.1921年，她再次回来。",
            "Maya Stone recorded 1,500 birds. She returned in 1921.",
        ),
        (
            "The project began in 1921.项目始于1921年，Lewis Terman带领孩子们一起观察。",
            "The project began in 1921.",
        ),
        (
            "The study involved Lewis Terman.\nLewis Terman带领孩子们开展观察研究。",
            "The study involved Lewis Terman.",
        ),
        ("Practice every day. | 每天练习。", "Practice every day."),
        ("Practice every day | 每天练习", "Practice every day"),
        ("Practice every day：每天练习。", "Practice every day"),
        ("每天练习：Practice every day.", "Practice every day."),
        ("每天练习。Practice every day.", "Practice every day."),
        (
            "Python and C++ use APIs. Python和C++都能够使用API接口。",
            "Python and C++ use APIs.",
        ),
        (
            "Keep the SHA-256 value on GitHub.请在GitHub上保留SHA-256校验值。",
            "Keep the SHA-256 value on GitHub.",
        ),
        (
            "A clear API is helpful.清楚的API接口很有帮助。More tests help.更多测试有帮助。",
            "A clear API is helpful. More tests help.",
        ),
        ("Learning Python is useful. 学习Python很有用。", "Learning Python is useful."),
        ("**Practice** daily.\n• 每天使用Python练习英语。", "Practice daily."),
        ("1921年，Lewis Terman带领孩子们开展观察研究。", ""),
        ("全中文内容。另一个译文句子。", ""),
    ],
)
def test_explicit_translation_mode_preserves_complete_english_in_order(source, expected):
    result = clean_english(source, mode=MODE)
    assert result.original_text == source
    assert result.cleaned_text == expected
    assert result.mode == MODE
    assert result.version == "english-translations-v1"
    assert result.removed_chinese_segments > 0
    assert asdict(result) == asdict(clean_english(source, mode=MODE))


@pytest.mark.parametrize(
    "source",
    [
        "Don't change C++, C#, API, SHA-256, GitHub or Python 3.13.",
        'He said, "Practice every day." We need 1,500 examples.',
        "The U.S. agency tested 3.14 units, e.g. the API.",
        "English | Another English column：an English explanation.",
        "Use 中文 as the exact label.",
        "I visited 北京 last year.",
        "I studied at 北京大学。",
        "She met Dr. 王。",
        "中文说明 includes an important English phrase.",
        "这段中文中包含 I will keep this sentence. 请不要丢失英文内容。",
        "A title中文翻译",  # No sentence boundary or explicit separator.
        'Keep the label "你好。欢迎。" in the displayed sentence.',
        "Keep (你好。欢迎。) in the displayed sentence.",
        "Keep [你好。欢迎。] in the displayed sentence.",
        "The kana カタカナ and 中文 stay together.",
        "学习 Python is useful for everyone。",  # Genuine English predicate.
    ],
)
def test_ambiguous_and_technical_content_is_not_deleted(source):
    result = clean_english(source, mode=MODE)
    # Existing annotation removal is an independent conservative feature; square
    # brackets and quotations remain protected even in enhanced mode.
    if source.startswith("Keep (你好"):
        assert result.cleaned_text == "Keep in the displayed sentence."
    elif source.startswith("这段中文中包含"):
        assert "I will keep this sentence." in result.cleaned_text
    else:
        assert result.cleaned_text == source
    if any("\u3400" <= char <= "\u9fff" for char in result.cleaned_text):
        assert any(item["reason"] == "mixed_chinese_retained" for item in result.warnings)


def test_default_and_explicit_conservative_retain_historical_behavior():
    source = "Maya Stone recorded birds.玛雅使用Python记录了鸟类。"
    default = clean_english(source)
    explicit = clean_english(source, mode="conservative")
    assert default.cleaned_text == source
    assert default.version == "english-rules-v1"
    assert default.mode == "conservative"
    assert asdict(default) == asdict(explicit)


def test_translation_removals_are_reviewable_without_audit_text_leak():
    source = "Keep the study.\r\n1921年，Lewis Terman带领孩子们开展观察研究。"
    result = clean_english(source, mode=MODE)
    removed = [c for c in result.changes if c["reason"] == "chinese_translation_segment_removed"]
    assert removed and removed[0]["line"] == 2
    assert removed[0]["removed_characters"] > 0
    assert result.removed_chinese_lines == 1
    assert result.removed_chinese_segments == 1
    assert any(w["reason"] == "translation_with_latin_removed" for w in result.warnings)
    audit = str(result.changes + result.warnings)
    assert "Lewis" not in audit and "带领" not in audit
    assert result.original_text == source


@pytest.mark.parametrize("mode", ["", "aggressive", "original", None, 1])
def test_unknown_strength_is_rejected(mode):
    with pytest.raises(InputError):
        clean_english("Keep all words.", mode=mode)


def test_enhanced_cleanup_never_uses_network(monkeypatch):
    import socket

    def deny(*args, **kwargs):
        raise AssertionError("Cleaner must stay offline")

    monkeypatch.setattr(socket, "socket", deny)
    assert (
        clean_english("Keep all words.保留全部单词。", mode=MODE).cleaned_text == "Keep all words."
    )


@pytest.mark.parametrize(
    "source",
    [
        "We measured 3.14年 in this mixed example.",
        "Use A|B中文 as the exact expression.",
        "Keep All Words 中文正文不能丢失。",
        "Mark the place as [北京大学。] in the record.",
        "This English sentence has 李小明 as its subject.",
        "保留API!这个英文片段需要人工检查。",
    ],
)
def test_enhanced_mode_retains_unresolved_technical_or_sentence_fragments(source):
    result = clean_english(source, mode=MODE)
    if source.startswith("保留API!"):
        assert result.cleaned_text == "保留API!"
    else:
        assert result.cleaned_text == source
    assert any(note["reason"] == "mixed_chinese_retained" for note in result.warnings)


def test_many_adjacent_translation_fragments_keep_all_english_in_order():
    source = "Keep the API.请保留API的完整接口。" * 3000
    result = clean_english(source, mode=MODE)
    assert result.cleaned_text == " ".join(["Keep the API."] * 3000)
    assert result.removed_chinese_segments == 3000
    assert result.original_text == source
