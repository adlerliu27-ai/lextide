"""Offline OCR boundaries with deterministic tools; golden image metrics live separately."""

import asyncio
import hashlib
import os
import sys

import pytest

from lextide.desktop import image_ocr as ocr
from lextide.errors import StudioError


@pytest.fixture
def image_path(tmp_path):
    gui = pytest.importorskip("PySide6.QtGui")
    path = tmp_path / "picture.png"
    image = gui.QImage(350, 90, gui.QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    assert image.save(str(path))
    return path


def fake_tool(tmp_path, monkeypatch, code):
    tool = tmp_path / "tess"
    tool.write_text(f"#!{sys.executable}\n" + code)
    tool.chmod(0o700)
    monkeypatch.setenv("LEXTIDE_TESSERACT", str(tool))


def recognize(path, language="mixed"):
    return asyncio.run(ocr.recognize(path, language=language, provider="tesseract"))


def test_snapshot_is_private_and_original_hash_preserved(image_path, tmp_path, monkeypatch):
    fake_tool(
        tmp_path,
        monkeypatch,
        """
import pathlib, sys, stat
if sys.argv[1:] == ['--list-langs']:
    print('eng\\nchi_sim')
else:
    p = pathlib.Path(sys.argv[1])
    assert p.name == 'input.png' and stat.S_IMODE(p.stat().st_mode)==0o600
    assert sys.argv[2:] == ['stdout','-l','chi_sim+eng','--oem','1','--psm','6',
                            '-c','textord_noise_rejrows=0']
    print('Check 1,500.\\n学习英语。')
""",
    )
    before = image_path.read_bytes()
    result = recognize(image_path)
    assert result["text"] == "Check 1,500.\n学习英语。\n"
    assert result["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert image_path.read_bytes() == before
    assert result["ocr"]["provider"] == "tesseract"
    assert result["ocr"]["effective_languages"] == ["chi_sim", "eng"]


@pytest.mark.parametrize(
    ("inventory", "text", "code"),
    [
        ("eng", "English only", "OCR_LANGUAGE_UNAVAILABLE"),
        ("eng\nchi_sim", "English only", "OCR_LANGUAGE_MISMATCH"),
        ("eng\nchi_sim", "只有中文", "OCR_LANGUAGE_MISMATCH"),
        ("eng\nchi_sim", "  ", "OCR_EMPTY"),
    ],
)
def test_rejects_missing_languages_or_text(
    image_path, tmp_path, monkeypatch, inventory, text, code
):
    fake_tool(
        tmp_path,
        monkeypatch,
        f"import sys\nprint({inventory!r} if '--list-langs' in sys.argv else {text!r})",
    )
    with pytest.raises(StudioError) as caught:
        recognize(image_path)
    assert caught.value.code == code


def test_bad_image_and_missing_executable(image_path, monkeypatch):
    monkeypatch.setenv("LEXTIDE_TESSERACT", "/nonexistent/tesseract")
    with pytest.raises(StudioError) as caught:
        recognize(image_path)
    assert caught.value.code == "OCR_UNAVAILABLE"
    image_path.write_bytes(b"not an image")
    with pytest.raises(StudioError) as caught:
        recognize(image_path)
    assert caught.value.code == "OCR_INVALID_IMAGE"


@pytest.mark.parametrize("stop", ["timeout", "cancel", "overflow"])
def test_subprocess_cleanup_and_output_budget(stop, tmp_path):
    pid_file = tmp_path / "pid"

    async def scenario():
        code = (
            "import os,sys,time,pathlib; pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
            + ("print('x'*1000000,flush=True);" if stop == "overflow" else "")
            + "time.sleep(10)"
        )
        task = asyncio.create_task(
            ocr._run(
                [sys.executable, "-c", code, str(pid_file)], 1 if stop == "timeout" else 10, 128
            )
        )
        if stop == "cancel":
            async with asyncio.timeout(3):
                while not pid_file.exists():
                    await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(StudioError) as caught:
                await task
            assert caught.value.code == ("OCR_TIMEOUT" if stop == "timeout" else "OCR_OUTPUT_LIMIT")
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    asyncio.run(scenario())


def test_vision_cannot_accept_silent_english_fallback(image_path, tmp_path, monkeypatch):
    tool = tmp_path / "helper"
    tool.write_text(
        f"#!{sys.executable}\n"
        + """
import sys,json
print(json.dumps({'supported_languages':['zh-Hans','en-US']} if '--languages' in sys.argv
else {'ok':True,'provider':'apple-vision','effective_languages':['en-US'],'text':'English'}))
"""
    )
    tool.chmod(0o700)
    monkeypatch.setenv("LEXTIDE_VISION_HELPER", str(tool))
    with pytest.raises(StudioError) as caught:
        asyncio.run(ocr.recognize(image_path, provider="apple-vision", language="mixed"))
    assert caught.value.code == "OCR_LANGUAGE_MISMATCH"


def test_corrected_cleaning_input_and_raw_ocr_are_independently_bound():
    from lextide.desktop import service

    original = "I reed. 我读书。"
    corrected = "I read. 我读书。"
    reviewed = "I read."
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731
    request = {
        "approved_sha256": digest(reviewed),
        "provenance": {
            "text": original,
            "ocr": {
                "provider": "tesseract",
                "language": "mixed",
                "requested_languages": ["chi_sim", "eng"],
                "effective_languages": ["chi_sim", "eng"],
                "duration_ms": 50,
                "confidence": None,
            },
            "cleaning": {
                "version": "english-translations-v1",
                "mode": "english",
                "cleaning_mode": "remove_chinese_translations",
                "original_sha256": digest(original),
                "input_text": corrected,
                "input_sha256": digest(corrected),
                "cleaned_text": reviewed,
            },
        },
    }
    raw, saved = service._provenance(request, reviewed)
    assert raw == original
    assert saved["text_preparation"]["input_text"] == corrected
    assert saved["approved_tts_sha256"] == digest(reviewed)
    request["provenance"]["cleaning"]["input_text"] += "!"
    with pytest.raises(StudioError):
        service._provenance(request, reviewed)
