"""Self-authored fixtures for G6; no speech service, network, or user material."""

from dataclasses import asdict

import pytest

from lextide.cleaning import MAX_INPUT_BYTES, CleanResult, clean_english
from lextide.errors import InputError


@pytest.mark.parametrize(
    ("source", "expected", "removed", "warning_codes"),
    [
        pytest.param(
            "Dr. Smith said, \"I can't use C++ 3.14, e.g. here.\"\nR&D isn't C#.",
            "Dr. Smith said, \"I can't use C++ 3.14, e.g. here.\"\nR&D isn't C#.",
            0,
            set(),
            id="T01-pure-English-quotes-technical-punctuation",
        ),
        pytest.param(
            "Learning is useful.\n学习很有用。\nPractice every day.\n每天练习。",
            "Learning is useful.\nPractice every day.",
            2,
            set(),
            id="T02-alternating-English-Chinese-lines",
        ),
        pytest.param(
            "Learning English takes time.\n学习英语需要时间。\n\n"
            "**First**, listen carefully.\n首先，仔细听。\n\n"
            "• Practice every day.\n• 每天坚持练习。",
            "Learning English takes time.\n\nFirst, listen carefully.\n\nPractice every day.",
            3,
            set(),
            id="T02-full-user-example-with-Unicode-bullets",
        ),
        pytest.param(
            "Learning Python is useful. 学习 Python 很有用。",
            "Learning Python is useful.",
            0,
            set(),
            id="T03-inline-translation-repeated-technical-name",
        ),
        pytest.param(
            "# **C++** notes\n- *Don't* change `foo_bar` or v2.1.\n"
            '2. __Keep__ "quotes".\n> Hello.',
            'C++ notes\nDon\'t change foo_bar or v2.1.\nKeep "quotes".\nHello.',
            0,
            set(),
            id="T04-markdown-retains-words-and-punctuation",
        ),
        pytest.param(
            "Python（Python 编程语言）is widely used.\n"
            "This word（单词）has an English gloss (word).",
            "Python is widely used.\nThis word has an English gloss (word).",
            0,
            set(),
            id="T05-Chinese-parenthetical-glosses",
        ),
        pytest.param(
            "This is an\nexample of the\nconservative joining rule.\nNext sentence.",
            "This is an example of the conservative joining rule.\nNext sentence.",
            0,
            set(),
            id="T06-clear-OCR-function-word-continuation",
        ),
        pytest.param(
            "Use 中文 as the exact label.\n学习 Python 很有用。\nKeep this. 保留 new content。",
            "Use 中文 as the exact label.\n学习 Python 很有用。\nKeep this. 保留 new content。",
            0,
            {"mixed_chinese_retained"},
            id="T07-ambiguous-mixed-content-is-retained",
        ),
        pytest.param(
            "全中文输入。\n第二行，含数字 2026。",
            "",
            2,
            {"no_english_text"},
            id="T08-all-Chinese-is-empty-not-invented-English",
        ),
        pytest.param("", "", 0, {"no_english_text"}, id="T09-empty-input"),
        pytest.param(
            "\ufeff  Hello\u00a0\tworld.\r\n\r\n\r\nNext\fline.  \r\n",
            "Hello world.\n\nNext line.",
            0,
            set(),
            id="T10-BOM-nbsp-CRLF-whitespace",
        ),
    ],
)
def test_required_cases_are_deterministic(source, expected, removed, warning_codes):
    first = clean_english(source)
    assert isinstance(first, CleanResult)
    assert first.original_text == source
    assert first.cleaned_text == expected
    assert first.version == "english-rules-v1"
    assert first.removed_chinese_lines == removed
    assert {record["reason"] for record in first.warnings} == warning_codes
    assert asdict(first) == asdict(clean_english(source))


def test_deletions_keep_original_line_numbers_and_never_copy_source_into_audit():
    original = "# Local secret\r\n本地私密行。\r\nHello world. 你好世界。\r\nPage 2 of 5"
    result = clean_english(original)
    assert result.original_text == original
    assert result.cleaned_text == "Local secret\nHello world."
    deletions = {(change["line"], change["reason"]) for change in result.changes}
    assert (1, "markdown_prefix_removed") in deletions
    assert (2, "chinese_line_removed") in deletions
    assert (3, "chinese_translation_suffix_removed") in deletions
    assert (4, "page_marker_removed") in deletions
    for record in result.changes + result.warnings:
        assert isinstance(record["line"], int)
        assert set(record) <= {"line", "reason", "operation", "previous_line"}
        assert "secret" not in str(record)
        assert "私密" not in str(record)


