"""Bounded, deterministic document cleanup and lossless English text slices.

Spans are half-open Python/Unicode character offsets in ``PreparedText.tts_text``.
The nonoverlapping edits in ``PreparedText.changes`` map these back to the
untouched original. Whitespace belongs to a slice; it is never reconstructed.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from bisect import bisect_left, bisect_right
from pathlib import Path

from ..errors import InputError
from ..text import PreparedText

TEXT_RULE_VERSION = "minimal-whitespace-linear-v1"
RULE_VERSION = "english-document-chunks-v1"
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_CHUNKS = 2000
_CLEANUP = re.compile(r"\r\n?|\t")
_PARAGRAPH_END = re.compile(r"\n(?:[^\S\n]*\n)+")
_SENTENCE_END = re.compile(r"""[.!?]+[\"'”’\)\]]*""")
_INITIALISM = re.compile(r"(?:[A-Za-z]\.)+[A-Za-z]")
_ABBREVIATIONS = frozenset(
    "mr mrs ms dr prof sr jr st vs etc e.g i.e a.m p.m fig no inc ltd dept approx al".split()
)
_DEFAULT_BYTES = {"edge-tts": 2800, "kokoro": 1200}


def prepare_document(*, text: str | None = None, input_path: Path | None = None) -> PreparedText:
    """Apply G1's cleanup with a linear edit trace and a 1 MiB input bound.

    This intentionally does not call G1's short-text byte limit or quadratic
    SequenceMatcher trace. Cleanup semantics remain BOM/newlines/tabs/outer trim.
    """
    if (text is None) == (input_path is None):
        raise InputError("请且仅提供一个 --text 或 --input UTF-8 TXT。")
    if input_path is not None:
        if not isinstance(input_path, Path):
            raise InputError("--input 必须指向现有的 .txt 普通文件。")
        try:
            if input_path.suffix.lower() != ".txt" or not input_path.is_file():
                raise InputError("--input 必须指向现有的 .txt 普通文件。")
            with input_path.open("rb") as source:
                raw = source.read(MAX_DOCUMENT_BYTES + 1)
        except OSError:
            raise InputError("无法读取 TXT；请检查文件权限。") from None
    else:
        if not isinstance(text, str):
            raise InputError("--text 必须为文本。")
        try:
            raw = text.encode("utf-8")
        except UnicodeError:
            raise InputError("输入含无效 Unicode 字符。") from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise InputError("G3 原始输入上限为 1 MiB。")
    try:
        original = raw.decode("utf-8")
    except UnicodeError:
        raise InputError("TXT 必须为 UTF-8（可含 BOM）；请先在本地转换编码。") from None

    # All substituted characters are whitespace, so trim bounds can be found
    # on the original without allocating a per-character provenance table.
    left = int(original.startswith("\ufeff"))
    right = len(original)
    while left < right and original[left].isspace():
        left += 1
    while right > left and original[right - 1].isspace():
        right -= 1
    changes = []

    def edit(start: int, end: int, new_start: int, replacement: str) -> None:
        changes.append(
            {
                "rule_id": TEXT_RULE_VERSION,
                "original_span": [start, end],
                "new_span": [new_start, new_start + len(replacement)],
                "before": original[start:end],
                "after": replacement,
                "reason": "移除开头 BOM、统一换行、Tab 转空格或去除两端空白",
            }
        )

    if left:
        edit(0, left, 0, "")
    parts = []
    position = left
    new_position = 0
    for match in _CLEANUP.finditer(original, left, right):
        unchanged = original[position : match.start()]
        parts.append(unchanged)
        new_position += len(unchanged)
        replacement = " " if match.group() == "\t" else "\n"
        edit(match.start(), match.end(), new_position, replacement)
        parts.append(replacement)
        new_position += 1
        position = match.end()
    parts.append(original[position:right])
    new_position += right - position
    if right < len(original):
        edit(right, len(original), new_position, "")
    cleaned = "".join(parts)
    for char in cleaned:
        if unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char != "\n":
            raise InputError("输入含不支持的控制/隐藏字符；请检查原始 TXT。")
    if not cleaned or not re.search(r"[A-Za-z]", cleaned):
        raise InputError("请输入至少含一个英文字母的非空文本。")
    return PreparedText(raw, original, cleaned, changes, "txt" if input_path else "text")


def _segments(text: str) -> tuple[list[dict], list[int]]:
    """Protect common titles, initials, initialisms and decimal punctuation.

    This is a conservative English heuristic, not NLP: an abbreviation at a
    sentence end may deliberately stay joined to the following sentence.
    Paragraph breaks always win, and oversized sentences may still be split.
    """
    paragraph_ends = [match.end() for match in _PARAGRAPH_END.finditer(text)]
    if not paragraph_ends or paragraph_ends[-1] != len(text):
        paragraph_ends.append(len(text))
    result = []
    paragraph_start = 0
    for paragraph_index, paragraph_end in enumerate(paragraph_ends):
        start = paragraph_start
        ends = []
        for match in _SENTENCE_END.finditer(text, paragraph_start, paragraph_end):
            # Check the successor after consuming the whole punctuation run.
            # A regex lookahead here retries every suffix of a huge run when
            # punctuation is followed by a letter, making that input quadratic.
            if match.end() < paragraph_end and not text[match.end()].isspace():
                continue
            punctuation = match.group().rstrip("\"'”’)]")
            if punctuation == ".":
                token_start = match.start()
                while token_start > paragraph_start:
                    char = text[token_start - 1]
                    if not (char.isascii() and (char.isalpha() or char == ".")):
                        break
                    token_start -= 1
                word = text[token_start : match.start()]
                if word and (
                    word.lower() in _ABBREVIATIONS
                    or (len(word) == 1 and word.isalpha())
                    or _INITIALISM.fullmatch(word)
                ):
                    continue
            end = match.end()
            while end < paragraph_end and text[end].isspace():
                end += 1
            ends.append(end)
        if not ends or ends[-1] != paragraph_end:
            ends.append(paragraph_end)
        for end in ends:
            index = len(result)
            result.append(
                {
                    "segment_id": f"seg-{index:06d}",
                    "index": index,
                    "start": start,
                    "end": end,
                    "text": text[start:end],
                    "paragraph_index": paragraph_index,
                }
            )
            start = end
        paragraph_start = paragraph_end
    return result, paragraph_ends


