"""Minimal, deterministic cleanup; all edits retain original and new character spans."""

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .config import MAX_INPUT_BYTES, MAX_TTS_BYTES
from .errors import InputError

RULE_VERSION = "minimal-whitespace-v1"


@dataclass(frozen=True)
class PreparedText:
    original_bytes: bytes
    original_text: str
    tts_text: str
    changes: list[dict]
    input_type: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.tts_text.encode("utf-8")).hexdigest()


def prepare_text(*, text: str | None = None, input_path: Path | None = None) -> PreparedText:
    if (text is None) == (input_path is None):
        raise InputError("请且仅提供一个 --text 或 --input UTF-8 TXT。")
    if input_path is not None:
        if input_path.suffix.lower() != ".txt" or not input_path.is_file():
            raise InputError("--input 必须指向现有的 .txt 普通文件。")
        try:
            with input_path.open("rb") as source:
                raw = source.read(MAX_INPUT_BYTES + 1)
        except OSError:
            raise InputError("无法读取 TXT；请检查文件权限。") from None
    else:
        try:
            raw = text.encode("utf-8")
        except UnicodeError:
            raise InputError("输入含无效 Unicode 字符。") from None
    if len(raw) > MAX_INPUT_BYTES:
        raise InputError("G1 原始输入上限为 64 KiB；请选取短文本。")
    try:
        original = raw.decode("utf-8")
    except UnicodeError:
        raise InputError("TXT 必须为 UTF-8（可含 BOM）；请先在本地转换编码。") from None
    cleaned = original.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    # No translation removal, punctuation expansion, phonetic rewrite, or sentence guessing.
    cleaned = cleaned.replace("\t", " ").strip()
    for char in cleaned:
        if unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char != "\n":
            raise InputError("输入含不支持的控制/隐藏字符；请检查原始 TXT。")
    if not cleaned or not re.search(r"[A-Za-z]", cleaned):
        raise InputError("请输入至少含一个英文字母的非空短文本。")
    if len(html.escape(cleaned, quote=False).encode("utf-8")) > MAX_TTS_BYTES:
        raise InputError("G1 转义后 UTF-8 文本上限为 3000 字节；长文请分次手动选择。")
    changes = []
    for op, i, j, a, b in SequenceMatcher(None, original, cleaned, autojunk=False).get_opcodes():
        if op != "equal":
            changes.append(
                {
                    "rule_id": RULE_VERSION,
                    "original_span": [i, j],
                    "new_span": [a, b],
                    "before": original[i:j],
                    "after": cleaned[a:b],
                    "reason": "移除开头 BOM、统一换行、Tab 转空格或去除两端空白",
                }
            )
    return PreparedText(raw, original, cleaned, changes, "txt" if input_path else "text")
