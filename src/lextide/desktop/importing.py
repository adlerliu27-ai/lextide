"""Local, bounded document extraction for the desktop worker (no Qt dependency).

``import_document`` is synchronous and must run in the desktop's dedicated,
time-limited import process. These byte/structure limits complement that process
boundary; they are not a promise that arbitrary PDF parsing has constant cost.
Original files are only opened for reading. No archive member is extracted to
disk, no external resource is fetched, and no macro/script is evaluated.

Rules: TXT retains its decoded whitespace; Markdown removes presentation syntax,
keeps link labels/image alt text/inline code, omits fenced and indented code and
raw HTML blocks, and reads tables row by row with semicolon-separated cells.
DOCX reads body paragraphs/tables in order, joins cell paragraphs with spaces,
and omits headers, footers, notes, drawings, deleted revisions and field commands.
PDF reads the text layer in page order; visual column order is not guaranteed.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import logging
import os
import re
import stat
import threading
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from ..longform.document import MAX_DOCUMENT_BYTES

# Conservative first-release limits, independent of the source's claimed sizes.
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
MAX_MARKDOWN_TOKENS = 100_000
MAX_MARKDOWN_NESTING = 32
MAX_ZIP_ENTRIES = 1000
MAX_ZIP_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 16 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 250
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
MAX_XML_ELEMENTS = 100_000
MAX_XML_DEPTH = 64
MAX_PDF_PAGES = 300
MAX_PDF_STREAM_BYTES = 8 * 1024 * 1024
MAX_PDF_TOTAL_CONTENT_BYTES = 64 * 1024 * 1024
MAX_PDF_OBJECTS = 100_000
SUPPORTED_ENCODINGS = (
    "auto",
    "utf-8",
    "utf-8-sig",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "gb18030",
    "gbk",
    "big5",
    "cp1252",
    "iso8859-1",
)
PDF_NO_TEXT_MESSAGE = "该 PDF 没有可提取的文本层，当前版本暂不支持 OCR。"
_WORD_NAMESPACES = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
)


class DocumentImportError(ImportError):
    """A safe, user-facing Chinese import error, without source content."""


def _check_text_size(text: str, limit: int = MAX_DOCUMENT_BYTES) -> None:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise DocumentImportError("文件包含无效 Unicode 字符，无法安全导入。") from None
    if size > limit:
        label = "1 MiB" if limit == MAX_DOCUMENT_BYTES else "2 MiB"
        raise DocumentImportError(f"提取文本超过 {label} 上限；请拆分文件后重试，不会静默截断。")


class _TextParts:
    """Check incremental output before keeping it, including separators."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.size = 0

    def add(self, text: str, separator: str = "\n\n") -> None:
        if not text:
            return
        prefix = separator if self.parts else ""
        self.size += len((prefix + text).encode("utf-8"))
        if self.size > MAX_DOCUMENT_BYTES:
            raise DocumentImportError("提取文本超过 1 MiB 上限；请拆分文件后重试，不会静默截断。")
        self.parts.append(prefix + text)

    def text(self) -> str:
        return "".join(self.parts)


def _read_source(path: Path) -> bytes:
    # O_NONBLOCK prevents a path replaced with a FIFO from blocking on open.
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise DocumentImportError("请选择本地普通文件，不能导入目录或设备。")
        if info.st_size > MAX_SOURCE_BYTES:
            raise DocumentImportError("源文件超过 32 MiB 上限；请拆分文件后重试。")
        source = os.fdopen(descriptor, "rb")
        descriptor = None  # source now owns and closes the file descriptor.
        with source:
            data = source.read(MAX_SOURCE_BYTES + 1)
    except OSError:
        raise DocumentImportError("无法读取文件；请检查文件是否存在以及读取权限。") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(data) > MAX_SOURCE_BYTES:
        raise DocumentImportError("源文件超过 32 MiB 上限；请拆分文件后重试。")
    return data


