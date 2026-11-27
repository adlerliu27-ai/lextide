"""Score synthetic OCR transcripts, including omissions, duplicates and reordered rows."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# The CLI tools live in the source checkout, outside the installed wheel.
with patch.object(sys, "path", [str(Path(__file__).resolve().parents[1]), *sys.path]):
    from scripts.ocr_baseline import metrics
    from scripts.ocr_benchmark import evaluate, group_summary, script_kind


@pytest.mark.parametrize(
    ("output", "recalled", "missing", "extra", "in_order"),
    [
        ("Alpha\nBeta\n", 2, [], 0, True),
        ("Beta\nAlpha", 2, [], 0, False),
        ("Alpha\nAlpha", 1, ["Beta"], 1, False),
        ("Alpha Beta", 0, ["Alpha", "Beta"], 1, False),
        ("Alpha\nBeta\nBeta", 2, [], 1, True),
        ("", 0, ["Alpha", "Beta"], 0, False),
    ],
)
def test_line_coverage(output, recalled, missing, extra, in_order):
    result = metrics(["Alpha", "", "Beta"], output)
    assert result["lines"] == 2
    assert result["recalled_lines"] == recalled
    assert result["unmatched_reference_lines"] == missing
    assert result["unmatched_output_line_count"] == extra
    assert result["order_correct_and_complete"] is in_order


def test_rows_are_not_reused_and_duplicate_lines_can_match_in_order():
    assert metrics(["twice", "twice"], "twice")["recalled_lines"] == 1
    assert metrics(["twice", "twice"], "twice\ntwice")["order_correct_and_complete"]
    # Alpha's closest match is the only match for the second line. Reassign it.
    result = metrics(["alpha", "alphX"], "alpha\nalpYa")
    assert result["recalled_lines"] == 2
    assert not result["order_correct_and_complete"]


def test_raw_and_normalized_cer_count_different_edits():
    result = metrics(["到达 ２０２５ 年。"], "到达2026年。\n")
    assert (result["normalized_chars"], result["normalized_errors"]) == (8, 1)
    assert result["raw_errors"] == 6


@pytest.mark.parametrize(
    ("output", "detected"),
    [
        ("Maya: 2025", "english"),
        ("王宁：2025", "chinese"),
        ("Ada 住在杭州。", "mixed"),
        ("3.14", "other"),
    ],
)
def test_output_script_class_uses_text_not_requested_language(output, detected):
    assert script_kind(output) == detected


def test_aggregate_includes_missing_text_in_every_denominator():
    def row(reference, output, status):
        return dict(
            kind="mixed",
            metrics=metrics([reference], output),
            output_scripts=script_kind(output),
            seconds=0.5,
            status=status,
        )

    good = row("Ada 和李明", "Ada 和李明", "recognized")
    partial = row("Fred 和王宁", "Fred", "partial_review")
    failed = row("Amy 在北京", "", "OCR_EMPTY")
    result = group_summary([good, partial, failed])
    assert result["normalized_CER"] == 9 / 19
    assert result["missing_lines"] == 2
    assert result["line_recall"] == result["language_accuracy"] == 1 / 3
    assert result["seconds"] == 1.5
    assert result["outcomes"] == {"recognized": 1, "partial_review": 1, "OCR_EMPTY": 1}


def test_runner_rejects_changed_input_before_engine_invocation(tmp_path):
    image = tmp_path / "sample.png"
    image.write_bytes(b"modified")
    (tmp_path / "ground-truth.json").write_text(
        json.dumps(
            {"fixtures": [{"image": image.name, "sha256": hashlib.sha256(b"old").hexdigest()}]}
        )
    )
    with pytest.raises(ValueError, match="Fixture hash mismatch"):
        asyncio.run(evaluate(tmp_path, tmp_path / "run"))


def test_empty_dataset_cannot_produce_a_successful_benchmark(tmp_path):
    (tmp_path / "ground-truth.json").write_text(json.dumps({"fixtures": []}))
    with pytest.raises(ValueError, match="at least one OCR fixture"):
        asyncio.run(evaluate(tmp_path, tmp_path / "run"))
    assert not (tmp_path / "run").exists()
