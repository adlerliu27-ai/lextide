"""Sentence subtitles from native metadata; no inferred or rescaled timings."""

import re

from .engines.base import SentenceBoundary
from .errors import ValidationError

TICKS_PER_MS = 10_000
MAX_AUDIO_DURATION_MS = 600_000
MAX_ADJUSTMENT_MS = 100
MAX_EDGE_GAP_MS = 2_000
_TIMESTAMP = r"([0-9]{2}):([0-5][0-9]):([0-5][0-9]),([0-9]{3})"
_TIME_RANGE = re.compile(rf"{_TIMESTAMP} --> {_TIMESTAMP}")
# Only leading deletions observed in the G5 Edge matrix, not all punctuation.
_EDGE_LEADING_MARKERS = frozenset('*-•>"`—')
_SENTENCE_CLOSERS = frozenset("\"'”’)]}*`")
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’"}


def _duration(value: int, *, max_audio_duration_ms: int = MAX_AUDIO_DURATION_MS) -> None:
    if type(max_audio_duration_ms) is not int or max_audio_duration_ms <= 0:
        raise ValidationError("音频时长上限必须为正整数。")
    if type(value) is not int or not 0 < value <= max_audio_duration_ms:
        raise ValidationError(f"音频时长必须是 1–{max_audio_duration_ms}ms 之间的整数。")