def _decode(data: bytes, encoding: str, changes: list[str]) -> tuple[str, str]:
    if not isinstance(encoding, str):
        raise DocumentImportError("请选择受支持的文本编码。")
    selected = encoding.lower().strip()
    if selected == "auto":
        if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            raise DocumentImportError("暂不支持 UTF-32；请先在本地另存为 UTF-8。")
        if data.startswith(codecs.BOM_UTF8):
            selected = "utf-8-sig"
        elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            selected = "utf-16"
        else:
            selected = "utf-8"
    else:
        try:
            selected = codecs.lookup(selected).name
        except LookupError:
            raise DocumentImportError(
                "不支持所选编码；请选择 UTF-8、UTF-16 或常见文本编码。"
            ) from None
        if selected not in SUPPORTED_ENCODINGS:
            raise DocumentImportError("不支持所选编码；请选择 UTF-8、UTF-16 或常见文本编码。")
    try:
        text = data.decode(selected, errors="strict")
    except UnicodeError:
        raise DocumentImportError(
            "无法按所选编码解码文件；自动识别只接受 UTF-8 或带 BOM 的 UTF-16。"
            "请明确选择正确编码或在本地另存为 UTF-8，不会丢弃无法识别的字符。"
        ) from None
    if text.startswith("\ufeff"):
        text = text[1:]
        changes.append("已移除文件开头的 Unicode BOM。")
    elif selected in {"utf-8-sig", "utf-16"} and data.startswith(
        (codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
    ):
        changes.append("已按 BOM 识别编码并移除文件开头的 BOM。")
    if "\x00" in text:
        raise DocumentImportError("文本包含 NUL 字符；可能是无 BOM 的 UTF-16，请明确选择正确编码。")
    return text, selected


def _markdown(text: str, changes: list[str], warnings: list[str]) -> str:
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        raise DocumentImportError(
            "Markdown 导入依赖尚未安装；请安装 Lextide desktop 依赖。"
        ) from None
    _check_text_size(text, MAX_MARKDOWN_BYTES)
    tokens = (
        MarkdownIt("commonmark", {"html": True, "maxNesting": MAX_MARKDOWN_NESTING})
        .enable("table")
        .parse(text)
    )
    if sum(1 + len(token.children or []) for token in tokens) > MAX_MARKDOWN_TOKENS:
        raise DocumentImportError("Markdown 结构过于复杂，超过 100000 个解析节点上限。")
    # markdown-it stops parsing at its nesting limit without an exception.
    # Reject that boundary instead of accepting an incomplete extraction.
    if any(token.level >= MAX_MARKDOWN_NESTING - 1 for token in tokens):
        raise DocumentImportError("Markdown 嵌套达到 32 层安全上限；请简化结构后重试。")
    counts: Counter = Counter()
    parts = _TextParts()
    row: list[str] | None = None
    # Raw executable HTML is never evaluated. Suppress its inline payload too.
    ignored_html: list[str] = []

    def inline(children) -> str:
        output = []
        for child in children or []:
            kind = child.type
            if kind == "html_inline":
                counts["html"] += 1
                tag = re.match(r"</?\s*(script|style|iframe|object)\b", child.content, re.I)
                if tag:
                    name = tag.group(1).lower()
                    if child.content.startswith("</"):
                        if name in ignored_html:
                            del ignored_html[ignored_html.index(name) :]
                    elif not child.content.rstrip().endswith("/>"):
                        ignored_html.append(name)
                continue
            if ignored_html:
                continue
            if kind in {"text", "code_inline"}:
                output.append(child.content)
                counts[kind] += 1
            elif kind in {"softbreak", "hardbreak"}:
                output.append("\n")
            elif kind == "image":
                counts["image"] += 1
                output.append(inline(child.children))
            elif kind == "link_open":
                counts["link"] += 1
        return "".join(output)

    for token in tokens:
        kind = token.type
        if kind in {"fence", "code_block"}:
            counts["code_block"] += 1
        elif kind == "html_block":
            counts["html"] += 1
        elif kind == "tr_open":
            row = []
        elif kind == "tr_close":
            parts.add("；".join(row or []), "\n")
            row = None
        elif kind == "inline":
            value = inline(token.children).strip()
            if row is not None:
                row.append(value)
            else:
                parts.add(value)
        elif kind in {"heading_open", "bullet_list_open", "ordered_list_open", "blockquote_open"}:
            counts["structure"] += 1
        elif kind == "table_open":
            counts["table"] += 1
    changes.append("Markdown 已转为正文：标题、强调、引用和列表保留文字，段落以换行分隔。")
    if counts["link"]:
        changes.append("链接只保留可见文字，省略目标地址；未读取链接内容。")
    if counts["image"]:
        changes.append("图片只保留替代文字（alt text），未读取图片。")
    if counts["code_inline"]:
        changes.append("行内代码保留文字并移除反引号。")
    if counts["table"]:
        changes.append("Markdown 表格按行提取，单元格以中文分号分隔。")
    if counts["code_block"]:
        warnings.append(f"已省略 {counts['code_block']} 个代码块；请在预览中确认需要朗读的内容。")
    if counts["html"]:
        warnings.append("已省略原始 HTML 块/标签以及 script、style 等内嵌内容；未执行脚本。")
    return parts.text()


class _BoundedXML(ET.TreeBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.elements = 0

    def start(self, tag, attrs):
        self.depth += 1
        self.elements += 1
        if self.depth > MAX_XML_DEPTH or self.elements > MAX_XML_ELEMENTS:
            raise DocumentImportError("DOCX XML 结构超过安全上限；请简化文档后重试。")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def doctype(self, name, pubid, system):
        raise DocumentImportError("DOCX XML 含不受支持的 DTD/实体声明，已停止导入。")


def _docx(data: bytes, changes: list[str], warnings: list[str]) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise DocumentImportError("DOCX ZIP 条目超过 1000 个安全上限。")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise DocumentImportError("DOCX 含重复 ZIP 条目，无法可靠识别正文。")
            if sum(entry.file_size for entry in entries) > MAX_ZIP_EXPANDED_BYTES:
                raise DocumentImportError("DOCX 展开大小超过 64 MiB 安全上限。")
            for entry in entries:
                if entry.flag_bits & 1:
                    raise DocumentImportError("不支持加密的 DOCX ZIP；请在本地解密后导入。")
                if entry.file_size > MAX_ZIP_MEMBER_BYTES or (
                    entry.file_size > max(entry.compress_size, 1) * MAX_ZIP_COMPRESSION_RATIO
                ):
                    raise DocumentImportError("DOCX 内部文件大小或压缩比超过安全上限。")
            if "word/document.xml" not in names:
                raise DocumentImportError("DOCX 缺少 word/document.xml 正文，文件可能损坏。")
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise DocumentImportError("DOCX 正文 XML 超过 8 MiB 安全上限。")
            with archive.open(info) as member:
                xml = member.read(MAX_DOCX_XML_BYTES + 1)
            if len(xml) > MAX_DOCX_XML_BYTES:
                raise DocumentImportError("DOCX 正文 XML 超过 8 MiB 安全上限。")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError):
        raise DocumentImportError(
            "无法读取 DOCX ZIP；文件可能损坏或使用不受支持的压缩方式。"
        ) from None
    # The parser callback is authoritative; this also catches UTF-16/32 DTDs.
    declaration_scan = xml.replace(b"\0", b"").upper()
    if b"<!DOCTYPE" in declaration_scan or b"<!ENTITY" in declaration_scan:
        raise DocumentImportError("DOCX XML 含不受支持的 DTD/实体声明，已停止导入。")
    try:
        root = ET.fromstring(xml, parser=ET.XMLParser(target=_BoundedXML()))
    except ET.ParseError:
        raise DocumentImportError("DOCX 正文 XML 损坏，无法提取文本。") from None
    namespace = next((item for item in _WORD_NAMESPACES if root.tag == f"{{{item}}}document"), None)
    if namespace is None:
        raise DocumentImportError("DOCX 正文命名空间不受支持或文件已损坏。")
    tag = lambda name: f"{{{namespace}}}{name}"  # noqa: E731
    body = root.find(tag("body"))
    if body is None:
        raise DocumentImportError("DOCX 缺少正文 body，文件可能损坏。")
    excluded = {tag(name) for name in ("drawing", "object", "pict", "del")}

    def paragraph(node) -> str:
        output = []

        def visit(element):
            if element.tag in excluded:
                return
            if element.tag == tag("t"):
                output.append(element.text or "")
            elif element.tag in {tag("br"), tag("cr")}:
                output.append("\n")
            elif element.tag == tag("tab"):
                output.append("\t")
            else:
                for child in element:
                    visit(child)

        visit(node)
        return "".join(output).strip()

    def blocks(parent):
        for child in parent:
            if child.tag == tag("p"):
                yield paragraph(child)
            elif child.tag == tag("tbl"):
                rows = []
                for row in child.findall(tag("tr")):
                    cells = [
                        " ".join(value.replace("\n", " ") for value in blocks(cell) if value)
                        for cell in row.findall(tag("tc"))
                    ]
                    rows.append("；".join(cells))
                yield "\n".join(rows)
            elif child.tag in {tag("sdt"), tag("sdtContent"), tag("customXml"), tag("ins")}:
                yield from blocks(child)

    parts = _TextParts()
    for value in blocks(body):
        parts.add(value)
    changes.append("DOCX 按正文顺序提取段落和表格；表格每行换行，单元格用中文分号分隔。")
    warnings.append("DOCX 仅导入正文；页眉、页脚、脚注、批注、绘图和删除修订未导入，请核对预览。")
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        warnings.append("文件含宏数据，已忽略；未执行宏。")
    if any(element.tag == tag("altChunk") for element in body.iter()):
        warnings.append("DOCX 含外部/嵌入内容引用（altChunk），已忽略且未读取外部资源。")
    return parts.text()


class _PDFDiagnostics(logging.Handler):
    """Do not turn pypdf's recover-and-skip warnings into successful imports."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.thread_id = threading.get_ident()
        self.warned = False

    def emit(self, record) -> None:
        if record.thread == self.thread_id:
            self.warned = True


def _pdf(data: bytes, changes: list[str], warnings: list[str]) -> str:
    try:
        from pypdf import PdfReader, apply_configuration
        from pypdf.errors import LimitReachedError
    except ImportError:
        raise DocumentImportError("PDF 导入依赖尚未安装；请安装 Lextide desktop 依赖。") from None
    # Public pypdf configuration is context-local and restored on every exit.
    limits = {
        "maximum_declared_stream_length": MAX_PDF_STREAM_BYTES,
        "array_based_stream_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "array_maximum_element_count": 10_000,
        "dictionary_maximum_element_count": 10_000,
        "stream_filters_maximum_length": 8,
        "stream_decoding_work_maximum_length": MAX_PDF_STREAM_BYTES * 4,
        "stream_parsing_work_maximum_count": 200_000,
        "zlib_maximum_recovery_input_length": 100_000,
        "flate_maximum_columns": 10_000,
        "flate_maximum_row_length": 1_000_000,
        "image_maximum_buffer_size": MAX_PDF_STREAM_BYTES,
        "page_tree_maximum_entries": 1000,
        "page_tree_maximum_depth": 32,
        "xform_maximum_invocations_per_extraction": 100,
        "jbig2dec_binary": None,
    }
    for codec in ("jbig2", "lzw", "run_length", "zlib", "brotli"):
        limits[f"{codec}_maximum_output_length"] = MAX_PDF_STREAM_BYTES
    parts = _TextParts()
    blank_pages = 0
    total_stream_bytes = 0
    diagnostics = _PDFDiagnostics()
    logger = logging.getLogger("pypdf")
    logger.addHandler(diagnostics)
    try:
        with apply_configuration(**limits):
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise DocumentImportError("该 PDF 已加密，请先在本地解密后再导入。")
            if sum(len(entries) for entries in reader.xref.values()) > MAX_PDF_OBJECTS:
                raise DocumentImportError("PDF 对象数量超过安全上限。")
            declared_pages = reader.trailer["/Root"]["/Pages"].get("/Count", 0)
            if int(declared_pages) > MAX_PDF_PAGES or len(reader.pages) > MAX_PDF_PAGES:
                raise DocumentImportError("PDF 超过 300 页上限；请拆分文件后重试。")
            for page in reader.pages:
                contents = page.get_contents()
                if contents is not None:
                    stream_size = len(contents.get_data())
                    total_stream_bytes += stream_size
                    if (
                        stream_size > MAX_PDF_STREAM_BYTES
                        or total_stream_bytes > MAX_PDF_TOTAL_CONTENT_BYTES
                    ):
                        raise DocumentImportError("PDF 内容流超过安全上限；请拆分或简化文件。")
                value = page.extract_text(extraction_mode="plain").strip()
                if diagnostics.warned:
                    raise DocumentImportError(
                        "PDF 内容存在解析异常或超过安全上限，无法保证文本完整；请修复或简化文件。"
                    )
                if not value:
                    blank_pages += 1
                parts.add(value)
    except DocumentImportError:
        raise
    except LimitReachedError:
        raise DocumentImportError("PDF 解析或解压超过安全上限；请拆分或简化文件。") from None
    except Exception:
        raise DocumentImportError(
            "PDF 文件损坏、结构不受支持或文本提取失败；请检查文件后重试。"
        ) from None
    finally:
        logger.removeHandler(diagnostics)
        diagnostics.close()
    if not parts.parts:
        raise DocumentImportError(PDF_NO_TEXT_MESSAGE)
    changes.append("PDF 按页序提取文本层，页间以空行分隔。")
    warnings.append("PDF 的多栏、阅读顺序、连字和页眉页脚可能与视觉布局不同，请核对并编辑预览。")
    if blank_pages:
        warnings.append(f"其中 {blank_pages} 页没有可提取文本；可能为空白页或扫描页，未执行 OCR。")
    return parts.text()


def import_document(path: Path, encoding: str = "auto") -> dict:
    """Return a JSON-serializable extraction and source identity, or a clear error.

    ``changes``/``warnings`` are lists of displayable strings. ``encoding`` is
    the actual strict codec for TXT/MD and ``None`` for DOCX/PDF. The SHA-256
    identifies exactly the bounded bytes parsed, even if the source later changes.
    Text confirmation/cleanup remains the G3 application service's responsibility.
    """
    if not isinstance(path, Path):
        raise DocumentImportError("导入路径必须是本地 Path 文件路径。")
    kind = path.suffix.lower().lstrip(".")
    if kind not in {"txt", "md", "docx", "pdf"}:
        raise DocumentImportError("仅支持 TXT、Markdown (.md)、DOCX 和文本层 PDF。")
    data = _read_source(path)
    changes: list[str] = []
    warnings: list[str] = []
    actual_encoding = None
    if kind in {"txt", "md"}:
        text, actual_encoding = _decode(data, encoding, changes)
        if kind == "md":
            text = _markdown(text, changes, warnings)
    elif kind == "docx":
        text = _docx(data, changes, warnings)
    else:
        text = _pdf(data, changes, warnings)
    _check_text_size(text)
    if not text.strip():
        raise DocumentImportError("文件没有可导入的正文文本；请检查内容及提取规则。")
    return {
        "text": text,
        "source_path": str(path.absolute()),
        "source_name": path.name,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "format": kind,
        "encoding": actual_encoding,
        "changes": changes,
        "warnings": warnings,
    }
