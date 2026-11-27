"""Generate synthetic study images and score the local production OCR adapter.

Run as python -m scripts.ocr_benchmark. Rendering requires the optional Pillow
environment; scoring runs in Lextide's desktop environment without Pillow.
"""

import argparse
import asyncio
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

from scripts.ocr_baseline import golden_images, metrics, render, summarize

LANGUAGES = {"english": "en", "chinese": "zh", "mixed": "mixed"}

# The three image styles are applied to each language. An empty reference line
# adds paragraph space; only nonblank lines count toward line recall.
CHALLENGES = {
    "english": [
        ["Maya Chen kept 1,025 samples in 2025.", "At 08:30, she wrote: Check the label."],
        ["Dr. Reed read the first page.", "", "The next page cost $3.50.", "He kept the receipt."],
        ["Ticket B-17 leaves at 09:45.", "Meet Ana Li by the blue sign!"],
    ],
    "chinese": [
        ["沈清在苏州记录了 35 个地点。", "请于 2026 年核对：第一站是平江路。"],
        ["许明先翻开课本。", "", "他接着检查第二段。", "最后圈出问号。"],
        ["陈晓把 12 本笔记放到书架上。", "下次见面是周三，别忘了！"],
    ],
    "mixed": [
        ["陈老师说：Maya Chen 会在 2025 年回来。", "Check page 3 at 10:30."],
        ["Nora Lin saved a copy.", "", "林老师用 Python 3.13 检查数字。", "The result was 4.75."],
        ["周宁在表中写下 Ada 和 28。", "Read the note: Room C-19."],
    ],
}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(output, primary_font, second_font):
    from PIL import ImageFont
    from PIL import __version__ as pillow_version

    output.mkdir(parents=True, exist_ok=False)
    fixtures = list(golden_images(output, primary_font, second_font))
    styles = [
        ("small-print", 18, "#242424", "#ffffff", 0),
        ("paragraphs", 36, "#232323", "#f5f0e6", 0),
        ("soft-gray", 20, "#757575", "#dcdcdc", 0.8),
    ]
    for kind, passages in CHALLENGES.items():
        for lines, (style, size, ink, bg, blur) in zip(passages, styles, strict=True):
            path = output / f"{kind}-{style}.png"
            font = ImageFont.truetype(str(primary_font), size)
            render(path, lines, font, 28, 0, ink, background=bg, blur=blur)
            fixtures.append(
                {
                    "image": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "kind": kind,
                    "split": "challenge",
                    "style": style,
                    "font": primary_font.name,
                    "font_size": size,
                    "reference": lines,
                }
            )
    write_json(
        output / "ground-truth.json",
        {
            "schema": 1,
            "pillow": pillow_version,
            "font_sha256": {
                f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                for f in (primary_font, second_font)
                if f
            },
            "fixtures": fixtures,
        },
    )


def script_kind(text):
    latin = bool(re.search(r"[A-Za-z]", text))
    han = bool(re.search(r"[\u3400-\u9fff]", text))
    if latin and han:
        return "mixed"
    if latin:
        return "english"
    return "chinese" if han else "other"


def group_summary(rows):
    base = summarize(rows)
    states = Counter(r["status"] for r in rows)
    script_matches = sum(r["kind"] == r["output_scripts"] for r in rows)
    return {
        **base,
        "missing_lines": base["lines"] - base["recalled_lines"],
        "extra_or_unmatched_output_lines": sum(
            r["metrics"]["unmatched_output_line_count"] for r in rows
        ),
        "script_matches": script_matches,
        "language_accuracy": script_matches / len(rows),
        "mean_seconds": base["seconds"] / len(rows),
        "outcomes": dict(states),
    }


async def evaluate(fixtures_dir, report_dir):
    from lextide.desktop.image_ocr import recognize
    from lextide.errors import StudioError

    manifest = json.loads((fixtures_dir / "ground-truth.json").read_text(encoding="utf-8"))
    fixtures = manifest["fixtures"]
    if not fixtures:
        raise ValueError("Expected at least one OCR fixture")
    report_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    for fixture in fixtures:
        name = fixture["image"]
        if Path(name).name != name:
            raise ValueError(f"Expected a fixture filename: {name!r}")
        path = fixtures_dir / name
        expected_hash = fixture["sha256"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"Fixture hash mismatch: {name}")
        requested = LANGUAGES[fixture["kind"]]
        start = time.perf_counter()
        try:
            result = await recognize(
                path, provider="tesseract", language=requested, allow_partial=True
            )
        except StudioError as error:
            output, status, metadata = "", error.code, {}
        else:
            if result["source_sha256"] != expected_hash:
                raise ValueError(f"Recognizer read a different fixture: {name}")
            output = result["text"]
            status = "partial_review" if result.get("review_required") else "recognized"
            metadata = result["ocr"]
        elapsed = time.perf_counter() - start
        rows.append(
            {
                **fixture,
                "status": status,
                "ocr": metadata,
                "requested": requested,
                "output": output,
                "output_scripts": script_kind(output),
                "seconds": elapsed,
                "metrics": metrics(fixture["reference"], output),
            }
        )

    groups = {
        "all": rows,
        "golden": [row for row in rows if row["split"] != "challenge"],
        "challenge": [row for row in rows if row["split"] == "challenge"],
    }
    summary = {}
    for group, selected in groups.items():
        summary[group] = {
            kind: group_summary([r for r in selected if r["kind"] == kind])
            for kind in LANGUAGES
            if any(r["kind"] == kind for r in selected)
        }
    write_json(report_dir / "observations.json", rows)
    write_json(report_dir / "summary.json", summary)
    print(json.dumps(summary["all"], indent=2))
    # Preserve measurements while making a broken local setup visible in CI.
    return int(any(r["status"] not in {"recognized", "partial_review", "OCR_EMPTY"} for r in rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("output", type=Path)
    prepare.add_argument("--font", required=True, type=Path)
    prepare.add_argument("--second-font", type=Path)
    score = commands.add_parser("run")
    score.add_argument("fixtures", type=Path)
    score.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        generate(args.output, args.font, args.second_font)
        return 0
    return asyncio.run(evaluate(args.fixtures, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
