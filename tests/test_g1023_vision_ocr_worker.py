"""Unit, security, and contract tests for the Python Apple Vision OCR worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from lextide import runtime
from lextide.desktop import image_ocr, vision_ocr_worker
from lextide.errors import StudioError


def _make_minimal_png(width: int, height: int) -> bytes:
    """Create a valid PNG header with arbitrary IHDR dimensions without allocating pixels."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # 1-byte dummy compressed payload (only used for header-rejection tests)
    idat = zlib.compress(b"\x00" * 4)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _make_real_png(path: Path, text: str = "Hello Lextide") -> Path:
    gui = pytest.importorskip("PySide6.QtGui")
    _app = gui.QGuiApplication.instance() or gui.QGuiApplication(["test"])
    image = gui.QImage(400, 100, gui.QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    painter = gui.QPainter(image)
    painter.setPen(gui.QColor(0, 0, 0))
    font = gui.QFont()
    font.setPixelSize(36)
    painter.setFont(font)
    painter.drawText(20, 64, text)
    painter.end()
    assert image.save(str(path), "PNG")
    return path


def _run_worker(*args: str, timeout: float = 10.0) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, "-m", "lextide.desktop.vision_ocr_worker", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    payload = json.loads(completed.stdout.decode("utf-8"))
    return completed.returncode, payload


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple Vision is macOS-only")
def test_real_vision_languages_query_contract():
    status, payload = _run_worker("--languages")
    assert status == 0
    assert payload["ok"] is True
    assert payload["provider"] == "apple-vision"
    assert payload["level"] == "accurate"
    assert isinstance(payload["revision"], int) and payload["revision"] >= 1
    assert isinstance(payload["duration_ms"], int) and payload["duration_ms"] >= 0
    supported = payload["supported_languages"]
    assert supported == sorted(supported)
    assert "en-US" in supported
    assert "zh-Hans" in supported


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple Vision is macOS-only")
def test_worker_rejects_bad_arguments_and_unsupported_languages(tmp_path):
    img = _make_real_png(tmp_path / "sample.png")
    status, payload = _run_worker("--only-one")
    assert status == 1 and payload["ok"] is False and payload["error_code"] == "INVALID_ARGUMENTS"

    status, payload = _run_worker(str(img), "fr-FR")
    assert status == 1 and payload["error_code"] == "UNSUPPORTED_LANGUAGE"

    status, payload = _run_worker(str(img), "en-US,en-US")
    assert status == 1 and payload["error_code"] == "UNSUPPORTED_LANGUAGE"

    status, payload = _run_worker(str(img), "")
    assert status == 1 and payload["error_code"] == "UNSUPPORTED_LANGUAGE"


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple Vision is macOS-only")
def test_worker_image_input_security_boundaries(tmp_path):
    real_img = _make_real_png(tmp_path / "real.png")
    before = real_img.read_bytes()

    # 1. Symlink must be refused via O_NOFOLLOW
    link = tmp_path / "link.png"
    link.symlink_to(real_img)
    status, payload = _run_worker(str(link), "en-US")
    assert status == 1 and payload["error_code"] == "INVALID_IMAGE"
    assert real_img.read_bytes() == before

    # 2. Missing file
    status, payload = _run_worker(str(tmp_path / "missing.png"), "en-US")
    assert status == 1 and payload["error_code"] == "INVALID_IMAGE"

    # 3. Empty file
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    status, payload = _run_worker(str(empty), "en-US")
    assert status == 1 and payload["error_code"] == "INVALID_IMAGE"

    # 4. Corrupt / non-image bytes
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png at all")
    status, payload = _run_worker(str(bad), "en-US")
    assert status == 1 and payload["error_code"] == "INVALID_IMAGE"

    # 5. Pixel limit (> 24,000,000 pixels) rejected from header before decompression
    huge_header = tmp_path / "huge-header.png"
    huge_header.write_bytes(_make_minimal_png(6000, 5000))
    status, payload = _run_worker(str(huge_header), "en-US")
    assert status == 1 and payload["error_code"] == "INVALID_IMAGE"

    # 6. File size limit (> 32 MiB) rejected before reading
    oversized = tmp_path / "oversized.png"
    with oversized.open("wb") as stream:
        stream.truncate(vision_ocr_worker.MAX_IMAGE_BYTES + 1)
    status, payload = _run_worker(str(oversized), "en-US")
    assert status == 1 and payload["error_code"] == "INVALID_IMAGE"


def test_runtime_vision_ocr_command_source_and_frozen(monkeypatch):
    monkeypatch.setattr(runtime, "is_frozen", lambda: False)
    assert runtime.vision_ocr_command("--languages") == [
        sys.executable,
        "-m",
        "lextide.desktop.vision_ocr_worker",
        "--languages",
    ]
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    assert runtime.vision_ocr_command("--languages") == [
        sys.executable,
        "--vision-ocr",
        "--languages",
    ]


def test_image_ocr_vision_command_respects_override_and_platform(monkeypatch, tmp_path):
    monkeypatch.delenv("LEXTIDE_VISION_HELPER", raising=False)
    monkeypatch.setattr(image_ocr.sys, "platform", "linux")
    with pytest.raises(StudioError) as caught:
        image_ocr._vision_command("--languages")
    assert caught.value.code == "OCR_UNAVAILABLE"

    monkeypatch.setattr(image_ocr.sys, "platform", "darwin")
    cmd = image_ocr._vision_command("--languages")
    assert cmd[-2:] == ["lextide.desktop.vision_ocr_worker", "--languages"]

    custom = tmp_path / "custom-helper"
    custom.write_text("#!/bin/sh\n")
    custom.chmod(0o700)
    monkeypatch.setenv("LEXTIDE_VISION_HELPER", str(custom))
    cmd = image_ocr._vision_command("--languages")
    assert cmd == [str(custom), "--languages"]


def test_deterministic_sorting_limits_and_language_mismatch(monkeypatch, capsys):
    """Exercise worker logic deterministically via synthetic Vision doubles."""

    class FakeCandidate:
        def __init__(self, text: str, conf: float):
            self._text = text
            self._conf = conf

        def string(self):
            return self._text

        def confidence(self):
            return self._conf

    class FakeObs:
        def __init__(self, text: str, conf: float, x: float, y: float, w: float, h: float):
            self._cand = FakeCandidate(text, conf)
            self._box = SimpleNamespace(
                origin=SimpleNamespace(x=x, y=y),
                size=SimpleNamespace(width=w, height=h),
            )

        def boundingBox(self):
            return self._box

        def topCandidates_(self, _n):
            return [self._cand]

    state = {"results": [], "perform_ok": True, "orientations": []}

    class FakeRequest:
        def __init__(self):
            self._langs = []

        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def setRecognitionLevel_(self, _lvl):
            pass

        def setUsesLanguageCorrection_(self, _flag):
            pass

        def revision(self):
            return 3

        def supportedRecognitionLanguagesAndReturnError_(self, _err):
            return (["en-US", "zh-Hans"], None)

        def setRecognitionLanguages_(self, langs):
            self._langs = list(langs)

        def recognitionLanguages(self):
            return self._langs

        def results(self):
            return state["results"]

    class FakeHandler:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithCGImage_orientation_options_(self, _img, orientation, _opts):
            state["orientations"].append(orientation)
            return self

        def performRequests_error_(self, _reqs, _err):
            return (state["perform_ok"], None if state["perform_ok"] else RuntimeError("boom"))

    import contextlib

    fake_objc = SimpleNamespace(autorelease_pool=contextlib.nullcontext)
    fake_vision = SimpleNamespace(
        VNRecognizeTextRequest=FakeRequest,
        VNRequestTextRecognitionLevelAccurate=0,
        VNImageRequestHandler=FakeHandler,
    )
    monkeypatch.setitem(sys.modules, "objc", fake_objc)
    monkeypatch.setitem(sys.modules, "Vision", fake_vision)
    monkeypatch.setattr(vision_ocr_worker.sys, "platform", "darwin")
    monkeypatch.setattr(vision_ocr_worker, "_load_cg_image", lambda _p: (object(), 6))

    # 1. Reading order: higher midY first; equal midY sorted by lower minX
    state["results"] = [
        FakeObs("Bottom line", 0.8, 0.1, 0.1, 0.4, 0.1),
        FakeObs("Top right", 0.9, 0.6, 0.8, 0.2, 0.1),
        FakeObs("Top left", 1.0, 0.1, 0.8, 0.2, 0.1),
    ]
    rc = vision_ocr_worker.main(["/fake.png", "en-US"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["text"] == "Top left\nTop right\nBottom line"
    assert out["effective_languages"] == ["en-US"]
    assert out["requested_languages"] == ["en-US"]
    assert pytest.approx(out["confidence"]) == 0.9
    assert state["orientations"][-1] == 6

    # 2. Chinese requested but English-only recognized -> LANGUAGE_MISMATCH
    rc = vision_ocr_worker.main(["/fake.png", "zh-Hans,en-US"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["error_code"] == "LANGUAGE_MISMATCH"

    # 3. Chinese with Han characters succeeds
    state["results"] = [FakeObs("学习英语", 0.95, 0.1, 0.5, 0.4, 0.1)]
    rc = vision_ocr_worker.main(["/fake.png", "zh-Hans"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["text"] == "学习英语"

    # 4. Empty result
    state["results"] = []
    rc = vision_ocr_worker.main(["/fake.png", "en-US"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["error_code"] == "EMPTY_RESULT"

    # 5. Exceeds 8192 observations -> OUTPUT_LIMIT
    state["results"] = [FakeObs("a", 1.0, 0.1, 0.1, 0.1, 0.1)] * 8193
    rc = vision_ocr_worker.main(["/fake.png", "en-US"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["error_code"] == "OUTPUT_LIMIT"

    # 6. Exceeds 1 MiB text -> OUTPUT_LIMIT
    state["results"] = [FakeObs("x" * (1_048_576), 1.0, 0.1, 0.1, 0.1, 0.1)]
    rc = vision_ocr_worker.main(["/fake.png", "en-US"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["error_code"] == "OUTPUT_LIMIT"

    # 7. Recognition failure
    state["results"] = []
    state["perform_ok"] = False
    rc = vision_ocr_worker.main(["/fake.png", "en-US"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["error_code"] == "RECOGNITION_FAILED"


def test_image_ocr_parent_handles_python_worker_error_codes(tmp_path, monkeypatch):
    img = _make_real_png(tmp_path / "img.png")
    before_hash = hashlib.sha256(img.read_bytes()).hexdigest()

    fake = tmp_path / "fake-vision"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "if sys.argv[1:] == ['--languages']:\n"
        "    print(json.dumps({'ok': True, 'provider': 'apple-vision', 'level': 'accurate', "
        "'revision': 3, 'duration_ms': 1, 'supported_languages': ['en-US', 'zh-Hans']}))\n"
        "else:\n"
        "    print(json.dumps({'ok': True, 'provider': 'apple-vision', 'level': 'accurate', "
        "'revision': 3, 'duration_ms': 12, 'requested_languages': ['zh-Hans', 'en-US'], "
        "'effective_languages': ['zh-Hans', 'en-US'], 'text': 'Hello\\n你好', "
        "'lines': [{'text': 'Hello', 'confidence': 0.9, 'bbox': [0, 0, 1, 1]}], "
        "'confidence': 0.9}))\n"
    )
    fake.chmod(0o700)
    monkeypatch.setenv("LEXTIDE_VISION_HELPER", str(fake))
    result = asyncio.run(image_ocr.recognize(img, provider="apple-vision", language="mixed"))
    assert result["text"] == "Hello\n你好"
    assert result["source_sha256"] == before_hash
    assert result["ocr"]["provider"] == "apple-vision"
    assert result["ocr"]["effective_languages"] == ["zh-Hans", "en-US"]
    assert result["ocr"]["confidence"] == 0.9
