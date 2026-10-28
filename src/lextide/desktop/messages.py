"""Translate backend diagnostics at the GUI boundary, never inside artifacts."""

from __future__ import annotations

import re

from ..reading import CHINESE_TEXT_MESSAGE, EMPTY_TEXT_MESSAGE
from .i18n import tr

# Backend messages are intentionally unchanged; these aliases only affect display.
_NOTES = {
    "已移除文件开头的 Unicode BOM。": "Removed the leading Unicode BOM.",
    "已按 BOM 识别编码并移除文件开头的 BOM。": "Detected encoding from the BOM and removed it.",
    "Markdown 已转为正文：标题、强调、引用和列表保留文字，段落以换行分隔。": (
        "Converted Markdown to text, keeping heading, emphasis, quote and "
        "list text in paragraph order."
    ),
    "链接只保留可见文字，省略目标地址；未读取链接内容。": (
        "Kept link labels and omitted destination URLs; no links were fetched."
    ),
    "图片只保留替代文字（alt text），未读取图片。": "Kept image alt text; images were not loaded.",
    "行内代码保留文字并移除反引号。": "Kept inline code text and removed backticks.",
    "Markdown 表格按行提取，单元格以中文分号分隔。": (
        "Extracted Markdown tables row by row, separating cells with semicolons."
    ),
    "已省略原始 HTML 块/标签以及 script、style 等内嵌内容；未执行脚本。": (
        "Omitted raw HTML blocks, tags, scripts and styles. No scripts were executed."
    ),
    "DOCX 按正文顺序提取段落和表格；表格每行换行，单元格用中文分号分隔。": (
        "Extracted DOCX paragraphs and tables in body order; table cells "
        "are separated with semicolons."
    ),
    "DOCX 仅导入正文；页眉、页脚、脚注、批注、绘图和删除修订未导入，请核对预览。": (
        "Only the DOCX body was imported. Headers, footers, notes, "
        "comments, drawings and deleted revisions were omitted; check the "
        "preview."
    ),
    "文件含宏数据，已忽略；未执行宏。": "Ignored macro data. No macros were executed.",
    "DOCX 含外部/嵌入内容引用（altChunk），已忽略且未读取外部资源。": (
        "Ignored DOCX external/embedded references (altChunk); no external resources were read."
    ),
    "PDF 按页序提取文本层，页间以空行分隔。": (
        "Extracted PDF text in page order with blank lines between pages."
    ),
    "PDF 的多栏、阅读顺序、连字和页眉页脚可能与视觉布局不同，请核对并编辑预览。": (
        "PDF columns, reading order, ligatures and headers/footers may "
        "differ from the page layout; check and edit the preview."
    ),
    "该 PDF 没有可提取的文本层，当前版本暂不支持 OCR。": (
        "This PDF has no extractable text layer. OCR is not supported in this version."
    ),
    "文件没有可导入的正文文本；请检查内容及提取规则。": (
        "No body text could be imported. Check the source content and extraction rules."
    ),
    "无法读取文件；请检查文件是否存在以及读取权限。": (
        "Cannot read the file. Check that it exists and is readable."
    ),
    ("仅支持 TXT、Markdown (.md)、DOCX 和文本层 PDF。"): (
        "Supported formats are TXT, Markdown (.md), DOCX and text-layer PDF."
    ),
    "文本包含 NUL 字符；可能是无 BOM 的 UTF-16，请明确选择正确编码。": (
        "Text contains NUL characters. Select the correct encoding, "
        "especially for UTF-16 without a BOM."
    ),
    (
        "无法按所选编码解码文件；自动识别只接受 UTF-8 或带 BOM 的 UTF-16。"
        "请明确选择正确编码或在本地另存为 "
        "UTF-8，不会丢弃无法识别的字符。"
    ): (
        "Cannot decode this file. Auto detection accepts UTF-8 or UTF-16 "
        "with a BOM. Select the correct encoding or save as UTF-8; "
        "unreadable characters are never discarded."
    ),
    "请输入至少含一个英文字母的非空短文本。": (
        "Enter non-empty speech text containing English letters."
    ),
    "请输入至少含一个英文字母的非空文本。": (
        "Enter non-empty speech text containing English letters."
    ),
    "服务未提供所选 voice；请核对英文 voice 名称后重试。": (
        "The service does not offer this voice. Check the English voice name and retry."
    ),
    "本地 Kokoro 模型未提供所选英文 voice。": (
        "The local Kokoro model does not offer this English voice."
    ),
}
_CODES = {
    "INVALID_INPUT": (
        "Invalid input. Check the text, voice, rate and local paths, then preview again."
    ),
    "DOCUMENT_IMPORT_FAILED": (
        "Could not import this document safely. Check its format, "
        "encoding, size and text layer; encrypted or damaged files must be "
        "repaired locally."
    ),
    "TEXT_REVIEW_REQUIRED": "Review the current text again before starting or resuming.",
    "TEXT_NOT_CONFIRMED": "Review the current text again before starting or resuming.",
    "ONLINE_NOT_AUTHORIZED": (
        "No online request was sent. Explicit permission is required for this run."
    ),
    "ONLINE_TIMEOUT": "The online request timed out. Check your connection and resume the task.",
    "ONLINE_HTTP_ERROR": "The online service rejected the request. Retry later.",
    "ONLINE_CONNECTION_FAILED": (
        "Network, DNS or TLS connection failed. Check your connection and retry."
    ),
    "ONLINE_NO_AUDIO": "The service returned no audio. Check the text and voice or retry later.",
    "ONLINE_RESPONSE_ERROR": "The online service returned an unexpected response. Retry later.",
    "ONLINE_METADATA_INVALID": (
        "The service returned incompatible metadata. Keep the error code when reporting this issue."
    ),
    "VALIDATION_FAILED": (
        "Audio, subtitles or saved file integrity could not be verified. "
        "No new successful output is accepted. Check the source task or "
        "regenerate the affected output."
    ),
    "OFFLINE_RUNTIME_MISSING": (
        "Kokoro runtime is unavailable. Select Python 3.13 with the "
        "offline dependencies installed in Settings."
    ),
    "MODEL_NOT_READY": (
        "The model is missing or damaged. Check the model directory and verify its files."
    ),
    "MODEL_CORRUPT": (
        "The model is missing or damaged. Check the model directory and verify its files."
    ),
    "MODEL_HASH_MISMATCH": (
        "Model SHA-256 does not match. Verify or reinstall the model before using it."
    ),
    "MODEL_LOAD_FAILED": "Could not load Kokoro. Check runtime and model compatibility.",
    "OFFLINE_SYNTHESIS_FAILED": (
        "Kokoro synthesis failed. Check the offline runtime and model; no online fallback is used."
    ),
    "OFFLINE_TIMEOUT": "Kokoro synthesis timed out. Completed chunks are kept for recovery.",
    "AUDIO_WRITE_FAILED": "Could not write audio. Check disk space and folder permissions.",
    "OUTPUT_IO_FAILED": "Could not write output. Check disk space and folder permissions.",
    "STORAGE_IO": (
        "Could not safely access task or cache storage. Check paths, disk space and permissions."
    ),
    "LOCAL_IO_FAILED": "Could not access local files. Check their existence and permissions.",
    "TASK_BUSY": "This task or cache entry is in use. Wait for the other process to finish.",
    "LONGFORM_IO_FAILED": (
        "Could not process the local audio files. Check disk space and permissions."
    ),
    "LONGFORM_ENCODE_TIMEOUT": "MP3 encoding timed out. Saved chunks can be reused when resuming.",
    "INTERRUPTED": "The operation was interrupted. Saved tasks can be resumed.",
    "FFMPEG_MISSING": "FFmpeg or ffprobe is unavailable. Choose the executables in Settings.",
    "DESKTOP_WORKER_ERROR": "The local operation failed. Check the input and installed runtime.",
}

