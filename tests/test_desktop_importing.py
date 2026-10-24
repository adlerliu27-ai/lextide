"""Self-created local fixtures only; no network, Qt or user documents."""

import codecs
import hashlib
import io
import json
import zipfile

import pytest

from lextide.desktop import importing
from lextide.desktop.importing import DocumentImportError, import_document

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def write_docx(path, body, *, extra=None):
    xml = f'<w:document xmlns:w="{WORD_NS}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


def write_pdf(path, texts, *, password=None, compress=False):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
                }
            )
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 10 200 Td (" + text.encode("ascii") + b") Tj ET")
            if compress:
                stream = stream.flate_encode()
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password is not None:
        writer.encrypt(password)
    with path.open("wb") as output:
        writer.write(output)
    return path


def test_txt_preserves_whitespace_source_hash_and_json_contract(tmp_path):
    path = tmp_path / "source.TXT"
    original = "  Hello 中文.\r\n\r\nNext\tline.\n"
    raw = original.encode()
    path.write_bytes(raw)
    result = import_document(path)
    assert result == {
        "text": original,
        "source_path": str(path),
        "source_name": path.name,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "format": "txt",
        "encoding": "utf-8",
        "changes": [],
        "warnings": [],
    }
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
    assert path.read_bytes() == raw


@pytest.mark.parametrize("codec", ["utf-8-sig", "utf-16", "utf-16-be"])
def test_bom_encodings_are_strict_and_recorded(tmp_path, codec):
    path = tmp_path / "encoded.txt"
    raw = "Hello 中文".encode(codec)
    if codec == "utf-16-be":
        raw = codecs.BOM_UTF16_BE + raw
    path.write_bytes(raw)
    result = import_document(path)
    assert result["text"] == "Hello 中文"
    assert result["encoding"] == ("utf-8-sig" if codec == "utf-8-sig" else "utf-16")
    assert any("BOM" in change for change in result["changes"])


@pytest.mark.parametrize("codec", ["gb18030", "gbk", "big5", "cp1252", "utf-16-le"])
def test_common_explicit_encodings_are_not_guessed(tmp_path, codec):
    text = "Hello café" if codec == "cp1252" else "Hello 中文"
    path = tmp_path / "encoded.txt"
    path.write_bytes(text.encode(codec))
    with pytest.raises(DocumentImportError, match="编码"):
        import_document(path)
    result = import_document(path, encoding=codec)
    assert result["text"] == text
    assert result["encoding"] == codec


@pytest.mark.parametrize("data", [b"Hello\xff", b"\xff\xfeH", b"\xff\xfe\x00\x00H\x00\x00\x00"])
def test_invalid_and_unsupported_encoded_input_fails_without_replacement(tmp_path, data):
    path = tmp_path / "invalid.txt"
    path.write_bytes(data)
    with pytest.raises(DocumentImportError):
        import_document(path)


@pytest.mark.parametrize("codec", ["rot13", "base64_codec", "unknown-codec"])
def test_arbitrary_python_codecs_are_rejected(tmp_path, codec):
    path = tmp_path / "text.txt"
    path.write_text("Hello", encoding="utf-8")
    with pytest.raises(DocumentImportError, match="编码"):
        import_document(path, encoding=codec)


