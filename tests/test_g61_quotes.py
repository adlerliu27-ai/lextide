"""G6-1 source-bounded quote omissions; synthetic metadata, no online requests."""

import hashlib
import json

import pytest

from lextide.engines.base import SentenceBoundary
from lextide.errors import ValidationError
from lextide.subtitles import prepare_subtitles


def prepare(source, texts, *, timing_source="edge-tts SentenceBoundary"):
    return prepare_subtitles(
        [SentenceBoundary(i * 10_000_000, 10_000_000, text) for i, text in enumerate(texts)],
        audio_duration_ms=len(texts) * 1000,
        expected_text=source,
        timing_source=timing_source,
    )


@pytest.mark.parametrize("separator", [", ", "; ", ",\n", ";\t"])
@pytest.mark.parametrize("wrapper", ["", "*", "**", ">"])
def test_report_structure_with_source_paired_quotes(separator, wrapper):
    ending = wrapper if wrapper in {"*", "**"} else ""
    source = (
        f'The tutor asks{separator}{wrapper}"You practised yesterday. What changed?"{ending} '
        "I explain my progress."
    )
    actual = [
        "The tutor asks" + separator.strip(),
        "You practised yesterday.",
        "What changed?",
        "I explain my progress.",
    ]
    before = hashlib.sha256(source.encode()).hexdigest()
    content, report = prepare(source, actual)
    assert report["text_coverage"]["verified"]
    assert report["text_coverage"]["normalization"] == "edge-quote-boundaries-v1"
    assert report["text_coverage"]["quote_boundary_omissions"]
    assert report["adjustments"] == []
    assert [line for line in content.splitlines() if "-->" in line] == [
        f"00:00:0{i},000 --> 00:00:0{i + 1},000" for i in range(4)
    ]
    assert [line for line in content.splitlines() if line in actual] == actual
    assert hashlib.sha256(source.encode()).hexdigest() == before
    assert json.loads(json.dumps(report))["text_coverage"]["verified"]


@pytest.mark.parametrize(
    "source, actual",
    [
        ('The teacher said, "Practice every day."', ["The teacher said,", "Practice every day."]),
        ('"Practice every day."', ["Practice every day."]),
        ('"Practice every day."', ['"Practice every day.']),
        (
            'The tutor said; "Practice, then rest. Don\'t stop."',
            ["The tutor said;", "Practice, then rest.", "Don't stop."],
        ),
        (
            'The tutor said, "Try again." Then said, "Try again tomorrow."',
            ["The tutor said,", "Try again.", "Then said,", "Try again tomorrow."],
        ),
        ('"First." "Second."', ["First.", '"Second."']),
        ('The teacher said, "Practice every day."', ["The teacher said,", 'Practice every day."']),
        (
            'The teacher said, **"Practice every day."**',
            ["The teacher said,", 'Practice every day."'],
        ),
        (
            'The teacher said, "Practice every day." Then rest.',
            ["The teacher said,", "Practice every day.", '" Then rest.'],
        ),
        ("The teacher said, “Practice every day.”", ["The teacher said,", "“Practice every day."]),
        ("The teacher said, 'Don't stop.'", ["The teacher said,", "'Don't stop."]),
        ("The teacher said, ‘Don’t stop.’", ["The teacher said,", "‘Don’t stop."]),
    ],
)
def test_single_multi_sentence_retained_and_omitted_closers(source, actual):
    assert prepare(source, actual)[1]["text_coverage"]["verified"]