# The core uses these more specific codes in runtime/model paths.
_CODES.update(
    {
        "OFFLINE_RUNTIME_UNAVAILABLE": _CODES["OFFLINE_RUNTIME_MISSING"],
        "MEDIA_TOOL_UNAVAILABLE": _CODES["FFMPEG_MISSING"],
        "MODEL_INVALID": _CODES["MODEL_NOT_READY"],
        "MODEL_BUSY": _CODES["TASK_BUSY"],
        "MODEL_IO": _CODES["LOCAL_IO_FAILED"],
        "OFFLINE_IO_FAILED": _CODES["AUDIO_WRITE_FAILED"],
        "OUTPUT_CONFLICT": _CODES["STORAGE_IO"],
        "CONFIRMATION_REQUIRED": _CODES["TEXT_REVIEW_REQUIRED"],
    }
)


def localize_note(message: str) -> str:
    source = _NOTES.get(message)
    if source:
        return tr(source)
    match = re.fullmatch(r"已省略 (\d+) 个代码块；请在预览中确认需要朗读的内容。", message)
    if match:
        return tr("Omitted {count} code blocks. Check the speech preview.", count=match[1])
    match = re.fullmatch(
        r"其中 (\d+) 页没有可提取文本；可能为空白页或扫描页，未执行 OCR。", message
    )
    if match:
        return tr(
            (
                "{count} pages had no extractable text. They may be blank or "
                "scanned; no OCR was performed."
            ),
            count=match[1],
        )
    return tr(message)