def _text(value: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValidationError("字幕文本必须是不含空字符的字符串。")
    # The public edge-tts stream already XML-unescapes metadata. Preserve literal entities.
    return " ".join(value.split())


def _coverage(value: str) -> str:
    # Punctuation and letter case are retained: missing sentence endings matter.
    return "".join(_text(value).split())


def _edge_coverage(expected_text: str, texts: list[str]) -> tuple[list[dict], list[dict]]:
    """Consume reviewed text once, in order; never search ahead for a matching cue.

    Whitespace retains the existing comparison semantics. A marker may only be
    omitted at a cue start which is also a document, line or punctuated sentence
    start in the source. The additional quote rule requires a paired source quote
    at a real cue boundary; comma/semicolon contexts require source whitespace.
    Metadata, SRT text and reviewed input are never rewritten.
    """
    positions = [i for i, char in enumerate(expected_text) if not char.isspace()]
    expected = "".join(expected_text[i] for i in positions)
    cursor = 0
    omissions = []
    quote_omissions = []

    def fail(index: int, offset: int, reason: str) -> None:
        position = positions[offset] + 1 if offset < len(positions) else len(expected_text) + 1
        raise ValidationError(
            f"Sentence {index}: Edge metadata does not match the reviewed text at character "
            f"{position} ({reason}). Check for omitted or changed text or sentence order. "
            "If editing is needed, edit the preview and review it again before retrying."
        )

    def source_start(offset: int, *, quotation: bool = False) -> bool:
        if offset == 0:
            return True
        gap = expected_text[positions[offset - 1] + 1 : positions[offset]]
        if "\n" in gap or "\r" in gap:
            return True
        if quotation and gap and expected[offset - 1] in ",;":
            return True
        previous = offset - 1
        while previous >= 0 and expected[previous] in _SENTENCE_CLOSERS:
            previous -= 1
        return bool(gap) and previous >= 0 and expected[previous] in ".!?。！？"

    # Locate punctuation pairs in the source, never matching or searching for cue
    # text. Ambiguous/unbalanced quotes and inline quoted words gain no tolerance.
    # Apostrophes inside words are not quote delimiters. G5's marker set is intact.
    paired_openings = set()
    quote_suffixes = {}
    active = None
    for offset, char in enumerate(expected):
        if char in "'’" and 0 < offset < len(expected) - 1:
            if expected[offset - 1].isalnum() and expected[offset + 1].isalnum():
                continue
        if active is not None:
            opening, prefix_start, closing, wrapper = active
            if char != closing:
                continue
            active = None
            suffix_end = offset + 1 + len(wrapper)
            raw_end = positions[offset] + 1 + len(wrapper)
            if (
                offset > opening + 1
                and expected[offset - 1] in ".!?。！？"
                and positions[offset] == positions[offset - 1] + 1
                and expected[offset + 1 : suffix_end] == wrapper
                and expected_text[positions[offset] + 1 : raw_end] == wrapper
                and (raw_end == len(expected_text) or expected_text[raw_end].isspace())
            ):
                paired_openings.add(prefix_start)
                # Also permit retained closing quotes with omitted matching stars.
                for suffix_start in range(offset, suffix_end):
                    quote_suffixes[suffix_start] = (opening, suffix_end)
            continue
        if char not in _QUOTE_PAIRS:
            continue
        prefix_start = offset
        while prefix_start and expected[prefix_start - 1] in "*>":
            prefix_start -= 1
        prefix = expected[prefix_start:offset]
        if (
            prefix in {"", "*", "**", ">"}
            and offset + 1 < len(expected)
            and expected[offset + 1].isalnum()
            and positions[offset] - positions[prefix_start] == offset - prefix_start
            # Reject quote-only runs before scanning their preceding closers.
            and source_start(prefix_start, quotation=True)
        ):
            active = (offset, prefix_start, _QUOTE_PAIRS[char], prefix if "*" in prefix else "")

    for index, text in enumerate(texts, 1):
        actual = _coverage(text)
        start = cursor
        if not expected.startswith(actual, start):
            prefix_end = start
            while prefix_end < len(expected) and expected[prefix_end] in _EDGE_LEADING_MARKERS:
                prefix_end += 1
            actual_prefix = 0
            while actual_prefix < len(actual) and actual[actual_prefix] in _EDGE_LEADING_MARKERS:
                actual_prefix += 1
            removed = prefix_end - start - actual_prefix
            # Dash deletion must be list-style, not a sign or a hyphenated word.
            dash_safe = all(
                expected[i] not in "-—"
                or (
                    positions[i] + 1 < len(expected_text)
                    and expected_text[positions[i] + 1].isspace()
                    and prefix_end < len(expected)
                    and expected[prefix_end].isalpha()
                )
                for i in range(start, prefix_end)
            )
            if (
                removed > 0
                and prefix_end < len(expected)
                and actual_prefix < len(actual)
                and (
                    source_start(start)
                    or (start in paired_openings and source_start(start, quotation=True))
                )
                and dash_safe
                and not any(
                    char in "\r\n"
                    for char in expected_text[positions[start] : positions[prefix_end]]
                )
                and expected[start + removed : prefix_end] == actual[:actual_prefix]
            ):
                start += removed
                if expected.startswith(actual, start):
                    omissions.append(
                        {
                            "cue_index": index,
                            "reviewed_character": positions[cursor] + 1,
                            "omitted_marker_count": removed,
                        }
                    )
                    if not source_start(cursor):
                        quote_omissions.append(
                            {
                                "cue_index": index,
                                "reviewed_character": positions[cursor] + 1,
                                "omitted_marker_count": removed,
                                "kind": "quoted_cue_prefix",
                            }
                        )
            if not expected.startswith(actual, start):
                common = 0
                while (
                    start + common < len(expected)
                    and common < len(actual)
                    and expected[start + common] == actual[common]
                ):
                    common += 1
                reason = "body_or_order_mismatch"
                if start == cursor and prefix_end > cursor:
                    reason = "unpermitted_leading_prefix_or_body_mismatch"
                fail(index, start + common, reason)
        cursor = start + len(actual)
        if cursor in quote_suffixes:
            opening, suffix_end = quote_suffixes[cursor]
            # Prefer exact next-cue consumption when the API retains the closer.
            following = _coverage(texts[index]) if index < len(texts) else ""
            if opening < cursor and (not following or not expected.startswith(following, cursor)):
                quote_omissions.append(
                    {
                        "cue_index": index,
                        "reviewed_character": positions[cursor] + 1,
                        "omitted_marker_count": suffix_end - cursor,
                        "kind": "paired_quote_suffix",
                    }
                )
                cursor = suffix_end
    if cursor != len(expected):
        fail(len(texts) + 1, cursor, "unconsumed_reviewed_text")
    return omissions, quote_omissions


def _format_time(value: int) -> str:
    seconds, milliseconds = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _parse_time(parts: tuple[str, ...]) -> int:
    hours, minutes, seconds, milliseconds = map(int, parts)
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _validate_cues(
    cues: list[tuple[int, int, str]],
    audio_duration_ms: int,
    *,
    max_audio_duration_ms: int = MAX_AUDIO_DURATION_MS,
) -> dict:
    if not cues:
        raise ValidationError("字幕为空，缺少句子时间信息。")
    previous_start, previous_end = -1, -1
    for index, (start, end, text) in enumerate(cues, 1):
        if start < 0 or end <= start:
            raise ValidationError(f"第 {index} 条字幕的时间必须非负且持续时间大于零。")
        if start <= previous_start:
            raise ValidationError(f"第 {index} 条字幕存在逆序或重复时间戳。")
        if start < previous_end:
            raise ValidationError(f"第 {index} 条字幕与上一条字幕重叠。")
        if end > audio_duration_ms:
            raise ValidationError(f"第 {index} 条字幕超出音频时长。")
        if not text.strip():
            raise ValidationError(f"第 {index} 条字幕文本为空。")
        previous_start, previous_end = start, end
    if cues[0][0] > MAX_EDGE_GAP_MS:
        raise ValidationError("首句字幕距离音频起点超过 2000ms，无法确认时间对齐。")
    trailing_gap = audio_duration_ms - cues[-1][1]
    if trailing_gap > MAX_EDGE_GAP_MS:
        raise ValidationError("末句字幕距离音频终点超过 2000ms，可能存在缺失或时间错位。")
    return {
        "valid": True,
        "cue_count": len(cues),
        "first_start_ms": cues[0][0],
        "last_end_ms": cues[-1][1],
        "audio_duration_ms": audio_duration_ms,
        "trailing_gap_ms": trailing_gap,
        "limits": {
            "max_audio_duration_ms": max_audio_duration_ms,
            "max_edge_gap_ms": MAX_EDGE_GAP_MS,
            "max_native_adjustment_ms": MAX_ADJUSTMENT_MS,
        },
    }


def prepare_subtitles(
    boundaries: list[SentenceBoundary],
    *,
    audio_duration_ms: int,
    expected_text: str,
    timing_source: str = "edge-tts SentenceBoundary",
    max_audio_duration_ms: int = MAX_AUDIO_DURATION_MS,
) -> tuple[str, dict]:
    """Return SRT and a timing/coverage report, or fail closed on bad metadata.

    Native starts are floored and ends are ceiled to milliseconds. The previous
    end may be capped at the next start for at most 100ms of overlap. The last
    end may similarly be capped at audio duration by at most 100ms. Both changes
    are reported; timestamps are never scaled. A 2s leading/trailing gap is the
    conservative limit for this short-text synthesis workflow.
    """
    _duration(audio_duration_ms, max_audio_duration_ms=max_audio_duration_ms)
    if not isinstance(boundaries, list) or not boundaries:
        raise ValidationError("缺少原生 SentenceBoundary 句子时间信息。")
    expected = _coverage(expected_text)
    if not expected:
        raise ValidationError("用于核对字幕覆盖的原文为空。")

    cues: list[tuple[int, int, str]] = []
    previous_offset = -1
    for index, boundary in enumerate(boundaries, 1):
        if not isinstance(boundary, SentenceBoundary):
            raise ValidationError(f"第 {index} 条句子时间信息类型无效。")
        offset, duration = boundary.offset_ticks, boundary.duration_ticks
        if type(offset) is not int or type(duration) is not int or offset < 0 or duration <= 0:
            raise ValidationError(f"第 {index} 条句子时间信息必须为非负起点和正整数时长。")
        if offset <= previous_offset:
            raise ValidationError(f"第 {index} 条句子时间信息存在逆序或重复时间戳。")
        text = _text(boundary.text)
        if not text:
            raise ValidationError(f"第 {index} 条句子文本为空。")
        cues.append(
            (offset // TICKS_PER_MS, (offset + duration + TICKS_PER_MS - 1) // TICKS_PER_MS, text)
        )
        previous_offset = offset

    # Compare public metadata without interpreting literal entity strings as markup.
    actual = "".join("".join(text.split()) for _, _, text in cues)
    coverage = {
        "verified": True,
        "normalization": "ignore whitespace only; preserve public API text literally",
        "expected_characters": len(expected),
        "actual_characters": len(actual),
    }
    if actual != expected:
        if timing_source != "edge-tts SentenceBoundary":
            raise ValidationError("句子元数据未完整覆盖原文，或文字、标点、顺序与原文不一致。")
        omissions, quote_omissions = _edge_coverage(expected_text, [text for _, _, text in cues])
        coverage.update(normalization="edge-leading-markers-v1", leading_marker_omissions=omissions)
        if quote_omissions:
            coverage.update(
                normalization="edge-quote-boundaries-v1", quote_boundary_omissions=quote_omissions
            )

    adjustments = []
    for index in range(1, len(cues)):
        start, end, text = cues[index - 1]
        next_start = cues[index][0]
        if next_start <= start:
            raise ValidationError(f"第 {index + 1} 条字幕取整后存在重复时间戳。")
        overlap = end - next_start
        if overlap > MAX_ADJUSTMENT_MS:
            raise ValidationError(f"第 {index + 1} 条字幕重叠超过 100ms，拒绝自动修正。")
        if overlap > 0:
            cues[index - 1] = (start, next_start, text)
            adjustments.append(
                {
                    "cue_index": index,
                    "kind": "cap_overlap",
                    "original_end_ms": end,
                    "adjusted_end_ms": next_start,
                    "delta_ms": -overlap,
                }
            )

    start, end, text = cues[-1]
    overshoot = end - audio_duration_ms
    if overshoot > MAX_ADJUSTMENT_MS:
        raise ValidationError("末句字幕超出音频时长超过 100ms，拒绝自动修正。")
    if overshoot > 0:
        cues[-1] = (start, audio_duration_ms, text)
        adjustments.append(
            {
                "cue_index": len(cues),
                "kind": "cap_audio_end",
                "original_end_ms": end,
                "adjusted_end_ms": audio_duration_ms,
                "delta_ms": -overshoot,
            }
        )

    report = _validate_cues(cues, audio_duration_ms, max_audio_duration_ms=max_audio_duration_ms)
    report.update(
        {
            "timing_source": timing_source,
            "adjustments": adjustments,
            "rounding": "floor start / ceil end to milliseconds",
            "text_coverage": coverage,
        }
    )
    content = (
        "\n\n".join(
            f"{index}\n{_format_time(start)} --> {_format_time(end)}\n{text}"
            for index, (start, end, text) in enumerate(cues, 1)
        )
        + "\n"
    )
    return content, report


def validate_srt(
    content: str,
    *,
    audio_duration_ms: int,
    max_audio_duration_ms: int = MAX_AUDIO_DURATION_MS,
) -> dict:
    """Strictly validate existing SRT timing; never repair or infer provenance.

    This API has no source text or native metadata, so it cannot certify text
    coverage, sentence segmentation, audio intelligibility, or word alignment.
    """
    _duration(audio_duration_ms, max_audio_duration_ms=max_audio_duration_ms)
    if not isinstance(content, str) or not content.strip() or "\x00" in content:
        raise ValidationError("SRT 为空或包含无效文本。")
    normalized = content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n", normalized.strip())
    cues = []
    for index, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(index):
            raise ValidationError(f"第 {index} 条 SRT 字幕格式无效或序号不连续。")
        match = _TIME_RANGE.fullmatch(lines[1])
        if not match:
            raise ValidationError(f"第 {index} 条 SRT 时间格式无效。")
        start, end = _parse_time(match.groups()[:4]), _parse_time(match.groups()[4:])
        cues.append((start, end, "\n".join(lines[2:])))
    report = _validate_cues(cues, audio_duration_ms, max_audio_duration_ms=max_audio_duration_ms)
    report.update(
        {
            "timing_source": "SRT timestamps (native provenance not verified)",
            "adjustments": [],
            "text_coverage": {"verified": False, "reason": "source text not supplied"},
        }
    )
    return report