@pytest.mark.parametrize(
    "text",
    [
        "C++ and C# keep +, #, &, /, 3.14, e.g., Mr. Smith and U.S. values.",
        "\"Double quotes\", 'single quotes', curly “quotes”, and don’t.",
        "snake_case and a_b_c, x*y*z, https://example.invalid/a_b stay intact.",
        "+ 2\n- 3\n> 5\n3.14 is pi.",
        "2026. A year followed by a sentence.",
        "This is a\n\nseparate paragraph.",
        "Words can be inter-\nnational, but do not guess spelling.",
        "Learning Python is useful. 学习 Java 很有用。",
        "I like Python. 中文 I. like. Python。",
        "She met Dr. 王。",
        "We are in the U.S. 美国。",
        "Keep (API 接口) as written.",
        "Keep (nested（中文）annotation) intact.",
        "The kana カタカナ and 中文 stay together.",
    ],
)
def test_conservative_cases_preserve_text(text):
    assert clean_english(text).cleaned_text == text


def test_inline_code_content_is_protected_from_emphasis_rules():
    result = clean_english("Use `a * b * c` and `foo_bar` with ***bold*** and _italic_.")
    assert result.cleaned_text == "Use a * b * c and foo_bar with bold and italic."


def test_ocr_preserves_list_boundaries_after_removing_markers():
    result = clean_english("This is an\n- example from a list.")
    assert result.cleaned_text == "This is an\nexample from a list."
    assert not any(change["reason"] == "ocr_line_joined" for change in result.changes)


def test_parenthetical_removal_does_not_leave_a_space_before_punctuation():
    assert clean_english("A useful word (词语). Next.").cleaned_text == "A useful word. Next."


def test_ocr_does_not_join_across_deleted_lines_or_pages():
    result = clean_english("This is an\n中文对照。\nexample.\nThis is an\nPage 2 of 5\nexample.")
    assert result.cleaned_text == "This is an\nexample.\nThis is an\nexample."
    assert not any(change["reason"] == "ocr_line_joined" for change in result.changes)


def test_numbers_and_unknown_noise_are_retained_with_warnings():
    result = clean_english("Page 2 of 5\n42\nPage 5 of 2\n~~~\nHello\x00world.\nHidden\u200bword.")
    assert result.cleaned_text == "42\nPage 5 of 2\n~~~\nHello world.\nHidden\u200bword."
    assert {(item["line"], item["reason"]) for item in result.warnings} == {
        (2, "numeric_line_retained"),
        (3, "ambiguous_page_marker_retained"),
        (4, "unresolved_markdown_retained"),
        (4, "unrecognized_noise_retained"),
        (5, "control_character_replaced"),
        (6, "hidden_character_retained"),
    }


def test_cleanup_has_no_network_dependency(monkeypatch):
    import socket

    def deny_network(*args, **kwargs):
        raise AssertionError("English cleanup must remain local")

    monkeypatch.setattr(socket, "socket", deny_network)
    assert clean_english("Hello world. 你好世界。").cleaned_text == "Hello world."


@pytest.mark.parametrize("text", [None, 42, b"Hello", "\ud800"])
def test_invalid_inputs_are_safe_input_errors(text):
    with pytest.raises(InputError):
        clean_english(text)


def test_input_bound_counts_utf8_bytes_and_accepts_exact_boundary():
    assert clean_english("A" * MAX_INPUT_BYTES).cleaned_text == "A" * MAX_INPUT_BYTES
    with pytest.raises(InputError, match="1 MiB"):
        clean_english("A" * (MAX_INPUT_BYTES + 1))
    with pytest.raises(InputError, match="1 MiB"):
        clean_english("中" * (MAX_INPUT_BYTES // 3 + 1))


def test_each_result_owns_its_mutable_audit_lists():
    first, second = clean_english("Hello."), clean_english("Hello.")
    first.changes.append({"line": 1, "reason": "test_only"})
    assert second.changes == []


def test_long_ocr_sequence_retains_order_and_trace():
    source = "This is the\n" + "continuation of the\n" * 10_000 + "sentence."
    result = clean_english(source)
    assert result.cleaned_text == source.replace("\n", " ")
    joins = [item for item in result.changes if item["reason"] == "ocr_line_joined"]
    assert len(joins) == 10_001
    assert joins[-1]["line"] == 10_002
    assert joins[-1]["previous_line"] == 10_001


def test_large_numeric_page_marker_cannot_trigger_python_integer_conversion_limit():
    assert clean_english("Page " + "9" * 5000 + " of " + "9" * 5000).cleaned_text == ""