def localize_error(message: str) -> str:
    code, separator, detail = message.partition(": ")
    if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]+", code):
        return message  # Controller-owned messages already passed through tr().
    if code == "INVALID_INPUT" and detail in (CHINESE_TEXT_MESSAGE, EMPTY_TEXT_MESSAGE):
        return code + ": " + tr(detail)
    # Preserve G5's numeric location and reason without echoing private speech text.
    match = re.match(
        (
            "Sentence (\\d+): Edge metadata does not match the reviewed text at "
            "character (\\d+) \\(([a-z_]+)\\)\\."
        ),
        detail,
    )
    if code == "VALIDATION_FAILED" and match:
        sentence, character, reason = match.groups()
        reason_text = {
            "body_or_order_mismatch": "Missing, changed or reordered body text",
            ("unpermitted_leading_prefix_or_body_mismatch"): (
                "Unsupported leading marker or changed body text"
            ),
            "unconsumed_reviewed_text": "Reviewed text was not fully covered",
            "empty_sentence": "Empty sentence metadata",
        }.get(reason, "Sentence metadata mismatch")
        return (
            code
            + ": "
            + tr(
                (
                    "Sentence {sentence}, character {character}: {reason}. Check the "
                    "draft and review it again before retrying."
                ),
                sentence=sentence,
                character=character,
                reason=tr(reason_text),
            )
        )
    if detail in _NOTES:
        return code + ": " + localize_note(detail)
    if code == "ONLINE_HTTP_ERROR":
        match = re.search(r"HTTP (\d{3})", detail)
        if match:
            return (
                code
                + ": "
                + tr("The online service returned HTTP {status}. Retry later.", status=match[1])
            )
    return (
        code
        + ": "
        + tr(_CODES.get(code, "The local operation failed. Check the input and installed runtime."))
    )


def cleaning_reason(code: str) -> str:
    labels = {
        "control_character_replaced": (
            "A control character was replaced with a space; check word integrity."
        ),
        "hidden_character_retained": "A hidden character was kept; check it manually.",
        "replacement_character_retained": (
            "An unreadable replacement character was kept; check the source."
        ),
        "mixed_chinese_retained": "Uncertain mixed-language text was kept for manual review.",
        "translation_with_latin_removed": (
            "Chinese translation containing names or technical terms was removed; "
            "review the result."
        ),
        "ambiguous_page_marker_retained": "An uncertain page marker was kept.",
        "numeric_line_retained": "A numeric line was kept; check whether it belongs to the body.",
        "unresolved_markdown_retained": "Uncertain formatting was kept for manual review.",
        "unrecognized_noise_retained": "Possible noise was kept for manual review.",
        "hyphenated_line_break_retained": (
            "A hyphenated line break was kept; check the original word."
        ),
        "no_english_text": "No English content was found; synthesis cannot start yet.",
    }
    return tr(labels.get(code, "Check this source passage manually."))
