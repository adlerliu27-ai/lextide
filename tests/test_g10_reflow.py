"""Visual line wraps must be resolved in the reviewable draft, never after approval."""

import hashlib

import pytest

from lextide.cleaning import clean_english
from lextide.desktop import service
from lextide.longform.document import chunk_document, prepare_document


def english(text):
    return clean_english(text, mode="remove_chinese_translations")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "Let me offer\nthe perspective of one observer.",
            "Let me offer the perspective of one observer.",
        ),
        (
            "They tend\nto rely on statistical evidence.",
            "They tend to rely on statistical evidence.",
        ),
        (
            "In the morning the samples can be\nexamined again.",
            "In the morning the samples can be examined again.",
        ),
        ("We spoke with Dr.\nMaya Jones at the lab.", "We spoke with Dr. Maya Jones at the lab."),
        (
            "We sent the first report from the laboratory to\nMaya yesterday.",
            "We sent the first report from the laboratory to Maya yesterday.",
        ),
    ],
)
def test_wrapped_prose(source, expected):
    r = english(source)
    assert r.original_text == source and r.cleaned_text == expected
    assert r.changes[-1]["reason"] == "ocr_line_joined"
    assert english(r.cleaned_text).cleaned_text == expected


@pytest.mark.parametrize(
    "source",
    [
        "She said, “It works.”\nthe next example is separate.",
        "A complete sentence!\nWhy not?\nnew paragraph.",
        "They tend\n\nto rely on evidence.",
        "Reading Notes\nthe view from the lake",
        "Silver by the river\ngreen beneath the moon\nsilent on the water",
        "Let me offer  \nthe second phrase.",  # explicit Markdown hard break
        "We walked in the\n北京 district.",
    ],
)
def test_preserves_meaningful_or_uncertain_breaks(source):
    expected = source.replace("  \n", "\n\n")
    assert english(source).cleaned_text == expected
    assert english(expected).cleaned_text == expected


def test_structural_boundaries_and_literal_blocks():
    source = (
        "# Let me offer\nthe next example.\n- They tend\n- to listen\n"
        "1. They tend\n2. to listen\n"
        "```python\n    Let me offer\n    the_example = 2\n```\n\n"
        "1\n00:00:01,000 --> 00:00:04,000\nLet me offer\nthe second phrase."
    )
    expected = (
        "Let me offer\n\nthe next example.\nThey tend\n\nto listen\n"
        "They tend\n\nto listen\n"
        "```python\n    Let me offer\n    the_example = 2\n```\n\n"
        "1\n00:00:01,000 --> 00:00:04,000\nLet me offer\nthe second phrase."
    )
    assert english(source).cleaned_text == expected
    assert english(expected).cleaned_text == expected


WRAPPED = (
    "When our class began recording the birds in the garden, the nearest microphone had to be\n"
    "approached quietly so the shyest birds would keep singing. During the first week we kept\n"
    "a separate notebook for each tree and marked every change in the weather. Let me offer\n"
    "the perspective of one patient observer who listened from a bench beside a narrow wooden\n"
    "footbridge. A listener can hear changes that a small recorder may miss. The observers tend\n"
    "to notice those changes when they compare notes before the morning traffic arrives. "
    "At noon we asked\n"
    "Maya to mark the quietest minute on the chart and explain her choice. "
    "She noticed one particularly\n"
    "brief silence just before the blackbird returned to its nest. On our walk home we decided\n"
    "to repeat this careful experiment on the next clear day, with another "
    "observer and a set of batteries for the recorder. We filed the paper sheets for later review."
)


def test_9_line_draft_and_plan_consume_the_reviewed_898_characters_in_order():
    assert len(WRAPPED) == 898 and WRAPPED.count("\n") == 8
    reflowed = english(WRAPPED)
    assert reflowed.original_text == WRAPPED
    assert reflowed.cleaned_text == WRAPPED.replace("\n", " ")
    assert english(reflowed.cleaned_text).cleaned_text == reflowed.cleaned_text
    preview = service.preview({"text": reflowed.cleaned_text})
    plan = chunk_document(prepare_document(text=preview["text"]), "kokoro")
    assert "".join(s["text"] for s in plan["segments"]) == preview["text"] == reflowed.cleaned_text
    assert preview["sha256"] == hashlib.sha256(reflowed.cleaned_text.encode()).hexdigest()
    assert not any("\n" in s["text"] for s in plan["segments"])


def test_provenance_accepts_saved_v1_profile_without_recleaning():
    original = "They tend\nto rely on evidence."
    digest = hashlib.sha256(original.encode()).hexdigest()
    _, record = service._provenance(
        {
            "provenance": {
                "text": original,
                "cleaning": {
                    "mode": "english",
                    "cleaning_mode": "conservative",
                    "version": "english-rules-v1",
                    "original_sha256": digest,
                    "cleaned_text": original,
                },
            }
        },
        original,
    )
    assert record["text_preparation"]["version"] == "english-rules-v1"
    assert record["text_preparation"]["cleaned_text"] == original