@pytest.mark.parametrize(
    "source, actual",
    [
        ('The tutor said, "Practice every day."', ["The tutor said,", "Practice day."]),
        ('The tutor said, "Practice every day."', ["The tutor said,", "Practice each day."]),
        ('The tutor said, "First. Second."', ["The tutor said,", "Second.", "First."]),
        ('The tutor said, "First. Second."', ["The tutor said,", "First."]),
        ('The tutor said, "First. Second."', ["The tutor said,"]),
        ('The tutor said, "First. First."', ["The tutor said,", "First."]),
        ('The tutor said, "First. Second."', ["The tutor said,", "First.", "First."]),
        ('The tutor said, "Practice, then rest."', ["The tutor said,", "Practice then rest."]),
        ('The tutor said, "Don\'t stop."', ["The tutor said,", "Dont stop."]),
        ('The tutor said, "Practice every day."', ["The tutor said", "Practice every day."]),
        ('The tutor said, "First." Then "Second."', ["The tutor said,", "First.", "Second."]),
        (
            'The tutor said, "First." Then "Second."',
            ["The tutor said,", "First.", "Then", "Second."],
        ),
        ('The tutor said, "Practice every day."', ["The tutor said, Practice every day."]),
        ('The tutor said, "Practice." Rest.', ["The tutor said,", "Practice. Rest."]),
        ('The tutor said,"Practice."', ["The tutor said,", "Practice."]),
        ('The tutor said: "Practice."', ["The tutor said:", "Practice."]),
        ("The tutor said, **Practice.", ["The tutor said,", "Practice."]),
        ('The tutor said, "Practice.', ["The tutor said,", "Practice."]),
        ('Practice."', ["Practice."]),
        ('"Practice." extra', ["Practice."]),
        ('"Practice?"***', ["Practice?"]),
        ('**"Practice?"*', ["Practice?"]),
        ('"Practice" every day.', ["Practice", "every day."]),
        ('"Practice, " then rest.', ["Practice,", "then rest."]),
        ("Practice every day.", ['"Practice every day."']),
        ("The tutor said, Practice.", ["The tutor said,", '"Practice."']),
        ("The tutor said, 'Practice.'", ["The tutor said,", "Practice."]),
    ],
)
def test_body_damage_and_unconfirmed_quote_changes_still_fail(source, actual):
    with pytest.raises(ValidationError):
        prepare(source, actual)


def test_unchanged_source_without_quotes_keeps_exact_path():
    source = "The tutor said, Practice every day. Then rest."
    content, report = prepare(source, ["The tutor said,", "Practice every day.", "Then rest."])
    assert "Practice every day." in content
    assert report["text_coverage"]["normalization"].startswith("ignore whitespace only")


@pytest.mark.parametrize("timing_source", ["kokoro sentence PCM sample counts", "unknown"])
def test_offline_and_unknown_sources_do_not_gain_quote_tolerance(timing_source):
    source = 'The tutor said, "Practice every day."'
    with pytest.raises(ValidationError):
        prepare(source, ["The tutor said,", "Practice every day."], timing_source=timing_source)
    assert prepare(source, [source], timing_source=timing_source)[1]["text_coverage"]["verified"]


def test_quote_mismatch_error_does_not_disclose_body():
    with pytest.raises(ValidationError) as error:
        prepare('The tutor said, "PrivateWord appears here."', ["The tutor said,", "WrongWord."])
    assert "Sentence 2" in str(error.value)
    assert "character" in str(error.value)
    assert "PrivateWord" not in str(error.value)
    assert "WrongWord" not in str(error.value)


def test_long_quote_prefix_cannot_hide_body_loss():
    # G5 allows leading marker removal, but the new quote-pair pre-scan must
    # remain bounded for marker-heavy input and still reject missing words.
    with pytest.raises(ValidationError):
        prepare('"' * 20_000 + "Keep all words.", ["Keep words."])


@pytest.mark.parametrize(
    "source, actual",
    [
        ("The tutor said, \"First. 'Second.'\"", ["The tutor said,", "First.", "Second."]),
        (
            'The tutor said, "First. Then "second" is quoted."',
            ["The tutor said,", "First.", "Then second is quoted."],
        ),
        (
            'The tutor said, "First."\n\nA whole paragraph.\n\n"Last."',
            ["The tutor said,", "First.", "Last."],
        ),
        (
            'The tutor said, "First." Then said, "First. Second."',
            ["The tutor said,", "First.", "Then said,", "Second."],
        ),
    ],
)
def test_nested_quote_changes_and_missing_repeated_passages_are_rejected(source, actual):
    with pytest.raises(ValidationError):
        prepare(source, actual)
