"""Generate original typeset fixtures and benchmark local OCR. Requires optional Pillow."""

import argparse
import hashlib
import json
import re
import subprocess
import time
import unicodedata
from pathlib import Path

# The first four pairs are the development split. Freeze the command before the holdout.
LESSONS = [
    (
        "学习英语需要时间。",
        "今天阅读三篇文章。",
        "Learning English takes time.",
        "Read three articles today.",
    ),
    (
        "请打开第二页。",
        "在纸上写下名字。",
        "Please open the second page.",
        "Write your name on paper.",
    ),
    (
        "北京的秋天很美。",
        "我们一起走到公园。",
        "Beijing is lovely in autumn.",
        "We walked to the park.",
    ),
    (
        "早上八点开始上课。",
        "实验结果已经保存。",
        "Class starts at eight.",
        "The results are ready.",
    ),
    (
        "李明今天去图书馆。",
        "他借了一本科学书。",
        "Maya visited the library.",
        "She borrowed a science book.",
    ),
    (
        "王老师喜欢听音乐。",
        "我们每天练习发音。",
        "Our teacher enjoys music.",
        "We practice every day.",
    ),
    (
        "一共找到了二十个样本。",
        "把数字写在第二行。",
        "We found 1,500 samples.",
        "Write 3.13 on line two.",
    ),
    (
        "陈华住在上海。",
        "城市有一条大河。",
        "Chen lives in Shanghai.",
        "A river runs through the city.",
    ),
    ("这是一张图片。", "请仔细检查原文。", "This is a picture.", "Check the original text."),
    (
        "小鸟从树上飞下来。",
        "午后的阳光很暖和。",
        "A bird flew down.",
        "The afternoon sun was warm.",
    ),
    (
        "新的计划已经完成。",
        "明天继续整理笔记。",
        "The new plan is complete.",
        "We will finish the notes tomorrow.",
    ),
    (
        "请保存文件再退出。",
        "数字和名称都很重要。",
        "Save the file before you leave.",
        "Python and Lextide handle text.",
    ),
    ("雨停了，我们出发。", "请带上地图和水。", "The rain has stopped.", "Bring water and a map."),
    ("她把书放在桌上。", "窗外传来读书声。", "She put the book down.", "Two friends read aloud."),
    (
        "周末去杭州旅行。",
        "我们买了三张车票。",
        "Hangzhou is our next stop.",
        "We bought 3 tickets.",
    ),
    (
        "声音来自左边。",
        "清晨的空气很新鲜。",
        "Sound came from the left.",
        "The morning air was fresh.",
    ),
    (
        "写下问题和答案。",
        "最后检查姓名和日期。",
        "Record the date and name.",
        "Ada answered in 12 seconds.",
    ),
    (
        "图书馆有很多书。",
        "认真阅读每一行。",
        "The library has many books.",
        "Read each line carefully.",
    ),
]


def distance(a, b):
    previous = list(range(len(b) + 1))
    for i, c in enumerate(a, 1):
        current = [i]
        for j, d in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[-1] + 1, previous[j - 1] + (c != d)))
        previous = current
    return previous[-1]


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def metrics(reference, output):
    joined = "\n".join(reference)
    truth, actual = normalized(joined), normalized(output)
    # Match each nonblank source line to at most one nonblank output line.
    # Prefer closer text; allow reassignment so a fuzzy match cannot consume
    # the only possible output line for a later reference.
    actual_lines = [normalized(line) for line in output.splitlines() if line.strip()]
    content_lines = [line for line in reference if line.strip()]
    expected_lines = [normalized(line) for line in content_lines]
    candidates = []
    for target in expected_lines:
        scores = sorted(
            (distance(target, line) / max(len(target), len(line)), i)
            for i, line in enumerate(actual_lines)
        )
        candidates.append([i for score, i in scores if score <= 0.2])
    assigned = {}

    def match(source, visited):
        for dest in candidates[source]:
            if dest in visited:
                continue
            visited.add(dest)
            if dest not in assigned or match(assigned[dest], visited):
                assigned[dest] = source
                return True
        return False

    for source in range(len(expected_lines)):
        match(source, set())

    # An ordered assignment may differ from the maximum matching for duplicate
    # lines. The earliest valid successor leaves the most room for later lines.
    last = -1
    ordered = True
    for options in candidates:
        successor = min((idx for idx in options if idx > last), default=None)
        if successor is None:
            ordered = False
            break
        last = successor
    return {
        "normalized_errors": distance(truth, actual),
        "normalized_chars": len(truth),
        "raw_errors": distance(joined, output.rstrip("\n")),
        "raw_chars": len(joined),
        "lines": len(expected_lines),
        "recalled_lines": len(assigned),
        "unmatched_reference_lines": [
            line for i, line in enumerate(content_lines) if i not in assigned.values()
        ],
        "unmatched_output_line_count": len(actual_lines) - len(assigned),
        "order_correct_and_complete": ordered,
    }


