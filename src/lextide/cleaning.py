"""Conservative, local English cleanup with source-line audit records.

This is a deterministic text filter, not a language detector or translator.
Mixed-language text is retained when the rules cannot identify a bounded
annotation. Audit records contain operation codes and original 1-based line
numbers, never source excerpts. ``original_text`` is always the exact input.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .errors import InputError

RULE_VERSION = "english-rules-v2"
TRANSLATION_RULE_VERSION = "english-translations-v2"
CLEANING_MODES = ("conservative", "remove_chinese_translations")
MAX_INPUT_BYTES = 1024 * 1024
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f]")
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_PREFIX = re.compile(
    r"(?:#{1,6} +|> +|[-+*•] +|\d{1,3}[.)] +)"
    r"(?=[A-Za-z\u3400-\u9fff\"'“‘`*_#>])"
)
_EMPHASIS = re.compile(
    r"(?<![\w\\*_])(?P<mark>\*\*\*|___|\*\*|__|\*|_)"
    r"(?P<body>[^\n*_]+?)(?P=mark)(?![\w*_])"
)
_INLINE_CODE = re.compile(r"(?<!`)(?P<ticks>`{1,3})(?P<body>[^`]+)(?P=ticks)(?!`)")
_PARENTHESES = re.compile(r"\(([^()（）]*)\)|（([^()（）]*)）")
_PAGE = re.compile(r"Page +([0-9]+) +of +([0-9]+)", re.IGNORECASE)
_ABBREVIATIONS = frozenset(
    "mr mrs ms dr prof sr jr st vs etc e.g i.e a.m p.m fig no inc ltd dept approx al".split()
)
_CONTINUATION_WORDS = frozenset(
    "a an the and or of to in for with from by as that which whose because while when if".split()
)


@dataclass(frozen=True)
class CleanResult:
    original_text: str
    cleaned_text: str
    version: str = RULE_VERSION
    removed_chinese_lines: int = 0
    changes: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    mode: str = "conservative"
    removed_chinese_segments: int = 0


@dataclass
class _Line:
    text: str
    number: int
    end_number: int
    structured: bool = False
    continuations: list[str] = field(default_factory=list)
    literal: bool = False

    @property
    def tail(self) -> str:
        return self.continuations[-1] if self.continuations else self.text


def _record(records: list[dict], line: int, reason: str, operation: str | None = None) -> None:
    record = {"line": line, "reason": reason}
    if operation is not None:
        record["operation"] = operation
    records.append(record)


def _only_han_letters(text: str) -> bool:
    """Digits/punctuation are allowed, but Latin, kana, etc. prevent deletion."""
    return bool(_HAN.search(text)) and all(
        not char.isalpha() or _HAN.fullmatch(char) for char in text
    )


def _normalize(text: str, line: int, changes: list[dict], warnings: list[dict]) -> str:
    if line == 1 and text.startswith("\ufeff"):
        text = text[1:]
        _record(changes, line, "bom_removed", "remove_span")
    result = []
    control = hidden = replacement = False
    for char in text:
        category = unicodedata.category(char)
        if char.isspace():
            result.append(" ")
        elif category == "Cc":
            # A separator avoids inventing a word by joining around a bad control.
            result.append(" ")
            control = True
        else:
            result.append(char)
            hidden |= category == "Cf"
            replacement |= char == "\ufffd"
    normalized = re.sub(r" +", " ", "".join(result)).strip(" ")
    if normalized != text:
        _record(changes, line, "whitespace_normalized", "normalize")
    if control:
        _record(changes, line, "control_character_replaced", "replace_span")
        _record(warnings, line, "control_character_replaced")
    if hidden:
        _record(warnings, line, "hidden_character_retained")
    if replacement:
        _record(warnings, line, "replacement_character_retained")
    return normalized


def _markdown(
    text: str, line: int, changes: list[dict], *, preserve_chinese_code: bool = False
) -> tuple[str, bool]:
    structured = False
    start = 0
    while prefix := _PREFIX.match(text, start):
        start = prefix.end()
        structured = True
        _record(changes, line, "markdown_prefix_removed", "remove_span")
    text = text[start:]

    def emphasis(match: re.Match) -> str:
        body = match["body"]
        if body != body.strip() or not any(char.isalnum() for char in body):
            return match[0]
        _record(changes, line, "markdown_emphasis_removed", "remove_span")
        return body

    # Code contents are protected from emphasis rules: `a * b * c` stays literal.
    output = []
    position = 0
    for match in _INLINE_CODE.finditer(text):
        output.append(_EMPHASIS.sub(emphasis, text[position : match.start()]))
        literal = preserve_chinese_code and _HAN.search(match["body"])
        output.append(match[0] if literal else match["body"])
        position = match.end()
        if not literal:
            _record(changes, line, "markdown_code_delimiters_removed", "remove_span")
    output.append(_EMPHASIS.sub(emphasis, text[position:]))
    return "".join(output), structured


def _annotations(text: str, line: int, changes: list[dict]) -> str:
    """Remove pure-Han glosses or a gloss repeating its immediately preceding word.

    A mixed gloss with additional Latin words is deliberately left for review.
    Nested parentheses do not qualify. Parentheses containing English alone
    (including acronyms and citations) are never removed.
    """
    output = []
    position = 0
    scan_position = 0
    round_depth = fullwidth_depth = 0
    english_seen = False
    for match in _PARENTHESES.finditer(text):
        between = text[scan_position : match.start()]
        scan_position = match.end()
        round_depth += between.count("(") - between.count(")")
        fullwidth_depth += between.count("（") - between.count("）")
        english_seen |= bool(re.search(r"[A-Za-z]", between))
        prefix_has_english = english_seen
        english_seen |= bool(re.search(r"[A-Za-z]", match[0]))
        body = (match[1] if match[1] is not None else match[2]).strip()
        if not _HAN.search(body) or not prefix_has_english:
            continue
        # Do not peel an inner annotation out of a nested parenthesis group.
        if round_depth or fullwidth_depth:
            continue
        pure_gloss = _only_han_letters(body)
        previous_word = re.search(r"([A-Za-z][A-Za-z0-9_+#.-]*)\s*$", between)
        latin_words = _LATIN_WORD.findall(body)
        repeated_gloss = (
            previous_word is not None
            and len(latin_words) == 1
            and latin_words[0] == previous_word[1]
            and body.startswith(latin_words[0])
            and all(not char.isalpha() or char.isascii() or _HAN.fullmatch(char) for char in body)
        )
        if not (pure_gloss or repeated_gloss):
            continue
        before = text[position : match.start()]
        after_char = text[match.end() : match.end() + 1]
        if after_char and after_char in ",.;:!?)]}”’":
            before = before.rstrip(" ")
        output.append(before)
        if (
            match.start()
            and after_char
            and text[match.start() - 1].isalnum()
            and after_char.isalnum()
        ):
            output.append(" ")
        position = match.end()
        _record(changes, line, "chinese_annotation_removed", "remove_span")
    output.append(text[position:])
    return re.sub(r" +", " ", "".join(output)).strip(" ")


def _translation_suffix(text: str) -> str | None:
    first_han = _HAN.search(text)
    if first_han is None or first_han.start() == 0:
        return None
    before = text[: first_han.start()]
    prefix = before.rstrip()
    suffix = text[first_han.start() :]
    # Whitespace separates a complete English sentence from a Chinese-first
    # sentence. A terminal Chinese mark avoids guessing at fragments/headings.
    if before == prefix or not re.search(r"[.!?][\"'”’]*$", prefix):
        return None
    if not re.search(r"[。！？][\"'”’]*$", suffix):
        return None
    english_words = _LATIN_WORD.findall(prefix)
    if len(english_words) < 2 or not suffix[0].isalpha():
        return None
    last_token = prefix.rstrip("\"'”’").rsplit(" ", 1)[-1]
    if last_token.lower().rstrip(".") in _ABBREVIATIONS:
        return None
    if re.fullmatch(r"(?:[A-Za-z]\.){2,}", last_token):
        return None
    if any(char.isalpha() and not char.isascii() for char in prefix):
        return None
    # Chinese translations often repeat product names (e.g. Python). An English
    # sentence or an unseen Latin word after Chinese text is not discarded.
    known_words = set(english_words)
    suffix_words = _LATIN_WORD.findall(suffix)
    if len(suffix_words) > 1 or any(word not in known_words for word in suffix_words):
        return None
    if re.search(r"[A-Za-z]+\s+[A-Za-z]+", suffix):
        return None
    if any(not char.isascii() and char.isalpha() and not _HAN.fullmatch(char) for char in suffix):
        return None
    return prefix


_TECHNICAL_TERMS = frozenset(
    "Python C++ C# AI API APIs SHA-256 GitHub HTML CSS JSON XML SQL URL HTTP HTTPS "
    "PDF TXT DOCX MP3 SRT FFmpeg ONNX CPU GPU UTF-8".split()
)
_ENGLISH_FUNCTION_WORDS = frozenset(
    "a an the i you he she it we they this that these those is are was were be been "
    "do does did will would should can could may must not and or but of to in on for "
    "with from by as at my your our their keep use don't".split()
)
_LATIN_PHRASE = re.compile(r"[A-Za-z][A-Za-z0-9+#.-]*(?: +[A-Za-z][A-Za-z0-9+#.-]*)*")
# Either OCR colon form can introduce a bounded Chinese speaker label.
_SPEAKER_LABEL = re.compile(
    r" *[A-Z][a-z]+(?: +[A-Z][a-z]+){0,2} *(?:博士|教授|老师|医生|先生|女士) *[:：]"
)
_CHINESE_PREDICATE = re.compile(r"[我你他她]们?(?:是|叫|使用)|采访了|介绍了|记录了|发布了|采用了")
_QUOTED_LATIN = re.compile(r"(?<![A-Za-z0-9])[\"'“‘`] *[A-Za-z]")
_LATIN_TERMINAL = re.compile(r" *[!?]")


def _translation_parts(text: str) -> list[tuple[str, bool]]:
    """Split only top-level sentence ends or explicit parallel-text separators.

    Keep quoted/bracketed Chinese terms inside the English sentence they belong
    to. Decimal dots, initials and known abbreviations are not sentence ends.
    The bounded lookbehind avoids repeatedly rescanning a long paragraph.
    """
    parts = []
    start = 0
    closers = []
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】", "{": "}", "“": "”", "‘": "’"}
    for index, char in enumerate(text):
        previous = text[index - 1 : index]
        following = text[index + 1 : index + 2]
        if char in "'’" and previous.isalnum() and following.isalnum():
            continue  # Apostrophe, not the end of a quoted span.
        if closers and char == closers[-1]:
            closers.pop()
            continue
        if char in pairs:
            closers.append(pairs[char])
            continue
        if char in {'"', "'", "`"} and (not closers or char != closers[-1]):
            closers.append(char)
            continue
        if closers:
            continue
        if char == "：" and _SPEAKER_LABEL.fullmatch(text, start, index + 1):
            continue
        if char in "|：":
            parts.append((text[start:index], False))
            parts.append((char, True))
            start = index + 1
        elif char in ".!?。！？":
            if char == ".":
                repeated_year_boundary = bool(
                    re.search(r"[A-Za-z]+ +[0-9]{4}$", text[max(start, index - 40) : index])
                    and re.match(r"[0-9]{4}年", text[index + 1 : index + 7])
                )
                if previous.isdigit() and following.isdigit() and not repeated_year_boundary:
                    continue
                token = re.search(r"[A-Za-z.]+$", text[max(start, index - 32) : index + 1])
                if token and (
                    token[0].lower().rstrip(".") in _ABBREVIATIONS
                    or re.fullmatch(r"(?:[A-Za-z]\.)+", token[0])
                ):
                    continue
                if following and following.isascii() and following.isalpha():
                    continue  # e.g. dotted host names, not a script transition.
            parts.append((text[start : index + 1], False))
            start = index + 1
    parts.append((text[start:], False))
    return parts


def _chinese_translation_candidate(text: str) -> bool:
    """Require Chinese grammar plus only bounded names/technical Latin tokens.

    Han / alphabetic-character ratio excludes digits, punctuation and spaces.
    Ratio alone never deletes text: every Latin run must independently qualify.
    Other scripts and ordinary English predicates always require manual review.
    """
    han = sum(bool(_HAN.fullmatch(char)) for char in text)
    if not han:
        return False
    letters = [char for char in text if char.isalpha()]
    if any(not char.isascii() and not _HAN.fullmatch(char) for char in letters):
        return False
    phrases = list(_LATIN_PHRASE.finditer(text))
    if not phrases:
        return True
    if _QUOTED_LATIN.search(text):
        return False  # A literal English quote/title is not evidence of a person's name.
    # A Chinese speaker label or Chinese subject, predicate and terminal mark
    # can provide stronger evidence than a letter ratio when a name is long.
    stripped = text.strip()
    chinese_clause = (
        stripped.rstrip("”’").endswith(("。", "！", "？"))
        and bool(_HAN.match(stripped) or _SPEAKER_LABEL.match(stripped))
        and bool(_CHINESE_PREDICATE.search(stripped))
    )
    for phrase in phrases:
        words = phrase[0].rstrip(".").split()
        technical = all(word in _TECHNICAL_TERMS for word in words)
        proper_name = (2 <= len(words) <= 3 or (len(words) == 1 and chinese_clause)) and all(
            re.fullmatch(r"[A-Z][a-z]+", word) and word.lower() not in _ENGLISH_FUNCTION_WORDS
            for word in words
        )
        if not (technical or proper_name):
            return False
        # A Latin phrase with its own English terminal punctuation could be an
        # independent statement, even if it contains only capitalized words.
        if phrase[0].endswith(".") or _LATIN_TERMINAL.match(text, phrase.end()):
            return False
    ratio = han / len(letters)
    # Short Chinese translations can have a long Latin name. The bounded-name
    # checks above are mandatory; the 35% letter ratio is only supporting evidence.
    return han >= 4 and (ratio >= 0.35 or chinese_clause)


def _remove_translations(
    text: str,
    line: int,
    changes: list[dict],
    warnings: list[dict],
    speaker: _Line | None = None,
) -> tuple[str, int, bool]:
    if not _HAN.search(text):
        return text, 0, False
    parts = _translation_parts(text)
    # Only the immediately following first clause belongs to a split label.
    # Keep the same grammar/name checks as the single-line representation.
    removed = [
        not separator
        and (
            _chinese_translation_candidate(part)
            or (
                speaker is not None
                and index == 0
                and _chinese_translation_candidate(speaker.text + part)
            )
        )
        for index, (part, separator) in enumerate(parts)
    ]
    count = sum(removed)
    if not count:
        return text, 0, False
    speaker_removed = speaker is not None and removed[0]
    if speaker_removed:
        count += 1
        changes.append(
            {
                "line": speaker.number,
                "reason": "chinese_translation_segment_removed",
                "operation": "remove_span",
                "removed_characters": len(speaker.text),
            }
        )
        # The label was retained on the previous iteration; its body now proves
        # it is a translation. Inspect only the adjacent lines' warnings.
        for index in range(len(warnings) - 1, -1, -1):
            warning = warnings[index]
            if warning["line"] < speaker.number:
                break
            if warning["line"] == speaker.number and warning["reason"] == "mixed_chinese_retained":
                warnings.pop(index)
        _record(warnings, speaker.number, "translation_with_latin_removed")
    # A separator joins two columns; only remove it when at least one adjacent
    # content part is removed. Preserve punctuation between surviving English.
    for index, (_, separator) in enumerate(parts):
        if separator and (
            (index and removed[index - 1]) or (index + 1 < len(parts) and removed[index + 1])
        ):
            removed[index] = True
    output = []
    gap = False
    for (part, separator), deleted in zip(parts, removed, strict=True):
        if deleted:
            if not separator:
                changes.append(
                    {
                        "line": line,
                        "reason": "chinese_translation_segment_removed",
                        "operation": "remove_span",
                        "removed_characters": len(part.strip()),
                    }
                )
                if _LATIN_WORD.search(part):
                    _record(warnings, line, "translation_with_latin_removed")
            gap = True
        else:
            if gap and output and part.strip() and not part.startswith(" "):
                output.append(" ")
            output.append(part)
            if part.strip():
                gap = False
    return re.sub(r" +", " ", "".join(output)).strip(), count, speaker_removed


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CUE_TIME = re.compile(r"^\d{2,}:\d{2}:\d{2}[,.]\d{3}\s*-->")
_HONORIFIC = re.compile(r"\b(?:Dr|Mr|Mrs|Ms|Prof)\.$", re.I)
_SUBJECT = re.compile(r"^(?:I|You|We|They|He|She|It)\s", re.I)


def _joinable(previous: _Line, current: _Line) -> bool:
    """Accept a wrap only with both a plausible continuation and evidence of prose."""
    if previous.structured or current.structured or previous.end_number + 1 != current.number:
        return False
    left, right = previous.tail, current.text
    if not left or not right or not re.match(r"[A-Za-z]", right):
        return False
    if any(char.isalpha() and not char.isascii() for char in left + right):
        return False
    if _HONORIFIC.search(left):
        return bool(re.match(r"[A-Z][a-z]", right))
    if not re.search(r"[A-Za-z0-9,]$", left):
        return False  # Includes sentence punctuation, closing quotes and hyphenated breaks.
    words = _LATIN_WORD.findall(left)
    if not words:
        return False
    continuation = words[-1].lower() in _CONTINUATION_WORDS
    long_prose = len(left) >= 48 and len(words) >= 7 and not left.istitle()
    # A capital can be a proper name inside a sentence. Require more evidence
    # than capitalization, e.g. a preceding preposition or a name followed by "to".
    if right[0].isupper():
        return bool(
            re.match(r"[A-Z][a-z]+\b", right)
            and (
                (continuation and len(words) >= 3)
                or (
                    (long_prose or _SUBJECT.match(left))
                    and re.match(r"[A-Z][a-z]+(?: [A-Z][a-z]+)? to\b", right)
                )
            )
        )
    if long_prose or (continuation and (previous.continuations or len(words) >= 3)):
        return True
    # Short grammatical fragments can wrap too, but two arbitrary lowercase
    # lines (a title, notes, verse) are not in themselves evidence of a wrap.
    return bool(
        (len(words) >= 2 and _SUBJECT.match(left) and right.startswith("to "))
        or (
            re.match(r"Let (?:me|us) [a-z]", left, re.I)
            and re.match(r"(?:a|an|the|some|you)\b", right)
        )
        or (
            len(words) >= 3
            and words[-1].lower() == "be"
            and words[-2].lower() in {"to", "can", "could", "may", "might", "will", "would", "must"}
        )
    )


def clean_english(text: str, *, mode: str = "conservative") -> CleanResult:
    """Clean at most 1 MiB of UTF-8 without network, NLP models, or file writes.

    Empty/all-Chinese input returns an empty result with ``no_english_text``;
    callers must let the user review that outcome before creating speech.
    Conservative mode retains standalone Chinese lines containing Latin words.
    The explicit translation mode additionally examines bounded sentence/column
    fragments, permitting only restricted names and technical terms within a
    Chinese translation. All ambiguous passages are kept for manual review.
    ``removed_chinese_lines`` counts whole lines, not inline glosses.
    """
    if mode not in CLEANING_MODES:
        raise InputError("Unknown English cleaning mode.")
    if not isinstance(text, str):
        raise InputError("英文清理需要文本输入。")
    if len(text) > MAX_INPUT_BYTES:
        raise InputError("英文清理输入上限为 1 MiB；请拆分文本后重试。")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise InputError("输入含无效 Unicode 字符。") from None
    if size > MAX_INPUT_BYTES:
        raise InputError("英文清理输入上限为 1 MiB；请拆分文本后重试。")

    changes: list[dict] = []
    warnings: list[dict] = []
    lines: list[_Line] = []
    removed_chinese_lines = 0
    removed_chinese_segments = 0
    parts = re.split(r"(\r\n|\r|\n)", text)
    fence = ""
    in_cue = False
    for index in range(0, len(parts), 2):
        number = index // 2 + 1
        raw = parts[index]
        if index + 1 < len(parts) and parts[index + 1] != "\n":
            _record(changes, number, "line_ending_normalized", "normalize")
        value = _normalize(raw, number, changes, warnings)
        marker = _FENCE.fullmatch(raw)
        if fence or marker:
            if marker and not fence:
                fence = marker[1]
            elif marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
            # Literal fenced lines retain indentation, punctuation and their own
            # boundaries. Keep diagnostic normalization for control characters.
            literal = (
                raw
                if not any(unicodedata.category(c) == "Cc" and c != "\t" for c in raw)
                else value
            )
            if marker:
                _record(warnings, number, "unresolved_markdown_retained")
                if not any(c.isalnum() for c in value):
                    _record(warnings, number, "unrecognized_noise_retained")
            lines.append(_Line(literal, number, number, structured=True, literal=True))
            continue
        if not value:
            in_cue = False
        elif _CUE_TIME.match(value):
            in_cue = True
        if (raw.startswith(("    ", "\t")) or in_cue) and value:
            lines.append(_Line(raw, number, number, structured=True, literal=True))
            continue
        protected_code = "`" in value and any(
            _HAN.search(m["body"]) for m in _INLINE_CODE.finditer(value)
        )
        value, structured = _markdown(
            value, number, changes, preserve_chinese_code=mode == "remove_chinese_translations"
        )
        structured |= raw.endswith(("  ", "\\"))
        if mode == "conservative" and _only_han_letters(value):
            removed_chinese_lines += 1
            _record(changes, number, "chinese_line_removed", "remove_line")
            continue
        value = _annotations(value, number, changes)
        if mode == "remove_chinese_translations" and not protected_code:
            speaker = None
            if (
                lines
                and lines[-1].end_number + 1 == number
                and not lines[-1].structured
                and not structured
                and _SPEAKER_LABEL.fullmatch(lines[-1].text)
            ):
                speaker = lines[-1]
            value, removed, speaker_removed = _remove_translations(
                value, number, changes, warnings, speaker
            )
            removed_chinese_segments += removed
            if speaker_removed:
                lines.pop()
                removed_chinese_lines += 1
            if removed and not value:
                removed_chinese_lines += 1
                continue
        elif mode == "conservative" and (suffix_removed := _translation_suffix(value)):
            value = suffix_removed
            _record(changes, number, "chinese_translation_suffix_removed", "remove_span")
        if _HAN.search(value):
            _record(warnings, number, "mixed_chinese_retained")
        if page := _PAGE.fullmatch(value):
            current_page, total_pages = page[1].lstrip("0"), page[2].lstrip("0")
            if current_page and (len(current_page), current_page) <= (
                len(total_pages),
                total_pages,
            ):
                _record(changes, number, "page_marker_removed", "remove_line")
                continue
            _record(warnings, number, "ambiguous_page_marker_retained")
        if value.isdecimal():
            _record(warnings, number, "numeric_line_retained")
        if "`" in value or value.startswith("~~~"):
            _record(warnings, number, "unresolved_markdown_retained")
            structured = True
        if value and not any(char.isalnum() for char in value):
            _record(warnings, number, "unrecognized_noise_retained")
        current = _Line(value, number, number, structured)
        if not value and (not lines or not lines[-1].text):
            _record(changes, number, "blank_line_removed", "remove_line")
            continue
        if lines and _joinable(lines[-1], current):
            previous = lines[-1]
            previous.continuations.append(value)
            _record(changes, number, "ocr_line_joined", "join_lines")
            changes[-1]["previous_line"] = previous.end_number
            previous.end_number = number
            continue
        if lines and lines[-1].end_number + 1 == number and value:
            if re.search(r"[A-Za-z][-‐‑]$", lines[-1].tail) and re.match(r"[a-z]", value):
                _record(warnings, number, "hyphenated_line_break_retained")
        lines.append(current)
    if lines and not lines[-1].text:
        _record(changes, lines[-1].number, "blank_line_removed", "remove_line")
        lines.pop()
    rendered: list[str] = []
    previous: _Line | None = None
    for line in lines:
        value = " ".join([line.text, *line.continuations])
        if previous is not None and not (previous.literal or line.literal):
            # Removed Markdown markers / translations must not lose their
            # boundary on a second cleanup pass. Preserve only boundaries that
            # the resulting plain prose would otherwise qualify to join.
            plain_previous = _Line(rendered[-1], 1, 1)
            if _joinable(plain_previous, _Line(value, 2, 2)):
                rendered.append("")
        rendered.append(value)
        previous = line
    cleaned = "\n".join(rendered)
    if not re.search(r"[A-Za-z]", cleaned):
        _record(warnings, 1, "no_english_text")
    return CleanResult(
        original_text=text,
        cleaned_text=cleaned,
        version=TRANSLATION_RULE_VERSION if mode == "remove_chinese_translations" else RULE_VERSION,
        removed_chinese_lines=removed_chinese_lines,
        changes=changes,
        warnings=warnings,
        mode=mode,
        removed_chinese_segments=removed_chinese_segments,
    )
