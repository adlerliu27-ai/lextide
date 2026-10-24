"""BUG-G5-1: recorded Edge metadata examples, not live network tests."""

import json

import pytest

from lextide.engines.base import SentenceBoundary
from lextide.errors import ValidationError
from lextide.subtitles import prepare_subtitles

MATRIX = [
    ("**First** part, then text.", "First** part, then text."),
    ("*Italic* lead then text.", "Italic* lead then text."),
    ("- Bullet item one.", "Bullet item one."),
    ("• Bullet with dot.", "Bullet with dot."),
    ("> Quoted line here.", "Quoted line here."),
    ('"Quoted" start here.', 'Quoted" start here.'),
    ("`code` at start here.", "code` at start here."),
    ("— Dash leads this.", "Dash leads this."),
    ("# Heading line.", "# Heading line."),
    ("1. Numbered item one.", "1. Numbered item one."),
    ("🙂 Emoji leads this.", "🙂 Emoji leads this."),
    ("A sentence with **bold** in it.", "A sentence with **bold** in it."),
]


def prepare(expected, texts, *, timing_source="edge-tts SentenceBoundary"):
    return prepare_subtitles(
        [SentenceBoundary(i * 10_000_000, 10_000_000, text) for i, text in enumerate(texts)],
        audio_duration_ms=1000 * len(texts),
        expected_text=expected,
        timing_source=timing_source,
    )


@pytest.mark.parametrize("expected, actual", MATRIX)
def test_recorded_twelve_case_matrix(expected, actual):
    content, report = prepare(expected, [actual])
    assert actual in content  # Never substitute reviewed text for public metadata.
    assert "00:00:00,000 --> 00:00:01,000" in content
    assert report["text_coverage"]["verified"]
    assert report["text_coverage"]["expected_characters"] == len("".join(expected.split()))
    assert report["text_coverage"]["actual_characters"] == len("".join(actual.split()))
    assert report["adjustments"] == []
    assert json.loads(json.dumps(report)) == report


def test_multiple_sentences_and_paragraphs_consumed_in_order():
    expected = "**First** part. > Second sentence.\n\n• Third paragraph.\n- Fourth item."
    actual = ["First** part.", "Second sentence.", "Third paragraph.", "Fourth item."]
    content, report = prepare(expected, actual)
    assert report["cue_count"] == 4
    assert report["last_end_ms"] == 4000
    assert [line for line in content.splitlines() if "-->" in line] == [
        f"00:00:0{i},000 --> 00:00:0{i + 1},000" for i in range(4)
    ]
    coverage = report["text_coverage"]
    assert coverage["normalization"] == "edge-leading-markers-v1"
    assert len(coverage["leading_marker_omissions"]) == 4


@pytest.mark.parametrize(
    "expected, actual",
    [
        ("One. • Missing. Three.", ["One.", "Three."]),
        ("One.\n\n• Missing paragraph with several words.\n\nThree.", ["One.", "Three."]),
        ("**First** part. Second.", ["First** part."]),
        ("First. Second.", ["Second.", "First."]),
        ("First. Second.", ["First.", "First."]),
        ("**First** part, then text.", ["First** then text."]),
        ("**First** part, then text.", ["First** section, then text."]),
        ("**First** part, then text.", ["First** part then text."]),
        ("**First** part, then text.", ["First part, then text."]),
        ("**First** part, then text.", ["First** part, then"]),
        ("> Original words.", ["Different words."]),
        ("One. **Again. Again.", ["One.", "Again."]),  # Cannot reuse the same sentence.
        ("One. Again. **Again.", ["One.", "Again.", "Again.", "Again."]),
        ("First. — Tail.", ["First.", "Tail.", "Tail."]),
        ("First. **", ["First."]),  # Trailing marks cannot be silently discarded.
        ("**First**.", ["*First*."]),  # Closing/interior markers must stay exact.
        ("First.", ["> First."]),  # No invented prefixes.
        ("- First.", ["> First."]),  # No substituted prefixes.
        ("• First.", ["First. •"]),  # No relocated prefixes.
        ("First **bold** here.", ["First", "bold** here."]),  # Forged mid-sentence cue.
        ("First-word.", ["First", "word."]),  # Forged mid-word cue.
        ("Value -3.", ["Value", "3."]),
        ("-3 degrees.", ["3 degrees."]),  # A sign is not a list marker.
        ("- 3 degrees.", ["3 degrees."]),
        ("- .5 degrees.", [".5 degrees."]),
        ("- ½ cup.", ["½ cup."]),
        ("- +3 degrees.", ["+3 degrees."]),
        ("-option.", ["option."]),
        ("Value 3.*units.", ["Value 3.", "units."]),
        ("Hello.\n***\nWorld.", ["Hello.", "World."]),  # Never skip a standalone line.
        ("**\nFirst.", ["First."]),
        ("# Heading.", ["Heading."]),  # Not evidenced as stripped by Edge.
        ("'Quoted' start.", ["Quoted' start."]),
        ("– Dash.", ["Dash."]),
        ("~ Approximate.", ["Approximate."]),
    ],
)
def test_body_loss_reordering_and_unconfirmed_changes_are_rejected(expected, actual):
    with pytest.raises(ValidationError):
        prepare(expected, actual)


@pytest.mark.parametrize("expected, actual", MATRIX[:8])
def test_kokoro_and_unknown_sources_remain_exact(expected, actual):
    for timing_source in ("kokoro sentence PCM sample counts", "unknown"):
        with pytest.raises(ValidationError):
            prepare(expected, [actual], timing_source=timing_source)
        assert prepare(expected, [expected], timing_source=timing_source)[1]["text_coverage"][
            "verified"
        ]


def test_repeated_text_keeps_monotonic_occurrences_without_searching_ahead():
    content, report = prepare("**Again. **Again.", ["Again.", "Again."])
    assert content.count("Again.") == 2
    assert report["cue_count"] == 2
    with pytest.raises(ValidationError):
        prepare("**Again. **Again.", ["Again."])


@pytest.mark.parametrize(
    "expected, texts",
    [
        ("**First** part.", ["*First** part."]),
        ('"One." **Two**.', ['"One."', "Two**."]),
        ("First item\n- Second item", ["First item", "Second item"]),
        ("*#Heading.", ["#Heading."]),
    ],
)
def test_suffix_prefix_retention_and_source_boundary_evidence(expected, texts):
    assert prepare(expected, texts)[1]["text_coverage"]["verified"]


def test_diagnostics_locate_mismatch_without_logging_private_text():
    private = "PrivateUnpublishedSentence"
    with pytest.raises(ValidationError) as error:
        prepare(f"First. **{private} ending.", ["First.", "Different ending."])
    assert error.value.code == "VALIDATION_FAILED"
    assert "Sentence 2" in str(error.value)
    assert "character" in str(error.value)
    assert private not in str(error.value)
    assert "Different" not in str(error.value)


def test_exact_coverage_report_and_native_timing_stay_compatible():
    _, report = prepare("One. Again. Again.", ["One.", "Again.", "Again."])
    assert report["text_coverage"] == {
        "verified": True,
        "normalization": "ignore whitespace only; preserve public API text literally",
        "expected_characters": 16,
        "actual_characters": 16,
    }