def chunk_document(prepared: PreparedText, engine_id: str, max_bytes: int | None = None) -> dict:
    """Build an exact partition with deterministic engine-specific byte budgets.

    Edge budgets HTML-escaped UTF-8; Kokoro budgets plain UTF-8. Both have a
    3000-byte engineering ceiling; the Kokoro default is deliberately conservative
    and is not a measured performance optimum. Prefer paragraph, then sentence,
    then whitespace boundaries. Long tokens fall back to Unicode code points
    (not grapheme clusters); no UTF-8 sequence is ever cut.

    A chunk must contain a Unicode letter or number. Numeric-only chunks are
    allowed by both accepted adapters. Punctuation/whitespace-only fragments
    stay attached; inputs for which the budget cannot do this fail locally.
    """
    if not isinstance(engine_id, str) or engine_id not in _DEFAULT_BYTES:
        raise InputError("engine 必须是 edge-tts 或 kokoro。")
    if max_bytes is None:
        max_bytes = _DEFAULT_BYTES[engine_id]
    if type(max_bytes) is not int or not 1 <= max_bytes <= 3000:
        raise InputError("chunk-bytes 必须为 1 至 3000 的整数。")
    if not isinstance(prepared, PreparedText) or not isinstance(prepared.tts_text, str):
        raise InputError("文档必须先通过 prepare_document 清理。")
    text = prepared.tts_text
    if not text or not re.search(r"[A-Za-z]", text):
        raise InputError("请输入至少含一个英文字母的非空文本。")
    costs = [0]
    meaningful = []
    try:
        for index, char in enumerate(text):
            if unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char != "\n":
                raise InputError("输入含不支持的控制/隐藏字符；请先清理文档。")
            cost = {"&": 5, "<": 4, ">": 4}.get(char) if engine_id == "edge-tts" else None
            costs.append(costs[-1] + (cost if cost is not None else len(char.encode("utf-8"))))
            if char.isalnum():
                meaningful.append(index)
    except UnicodeError:
        raise InputError("输入含无效 Unicode 字符。") from None
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise InputError("G3 文本上限为 1 MiB。")
    segments, paragraph_ends = _segments(text)
    sentence_ends = [segment["end"] for segment in segments]
    spaces = list(re.finditer(r"\s+", text))
    space_starts = [match.start() for match in spaces]
    chunks = []
    start = 0
    segment_index = 0

    def viable(end: int) -> bool:
        next_index = bisect_left(meaningful, end)
        if next_index == 0 or meaningful[next_index - 1] < start:
            return False
        if end == len(text):
            return True
        return (
            next_index < len(meaningful)
            and costs[meaningful[next_index] + 1] - costs[end] <= max_bytes
        )

    while start < len(text):
        if len(chunks) >= MAX_CHUNKS:
            raise InputError("G3 文档最多 2000 个 chunk；请增大 chunk-bytes 或缩短文本。")
        hard_end = bisect_right(costs, costs[start] + max_bytes) - 1
        # If only punctuation/space remains beyond the budget, leave the last
        # pronounceable character for the final slice instead of making a silent
        # trailing request. A genuinely impossible tail fails via viable().
        if hard_end < len(text) and hard_end > meaningful[-1]:
            hard_end = meaningful[-1]
        candidates = []
        for boundaries in (paragraph_ends, sentence_ends):
            index = bisect_right(boundaries, hard_end) - 1
            if index >= 0:
                candidates.append(boundaries[index])
        space_index = bisect_left(space_starts, hard_end) - 1
        if space_index >= 0:
            candidates.append(min(spaces[space_index].end(), hard_end))
        candidates.append(hard_end)
        end = next((end for end in candidates if end > start and viable(end)), None)
        if end is None:
            raise InputError(
                "无法在 chunk-bytes 上限内保留有效文本片段；请增大上限或缩短连续空白/标点。"
            )
        pieces = []
        while segment_index < len(segments) and segments[segment_index]["start"] < end:
            segment = segments[segment_index]
            piece_start = max(start, segment["start"])
            piece_end = min(end, segment["end"])
            pieces.append(
                {
                    "segment_id": segment["segment_id"],
                    "start": piece_start,
                    "end": piece_end,
                    "chunk_start": piece_start - start,
                    "chunk_end": piece_end - start,
                }
            )
            if segment["end"] > end:
                break
            segment_index += 1
        chunk_text = text[start:end]
        index = len(chunks)
        chunks.append(
            {
                "chunk_id": f"chunk-{index:06d}",
                "index": index,
                "start": start,
                "end": end,
                "text": chunk_text,
                "text_sha256": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                "segment_ids": [piece["segment_id"] for piece in pieces],
                "pieces": pieces,
            }
        )
        start = end
    return {
        "rule_version": RULE_VERSION,
        "engine_id": engine_id,
        "max_bytes": max_bytes,
        "segments": segments,
        "chunks": chunks,
    }