def render(path, lines, font, margin, indent, ink, *, background="white", blur=0):
    from PIL import Image, ImageDraw

    boxes = [font.getbbox(s) for s in lines]
    height = max(b - t for _, t, _, b in boxes)
    gap = max(10, height // 2)
    width = max(right - left for left, _, right, _ in boxes) + 2 * margin + indent
    image = Image.new(
        "RGB", (width, 2 * margin + len(lines) * height + (len(lines) - 1) * gap), background
    )
    draw = ImageDraw.Draw(image)
    for i, (line, (left, top, _, _)) in enumerate(zip(lines, boxes, strict=True)):
        draw.text(
            (margin - left + (indent if i else 0), margin + i * (height + gap) - top),
            line,
            font=font,
            fill=ink,
        )
    if blur:
        from PIL import ImageFilter

        image = image.filter(ImageFilter.GaussianBlur(blur))
    image.save(path)


def summarize(rows):
    keys = (
        "normalized_errors",
        "normalized_chars",
        "raw_errors",
        "raw_chars",
        "lines",
        "recalled_lines",
    )
    totals = {k: sum(r["metrics"][k] for r in rows) for k in keys}
    return {
        **totals,
        "images": len(rows),
        "normalized_CER": totals["normalized_errors"] / totals["normalized_chars"],
        "raw_CER": totals["raw_errors"] / totals["raw_chars"],
        "line_recall": totals["recalled_lines"] / totals["lines"],
        "complete_in_order_images": sum(r["metrics"]["order_correct_and_complete"] for r in rows),
        "seconds": sum(r["seconds"] for r in rows),
    }


def golden_images(output, primary_font, second_font=None):
    from PIL import ImageFont

    for n, (zh1, zh2, en1, en2) in enumerate(LESSONS):
        font_path = second_font if n % 2 and second_font else primary_font
        font = ImageFont.truetype(str(font_path), [32, 40, 48][n % 3])
        for kind, lines in (
            ("chinese", [zh1, zh2]),
            ("english", [en1, en2]),
            ("mixed", [en1, zh1, en2]),
        ):
            ext = ["png", "jpeg", "jpg"][n % 3]
            path = output / f"{kind}-{n:02d}.{ext}"
            render(
                path,
                lines,
                font,
                [22, 32, 44][n % 3],
                [0, 12, 24][n % 3],
                ["#101010", "#202020"][n % 2],
            )
            yield {
                "image": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "font": font_path.name,
                "font_size": font.size,
                "reference": lines,
                "kind": kind,
                "split": "development" if n < 4 else "validation" if n < 12 else "holdout",
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--second-font", type=Path)
    parser.add_argument("--tesseract", default="tesseract")
    parser.add_argument(
        "--default-row-rejection",
        action="store_true",
        help="Reproduce the comparison without the production row-retention option.",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for fixture in golden_images(args.output, args.font, args.second_font):
        path = args.output / fixture["image"]
        cmd = [
            args.tesseract,
            str(path),
            "stdout",
            "-l",
            "chi_sim+eng",
            "--psm",
            "6",
            "--oem",
            "1",
        ]
        if not args.default_row_rejection:
            cmd.extend(["-c", "textord_noise_rejrows=0"])
        start = time.monotonic()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True)
        rows.append(
            {
                **fixture,
                "command": cmd,
                "stdout": p.stdout,
                "stderr": p.stderr,
                "exit_code": p.returncode,
                "seconds": round(time.monotonic() - start, 4),
                "metrics": metrics(fixture["reference"], p.stdout),
            }
        )
    report = {
        s: {
            k: summarize([r for r in rows if r["kind"] == k and r["split"] == s])
            for k in ("chinese", "english", "mixed")
        }
        for s in ("development", "validation", "holdout")
    }
    (args.output / "fixtures-results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    )
    (args.output / "metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