def test_extracted_utf8_limit_and_exact_boundary(tmp_path):
    path = tmp_path / "large.txt"
    path.write_text("A" * importing.MAX_DOCUMENT_BYTES, encoding="utf-8")
    assert len(import_document(path)["text"]) == importing.MAX_DOCUMENT_BYTES
    # The bound applies to final UTF-8 bytes, even when another source codec is smaller.
    path.write_bytes(("中" * (importing.MAX_DOCUMENT_BYTES // 3 + 1)).encode("gbk"))
    with pytest.raises(DocumentImportError, match="1 MiB"):
        import_document(path, encoding="gbk")


def test_source_size_is_checked_before_reading(tmp_path):
    path = tmp_path / "large.pdf"
    with path.open("wb") as output:
        output.truncate(importing.MAX_SOURCE_BYTES + 1)
    with pytest.raises(DocumentImportError, match="32 MiB"):
        import_document(path)


def test_empty_missing_unsupported_and_non_file_inputs_fail_clearly(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text(" \n\t", encoding="utf-8")
    with pytest.raises(DocumentImportError, match="正文"):
        import_document(empty)
    with pytest.raises(DocumentImportError, match="无法读取"):
        import_document(tmp_path / "missing.txt")
    with pytest.raises(DocumentImportError, match="仅支持"):
        import_document(tmp_path / "macro.docm")
    directory = tmp_path / "directory.txt"
    directory.mkdir()
    with pytest.raises(DocumentImportError, match="普通文件"):
        import_document(directory)
    with pytest.raises(DocumentImportError, match="Path"):
        import_document(str(empty))


def test_markdown_reading_rules_are_predictable_and_report_omissions(tmp_path):
    path = tmp_path / "reading.md"
    path.write_text(
        "# Heading\n\nA **bold** paragraph with [link label](https://example.invalid).\n\n"
        "- First item\n- Second `inline code`\n\n![Image description](missing.png)\n\n"
        "> Quoted sentence.\n\n```python\nsecret_code()\n```\n\n"
        "| Name | Value |\n| --- | --- |\n| Alpha | One |\n\n"
        "<script>do_not_read_or_execute()</script>\n\nLast paragraph.",
        encoding="utf-8",
    )
    result = import_document(path)
    assert result["text"] == (
        "Heading\n\nA bold paragraph with link label.\n\nFirst item\n\nSecond inline code\n\n"
        "Image description\n\nQuoted sentence.\nName；Value\nAlpha；One\n\nLast paragraph."
    )
    assert "secret_code" not in result["text"]
    assert "do_not_read" not in result["text"]
    assert any("代码块" in warning for warning in result["warnings"])
    assert any("HTML" in warning for warning in result["warnings"])
    assert any("链接" in change for change in result["changes"])
    assert any("表格" in change for change in result["changes"])


def test_markdown_inline_html_is_plain_and_script_payload_is_omitted(tmp_path):
    path = tmp_path / "html.md"
    path.write_text("Hello <b>bold</b> <script>dangerous()</script> world.", encoding="utf-8")
    result = import_document(path)
    assert result["text"] == "Hello bold  world."
    assert result["warnings"]


def test_markdown_code_only_is_not_successfully_imported_as_empty(tmp_path):
    path = tmp_path / "code.md"
    path.write_text("```\nOnly code\n```", encoding="utf-8")
    with pytest.raises(DocumentImportError, match="正文"):
        import_document(path)


def test_markdown_source_bound_is_enforced_before_parsing(tmp_path):
    path = tmp_path / "large.md"
    path.write_text("#" * (importing.MAX_MARKDOWN_BYTES + 1), encoding="utf-8")
    with pytest.raises(DocumentImportError, match="2 MiB"):
        import_document(path)


def test_markdown_deep_structure_is_rejected_instead_of_silently_losing_text(tmp_path):
    path = tmp_path / "deep.md"
    path.write_text("Visible\n\n" + "> " * 40 + "Must not be lost", encoding="utf-8")
    with pytest.raises(DocumentImportError, match="嵌套"):
        import_document(path)


def test_docx_body_table_order_and_external_content_omission(tmp_path):
    path = write_docx(
        tmp_path / "document.docx",
        "<w:p><w:r><w:t>First paragraph.</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell A</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Cell A continued</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Cell B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "<w:p><w:hyperlink><w:r><w:t>Visible link</w:t></w:r></w:hyperlink>"
        "<w:r><w:tab/><w:t>End.</w:t><w:br/><w:t>Line.</w:t></w:r>"
        "<w:del><w:r><w:t>Deleted text</w:t></w:r></w:del></w:p><w:altChunk/>",
        extra={
            "word/header1.xml": "DO NOT READ HEADER",
            "word/vbaProject.bin": b"NOT EXECUTABLE",
            "word/_rels/document.xml.rels": '<Relationship Target="https://example.invalid"/>',
        },
    )
    before = path.read_bytes()
    result = import_document(path)
    assert result["text"] == (
        "First paragraph.\n\nCell A Cell A continued；Cell B\n\nVisible link\tEnd.\nLine."
    )
    assert result["encoding"] is None
    assert result["format"] == "docx"
    assert any("宏" in warning for warning in result["warnings"])
    assert any("外部" in warning for warning in result["warnings"])
    assert path.read_bytes() == before


def test_docx_nested_tables_and_content_controls_have_stable_order(tmp_path):
    path = write_docx(
        tmp_path / "nested.docx",
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>Intro</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt><w:tbl><w:tr><w:tc><w:tbl><w:tr><w:tc>"
        "<w:p><w:r><w:t>Nested</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "</w:tc></w:tr></w:tbl><w:p><w:r><w:t>Tail</w:t></w:r></w:p>",
    )
    assert import_document(path)["text"] == "Intro\n\nNested\n\nTail"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_docx_dtd_and_entities_are_rejected_including_utf16(tmp_path, encoding):
    path = tmp_path / "entity.docx"
    xml = (
        '<?xml version="1.0" encoding="' + encoding + '"?>'
        '<!DOCTYPE document [<!ENTITY bad SYSTEM "file:///private/secret">]>'
        f'<w:document xmlns:w="{WORD_NS}"><w:body><w:p>&bad;</w:p></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml.encode(encoding))
    with pytest.raises(DocumentImportError, match="DTD/实体"):
        import_document(path)


def test_docx_zip_bomb_is_rejected_before_decompression(tmp_path):
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"A" * 200_000)
    with pytest.raises(DocumentImportError, match="压缩比"):
        import_document(path)


def test_docx_xml_depth_and_zip_entry_limits(tmp_path):
    path = write_docx(tmp_path / "deep.docx", "<w:x>" * 70 + "</w:x>" * 70)
    with pytest.raises(DocumentImportError, match="XML 结构"):
        import_document(path)
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(importing.MAX_ZIP_ENTRIES + 1):
            archive.writestr(f"entry-{index}", "")
    with pytest.raises(DocumentImportError, match="ZIP 条目"):
        import_document(path)


def test_docx_duplicate_members_and_corrupt_content_fail(tmp_path):
    path = tmp_path / "broken.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<broken>")
    with pytest.raises(DocumentImportError, match="XML 损坏"):
        import_document(path)
    with zipfile.ZipFile(path, "a") as archive:
        with pytest.warns(UserWarning, match="Duplicate"):
            archive.writestr("word/document.xml", "second")
    with pytest.raises(DocumentImportError, match="重复"):
        import_document(path)
    path.write_bytes(b"Not ZIP")
    with pytest.raises(DocumentImportError, match="ZIP"):
        import_document(path)


def test_pdf_real_text_layer_page_order_blank_page_warning_and_immutable_source(tmp_path):
    path = write_pdf(tmp_path / "pages.pdf", ["First page.", "", "Third page."], compress=True)
    before = path.read_bytes()
    result = import_document(path)
    assert result["text"] == "First page.\n\nThird page."
    assert result["encoding"] is None
    assert any("1 页" in warning for warning in result["warnings"])
    assert any("多栏" in warning for warning in result["warnings"])
    assert path.read_bytes() == before


def test_pdf_without_text_layer_has_exact_required_message(tmp_path):
    path = write_pdf(tmp_path / "scan.pdf", [""])
    with pytest.raises(DocumentImportError) as caught:
        import_document(path)
    assert str(caught.value) == "该 PDF 没有可提取的文本层，当前版本暂不支持 OCR。"


def test_pdf_encrypted_and_corrupt_have_distinct_messages(tmp_path):
    path = write_pdf(tmp_path / "protected.pdf", ["Private text"], password="test-password")
    with pytest.raises(DocumentImportError, match="加密"):
        import_document(path)
    path.write_bytes(b"%PDF-1.7\ncorrupt")
    with pytest.raises(DocumentImportError, match="损坏"):
        import_document(path)


def test_pdf_page_limit(tmp_path):
    path = write_pdf(tmp_path / "many-pages.pdf", [""] * (importing.MAX_PDF_PAGES + 1))
    with pytest.raises(DocumentImportError, match="300 页"):
        import_document(path)


def test_pdf_extracted_text_limit(tmp_path):
    path = write_pdf(tmp_path / "large-text.pdf", ["A" * (importing.MAX_DOCUMENT_BYTES + 1)])
    with pytest.raises(DocumentImportError, match="1 MiB"):
        import_document(path)


def test_pdf_decompression_budget_is_enforced_and_configuration_restored(tmp_path):
    from pypdf import get_configuration

    original = get_configuration()
    path = write_pdf(
        tmp_path / "compressed-stream.pdf",
        ["A" * (importing.MAX_PDF_STREAM_BYTES + 1)],
        compress=True,
    )
    assert path.stat().st_size < 100_000
    with pytest.raises(DocumentImportError, match="安全上限"):
        import_document(path)
    assert get_configuration() == original


def test_pdf_broken_xform_cannot_silently_return_partial_text(tmp_path):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject

    path = write_pdf(tmp_path / "broken-form.pdf", ["Visible text"])
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(path.read_bytes())))
    page = writer.pages[0]
    contents = page.get_contents()
    contents.set_data(contents.get_data() + b" /MissingForm Do")
    page[NameObject("/Contents")] = writer._add_object(contents)
    with path.open("wb") as output:
        writer.write(output)
    with pytest.raises(DocumentImportError, match="无法保证文本完整"):
        import_document(path)


def test_non_regular_fifo_is_rejected_without_blocking(tmp_path):
    import os

    path = tmp_path / "pipe.txt"
    os.mkfifo(path)
    with pytest.raises(DocumentImportError, match="普通文件"):
        import_document(path)


def test_pdf_actions_do_not_execute_or_fetch_any_resources(tmp_path, monkeypatch):
    import socket

    from pypdf import PdfReader, PdfWriter
    from pypdf.actions import JavaScript

    path = write_pdf(tmp_path / "actions.pdf", ["Safe text"])
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(path.read_bytes())))
    writer.add_open_action(JavaScript("app.alert('must not run');"))
    with path.open("wb") as output:
        writer.write(output)

    def deny_network(*args, **kwargs):
        raise AssertionError("Document importing must remain local")

    monkeypatch.setattr(socket, "socket", deny_network)
    assert import_document(path)["text"] == "Safe text"
