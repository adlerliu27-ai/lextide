import json

import pytest

from lextide.engines.base import SentenceBoundary
from lextide.errors import ValidationError
from lextide.subtitles import prepare_subtitles, validate_srt


@pytest.mark.parametrize("text", ["Use &amp; literally.", "A &not; B.", "A &copy; B."])
def test_public_metadata_preserves_literal_entities(text):
    srt, report = prepare_subtitles(
        [SentenceBoundary(0, 10_000_000, text)], audio_duration_ms=1000, expected_text=text
    )
    assert text in srt
    assert report["text_coverage"]["verified"]


def boundary(start_ms, end_ms, text):
    return SentenceBoundary(start_ms * 10_000, (end_ms - start_ms) * 10_000, text)


def test_native_overlap_and_small_audio_overshoot_are_recorded():
    content, report = prepare_subtitles(
        [boundary(100, 1050, "First."), boundary(1000, 2025, "Second!")],
        audio_duration_ms=2000,
        expected_text="First. Second!",
    )
    assert "00:00:00,100 --> 00:00:01,000" in content
    assert "00:00:01,000 --> 00:00:02,000" in content
    assert report["adjustments"] == [
        {
            "cue_index": 1,
            "kind": "cap_overlap",
            "original_end_ms": 1050,
            "adjusted_end_ms": 1000,
            "delta_ms": -50,
        },
        {
            "cue_index": 2,
            "kind": "cap_audio_end",
            "original_end_ms": 2025,
            "adjusted_end_ms": 2000,
            "delta_ms": -25,
        },
    ]
    assert report["timing_source"] == "edge-tts SentenceBoundary"
    assert report["text_coverage"]["verified"] is True
    assert json.loads(json.dumps(report))["last_end_ms"] == 2000
    offline = validate_srt(content, audio_duration_ms=2000)
    assert offline["cue_count"] == 2
    assert offline["text_coverage"]["verified"] is False


def test_rounding_and_unicode_preserve_punctuation():
    content, report = prepare_subtitles(
        [SentenceBoundary(1_000_001, 8_999_998, "中文，\n\n café & tea！")],
        audio_duration_ms=1100,
        expected_text="中文，café & tea！",
    )
    assert "00:00:00,100 --> 00:00:01,000" in content
    assert "中文， café & tea！\n" in content
    assert report["adjustments"] == []
    assert validate_srt(content, audio_duration_ms=1100)["valid"] is True


@pytest.mark.parametrize(
    "boundaries, expected",
    [
        ([], "Hello."),
        ([boundary(0, 1000, "Hello.")], "Hello. Missing sentence."),
        ([boundary(0, 1000, "Hello")], "Hello."),
        ([boundary(1000, 1500, "One."), boundary(500, 900, "Two.")], "One. Two."),
        ([boundary(0, 1000, "One."), boundary(0, 1000, "Two.")], "One. Two."),
        ([boundary(0, 1200, "One."), boundary(1000, 1500, "Two.")], "One. Two."),
        ([boundary(-1, 500, "Hello.")], "Hello."),
        ([boundary(0, 0, "Hello.")], "Hello."),
        ([boundary(0, 1000, " ")], "Hello."),
        ([SentenceBoundary(0, 100_000, "One."), SentenceBoundary(1, 100_000, "Two.")], "One. Two."),
    ],
)
def test_missing_invalid_duplicate_and_reversed_metadata_fail(boundaries, expected):
    with pytest.raises(ValidationError):
        prepare_subtitles(boundaries, audio_duration_ms=2000, expected_text=expected)


@pytest.mark.parametrize(
    "start, end, audio",
    [
        (0, 2101, 2000),  # Overshoot exceeds the repair allowance.
        (0, 1000, 4000),  # Excessive trailing gap.
        (2001, 3000, 3100),  # Excessive leading gap.
        (2000, 2050, 2000),  # Capping cannot create a zero-duration cue.
    ],
)
def test_timing_mismatch_fails(start, end, audio):
    with pytest.raises(ValidationError):
        prepare_subtitles(
            [boundary(start, end, "Hello.")], audio_duration_ms=audio, expected_text="Hello."
        )


@pytest.mark.parametrize("duration", [0, -1, 600001, 1000.0, float("nan"), float("inf"), True])
def test_invalid_audio_duration_fails(duration):
    with pytest.raises(ValidationError):
        prepare_subtitles(
            [boundary(0, 1000, "Hello.")], audio_duration_ms=duration, expected_text="Hello."
        )
    with pytest.raises(ValidationError):
        validate_srt("1\n00:00:00,000 --> 00:00:01,000\nHello.\n", audio_duration_ms=duration)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "1\n00:00:00,000 --> 00:00:01,000\n",  # Missing text.
        "2\n00:00:00,000 --> 00:00:01,000\nHello.",
        "1\n00:00:00.000 --> 00:00:01.000\nHello.",
        "1\n00:00:60,000 --> 00:01:01,000\nHello.",
        "1\n00:00:01,000 --> 00:00:01,000\nHello.",
        "1\n00:00:00,000 --> 00:00:03,001\nHello.",
        "1\n00:00:00,000 --> 00:00:01,050\nOne.\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nTwo.",  # Offline validation never repairs.
        "1\n00:00:00,000 --> 00:00:01,000\nOne.\n\n2\n00:00:00,000 --> 00:00:01,000\nOne.",
    ],
)
def test_malformed_srt_fails(content):
    with pytest.raises(ValidationError):
        validate_srt(content, audio_duration_ms=3000)


def test_repeated_spoken_text_at_distinct_times_is_valid():
    content, report = prepare_subtitles(
        [boundary(0, 1000, "Again."), boundary(1000, 2000, "Again.")],
        audio_duration_ms=2000,
        expected_text="Again. Again.",
    )
    assert report["cue_count"] == 2
    assert report["adjustments"] == []
    assert validate_srt(content.replace("\n", "\r\n"), audio_duration_ms=2000)["valid"]
