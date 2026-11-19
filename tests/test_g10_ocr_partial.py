"""Partial OCR is an explicit review result, never a fallback from provider failures."""

import asyncio
import hashlib
import json
import stat

import pytest

from lextide.desktop import image_ocr as ocr
from lextide.errors import StudioError


@pytest.fixture
def captured_image(tmp_path, monkeypatch):
    path = tmp_path / "source.png"
    data = b"a bounded image snapshot"
    path.write_bytes(data)
    monkeypatch.setattr(ocr, "_image_bytes", lambda source: source.read_bytes())
    return path, data


def adapter(monkeypatch, *, text="English only.", failure=None):
    seen = []

    async def recognize(local, language):
        seen.append(local)
        assert stat.S_IMODE(local.stat().st_mode) == 0o600
        if failure is not None:
            raise failure
        return text, ocr._languages("tesseract", language), None

    monkeypatch.setattr(ocr, "_tesseract", recognize)
    return seen


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("mixed", "English only."),
        ("mixed", "只有中文。"),
        ("zh", "Chinese glyphs mistaken for Latin."),
        ("en", "只有中文。"),
        ("mixed", "123?!"),
    ],
)
def test_nonempty_language_mismatch_requires_explicit_partial_opt_in(
    captured_image, monkeypatch, language, text
):
    path, data = captured_image
    snapshots = adapter(monkeypatch, text=text)
    with pytest.raises(StudioError) as caught:
        asyncio.run(ocr.recognize(path, provider="tesseract", language=language))
    assert caught.value.code == "OCR_LANGUAGE_MISMATCH"
    result = asyncio.run(
        ocr.recognize(path, provider="tesseract", language=language, allow_partial=True)
    )
    assert result["review_required"] is True
    assert result["review_reason"] == "OCR_LANGUAGE_MISMATCH"
    assert result["text"] == text
    assert result["source_sha256"] == hashlib.sha256(data).hexdigest()
    assert result["source_path"] == str(path.absolute())
    assert result["source_name"] == "source.png"
    assert path.read_bytes() == data
    assert ocr.checked_metadata(result["ocr"]) == result["ocr"]
    assert len(snapshots) == 2 and all(not snapshot.parent.exists() for snapshot in snapshots)


@pytest.mark.parametrize(
    ("language", "text"),
    [("en", "English."), ("zh", "中文。"), ("mixed", "English. 中文。")],
)
def test_complete_result_remains_ordinary_import(captured_image, monkeypatch, language, text):
    path, _ = captured_image
    adapter(monkeypatch, text=text)
    result = asyncio.run(
        ocr.recognize(path, provider="tesseract", language=language, allow_partial=True)
    )
    assert result["text"] == text
    assert not result.get("review_required")
    assert "review_reason" not in result


@pytest.mark.parametrize(
    "code",
    [
        "OCR_UNAVAILABLE",
        "OCR_LANGUAGE_UNAVAILABLE",
        "OCR_TIMEOUT",
        "OCR_FAILED",
        "OCR_OUTPUT_LIMIT",
        "OCR_LANGUAGE_MISMATCH",
    ],
)
def test_provider_errors_never_become_partial_results(captured_image, monkeypatch, code):
    path, data = captured_image
    snapshots = adapter(monkeypatch, failure=ocr._error(code))
    with pytest.raises(StudioError) as caught:
        asyncio.run(ocr.recognize(path, provider="tesseract", language="mixed", allow_partial=True))
    assert caught.value.code == code
    assert path.read_bytes() == data
    assert len(snapshots) == 1 and not snapshots[0].parent.exists()


def test_cancel_cleans_snapshot_without_returning_partial(captured_image, monkeypatch):
    path, _ = captured_image
    snapshots = adapter(monkeypatch, failure=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ocr.recognize(path, provider="tesseract", language="mixed", allow_partial=True))
    assert len(snapshots) == 1 and not snapshots[0].parent.exists()


@pytest.mark.parametrize(
    ("text", "code"), [("  \n", "OCR_EMPTY"), ("long text", "OCR_OUTPUT_LIMIT")]
)
def test_empty_and_oversized_output_remain_hard_failures(captured_image, monkeypatch, text, code):
    path, _ = captured_image
    if code == "OCR_OUTPUT_LIMIT":
        monkeypatch.setattr(ocr, "MAX_DOCUMENT_BYTES", 3)
    snapshots = adapter(monkeypatch, text=text)
    with pytest.raises(StudioError) as caught:
        asyncio.run(ocr.recognize(path, provider="tesseract", language="mixed", allow_partial=True))
    assert caught.value.code == code
    assert len(snapshots) == 1 and not snapshots[0].parent.exists()


@pytest.mark.parametrize("helper_error", [False, True])
def test_vision_language_failure_is_not_an_accepted_fallback(
    captured_image, monkeypatch, helper_error
):
    path, _ = captured_image
    monkeypatch.setattr(ocr, "_tool", lambda _provider: "/fake/vision")

    async def run(args, _timeout, _limit):
        if args[-1] == "--languages":
            return 0, json.dumps({"supported_languages": ["zh-Hans", "en-US"]})
        if helper_error:
            return 1, json.dumps({"ok": False, "error_code": "LANGUAGE_MISMATCH"})
        return 0, json.dumps(
            {
                "ok": True,
                "provider": "apple-vision",
                "effective_languages": ["en-US"],
                "text": "An unrequested fallback.",
            }
        )

    monkeypatch.setattr(ocr, "_run", run)
    with pytest.raises(StudioError) as caught:
        asyncio.run(
            ocr.recognize(path, provider="apple-vision", language="mixed", allow_partial=True)
        )
    assert caught.value.code == "OCR_LANGUAGE_MISMATCH"


def test_failed_tesseract_with_nonempty_stdout_cannot_supply_partial(captured_image, monkeypatch):
    path, _ = captured_image
    # Reuse an existing executable: freshly written shebang scripts have variable
    # host launch latency. Keep the real subprocess runner and its production limits.
    execute = ocr._run
    script = (
        'if [ "$1" = "--list-langs" ]; then printf "eng\\nchi_sim\\n"; '
        'else printf "Incomplete text from a failed recognition.\\n"; exit 1; fi'
    )

    async def run(args, timeout, limit):
        return await execute([args[0], "-c", script, "fake-tesseract", *args[1:]], timeout, limit)

    monkeypatch.setattr(ocr, "_tool", lambda _provider: "/bin/sh")
    monkeypatch.setattr(ocr, "_run", run)
    with pytest.raises(StudioError) as caught:
        asyncio.run(ocr.recognize(path, provider="tesseract", language="mixed", allow_partial=True))
    assert caught.value.code == "OCR_FAILED"
