"""Local Apple Vision OCR worker using PyObjC bindings.

Runs in a dedicated subprocess so the Python parent can enforce the 8-second
recognition deadline, cancellation, output budgets, and process reaping without
blocking the Qt GUI main thread. Performs at most one accurate recognition
request and never falls back to another language or OCR engine.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
import unicodedata
from typing import Any

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_OBSERVATIONS = 8192
MAX_OUTPUT_BYTES = 1_048_576
ALLOWED_IMAGE_TYPES = frozenset({"public.png", "public.jpeg"})
ALLOWED_LANGUAGES = frozenset({"en-US", "zh-Hans"})

# Covers CJK Unified Ideographs (BMP + Ext A + Compatibility + Ext B..G).
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]")


def _has_han_character(text: str) -> bool:
    if _HAN_RE.search(text):
        return True
    for char in text:
        if ord(char) >= 0x2E80 and unicodedata.name(char, "").startswith("CJK "):
            return True
    return False


def _emit(payload: dict[str, Any], *, revision: int, started: float, status: int) -> int:
    result = dict(payload)
    result["provider"] = "apple-vision"
    result["level"] = "accurate"
    result["revision"] = revision
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    try:
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sys.stdout.buffer.write(encoded.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    except OSError:
        return 2
    return status


def _fail(code: str, *, revision: int, started: float) -> int:
    return _emit({"ok": False, "error_code": code}, revision=revision, started=started, status=1)


def _read_bounded_image_bytes(path: str) -> bytes | None:
    """Read a single bounded regular file through one non-following file descriptor."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > MAX_IMAGE_BYTES:
            os.close(descriptor)
            return None
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        return None
    if len(data) != info.st_size:
        return None
    return data


def _load_cg_image(path: str) -> tuple[Any, int] | None:
    """Validate image container headers and pixel limits before decompressing pixels."""
    import Quartz.ImageIO as ImageIO

    raw_bytes = _read_bounded_image_bytes(path)
    if raw_bytes is None:
        return None
    source = ImageIO.CGImageSourceCreateWithData(raw_bytes, None)
    if source is None:
        return None
    image_type = ImageIO.CGImageSourceGetType(source)
    if image_type is None or str(image_type) not in ALLOWED_IMAGE_TYPES:
        return None
    if int(ImageIO.CGImageSourceGetCount(source)) != 1:
        return None
    properties = ImageIO.CGImageSourceCopyPropertiesAtIndex(source, 0, None)
    if not properties:
        return None
    width_val = properties.get(ImageIO.kCGImagePropertyPixelWidth)
    height_val = properties.get(ImageIO.kCGImagePropertyPixelHeight)
    if width_val is None or height_val is None:
        return None
    try:
        width = int(width_val)
        height = int(height_val)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or width > MAX_IMAGE_PIXELS // height:
        return None
    cg_image = ImageIO.CGImageSourceCreateImageAtIndex(source, 0, None)
    if cg_image is None:
        return None
    raw_orientation = properties.get(ImageIO.kCGImagePropertyOrientation)
    orientation = 1
    if raw_orientation is not None:
        try:
            candidate = int(raw_orientation)
            if 1 <= candidate <= 8:
                orientation = candidate
        except (TypeError, ValueError):
            orientation = 1
    return cg_image, orientation


def _run_vision(argv: list[str], started: float) -> int:
    if sys.platform != "darwin":
        return _fail("PROVIDER_UNAVAILABLE", revision=0, started=started)
    try:
        import objc
        import Vision
    except ImportError:
        return _fail("PROVIDER_UNAVAILABLE", revision=0, started=started)

    with objc.autorelease_pool():
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        revision = int(request.revision())

        supported_raw, supported_err = request.supportedRecognitionLanguagesAndReturnError_(None)
        if supported_err is not None or supported_raw is None:
            return _fail("PROVIDER_UNAVAILABLE", revision=revision, started=started)
        supported = [str(item) for item in supported_raw]

        if argv == ["--languages"]:
            return _emit(
                {"ok": True, "supported_languages": sorted(supported)},
                revision=revision,
                started=started,
                status=0,
            )

        if len(argv) != 2:
            return _fail("INVALID_ARGUMENTS", revision=revision, started=started)

        image_path, raw_languages = argv
        languages = raw_languages.split(",")
        supported_set = set(supported)
        if (
            not languages
            or len(set(languages)) != len(languages)
            or not all(lang in supported_set and lang in ALLOWED_LANGUAGES for lang in languages)
        ):
            return _fail("UNSUPPORTED_LANGUAGE", revision=revision, started=started)

        request.setRecognitionLanguages_(languages)
        loaded = _load_cg_image(image_path)
        if loaded is None:
            return _fail("INVALID_IMAGE", revision=revision, started=started)
        cg_image, orientation = loaded

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_orientation_options_(
            cg_image, orientation, None
        )
        ok, error = handler.performRequests_error_([request], None)
        if not ok or error is not None:
            return _fail("RECOGNITION_FAILED", revision=revision, started=started)

        raw_results = list(request.results() or [])
        # Simple horizontal single-column reading order matching VisionOCR.swift.
        observations = sorted(
            raw_results,
            key=lambda obs: (
                -(float(obs.boundingBox().origin.y) + float(obs.boundingBox().size.height) / 2.0),
                float(obs.boundingBox().origin.x),
            ),
        )
        if len(observations) > MAX_OBSERVATIONS:
            return _fail("OUTPUT_LIMIT", revision=revision, started=started)

        rows: list[dict[str, Any]] = []
        strings: list[str] = []
        confidence_sum = 0.0
        byte_count = 0
        for item in observations:
            candidates = item.topCandidates_(1)
            if not candidates:
                continue
            best = candidates[0]
            line_text = str(best.string())
            if not line_text:
                continue
            byte_count += len(line_text.encode("utf-8")) + 1
            if byte_count > MAX_OUTPUT_BYTES:
                return _fail("OUTPUT_LIMIT", revision=revision, started=started)
            box = item.boundingBox()
            conf = float(best.confidence())
            rows.append(
                {
                    "text": line_text,
                    "confidence": conf,
                    "bbox": [
                        float(box.origin.x),
                        float(box.origin.y),
                        float(box.size.width),
                        float(box.size.height),
                    ],
                }
            )
            strings.append(line_text)
            confidence_sum += conf

        if not rows:
            return _fail("EMPTY_RESULT", revision=revision, started=started)

        text = "\n".join(strings)
        # A Han character is necessary for our explicit Chinese profile, never proof
        # of full language or semantic coverage. English-only inputs use en-US.
        if "zh-Hans" in languages and not _has_han_character(text):
            return _fail("LANGUAGE_MISMATCH", revision=revision, started=started)

        effective_languages = [str(lang) for lang in (request.recognitionLanguages() or [])]
        return _emit(
            {
                "ok": True,
                "text": text,
                "lines": rows,
                "requested_languages": languages,
                "effective_languages": effective_languages,
                "confidence": confidence_sum / float(len(rows)),
            },
            revision=revision,
            started=started,
            status=0,
        )


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    args = sys.argv[1:] if argv is None else list(argv)
    return _run_vision(args, started)


if __name__ == "__main__":
    raise SystemExit(main())
