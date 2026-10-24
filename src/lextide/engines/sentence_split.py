"""Conservative English sentence slices; punctuation and literal entities are untouched."""

import re

# Titles and common abbreviations are kept with their following text. Ambiguous
# abbreviation-at-sentence-end cases intentionally remain a single audio segment.
_ABBREVIATIONS = frozenset(
    "mr mrs ms dr prof sr jr st vs etc e.g i.e a.m p.m fig no inc ltd dept approx".split()
)
_END = re.compile(r"""[.!?]+[\"'”’\)\]]*(?=\s|$)""")
_TOKEN = re.compile(r"([A-Za-z](?:[A-Za-z.]*)?)\.$")


def split_sentences(text: str) -> list[str]:
    """Return contiguous nonempty original slices, including intervening whitespace.

    This is deterministic English segmentation, not linguistic alignment. It
    protects decimals, initialisms and common titles; it never HTML-unescapes.
    Keeping whitespace in the slices gives exact ``''.join(result) == text``.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    result = []
    start = 0
    for match in _END.finditer(text):
        punctuation = match.group().rstrip("\"'”’)]")
        if punctuation == ".":
            token = _TOKEN.search(text[: match.start() + 1])
            if token:
                word = token.group(1)
                if word.lower() in _ABBREVIATIONS or len(word) == 1:
                    continue
                if re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]", word):
                    continue
        end = match.end()
        while end < len(text) and text[end].isspace():
            end += 1
        segment = text[start:end]
        if segment.strip():
            result.append(segment)
            start = end
    if start < len(text):
        remainder = text[start:]
        if remainder.strip():
            result.append(remainder)
        elif result:
            result[-1] += remainder
    return result
