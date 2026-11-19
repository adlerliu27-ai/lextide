"""Bounded local image recognition in the desktop worker. No network clients."""

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from ..errors import StudioError
from ..longform.document import MAX_DOCUMENT_BYTES
from ..runtime import external_runtime_environment, is_frozen
from .importing import DocumentImportError, _read_source

ERRORS = {
    "OCR_INVALID_IMAGE": (
        "Choose a readable, single PNG or JPEG image (up to 32 MiB and 24 megapixels)."
    ),
    "OCR_UNAVAILABLE": (
        "The local OCR tool is unavailable. Check the installation or choose another provider."
    ),
    "OCR_LANGUAGE_UNAVAILABLE": (
        "This OCR installation lacks a required language. Check local language data."
    ),
    "OCR_TIMEOUT": "Local recognition timed out. Try Tesseract or a smaller image.",
    "OCR_EMPTY": "No text was recognized. Try a clearer image.",
    "OCR_LANGUAGE_MISMATCH": (
        "The expected language was missing from the result. Check the language and image."
    ),
    "OCR_OUTPUT_LIMIT": "The recognition result exceeds the text limit. Try a smaller section.",
    "OCR_FAILED": "Local recognition failed. Check the image and the selected provider.",
}


def _error(code: str) -> StudioError:
    return StudioError(code, ERRORS[code], 5)


def _languages(provider: str, language: str) -> list[str]:
    if provider == "tesseract":
        return ["eng"] if language == "en" else ["chi_sim", "eng"]
    return {"en": ["en-US"], "zh": ["zh-Hans"], "mixed": ["zh-Hans", "en-US"]}[language]


def checked_metadata(value: dict) -> dict:
    """Bound the local provenance saved beside a task, separately from its audio settings."""
    if not isinstance(value, dict):
        raise _error("OCR_FAILED")
    provider, language = value.get("provider"), value.get("language")
    if provider not in ("tesseract", "apple-vision") or language not in ("en", "zh", "mixed"):
        raise _error("OCR_FAILED")
    langs = _languages(provider, language)
    duration, confidence = value.get("duration_ms"), value.get("confidence")
    if (
        value.get("requested_languages") != langs
        or value.get("effective_languages") != langs
        or type(duration) is not int
        or not 0 <= duration <= 2**31
        or (
            confidence is not None
            and (type(confidence) not in (int, float) or not 0 <= confidence <= 1)
        )
    ):
        raise _error("OCR_FAILED")
    return {
        "provider": provider,
        "language": language,
        "requested_languages": langs,
        "effective_languages": langs,
        "duration_ms": duration,
        "confidence": confidence,
    }


def _tool(provider: str) -> str:
    if provider == "apple-vision":
        candidates = (
            [Path(sys.executable).parent.parent / "Resources/ocr/lextide-vision-ocr"]
            if is_frozen()
            else [os.environ.get("LEXTIDE_VISION_HELPER")]
        )
    else:
        configured = os.environ.get("LEXTIDE_TESSERACT")
        candidates = (
            [configured]
            if configured
            else [
                shutil.which("tesseract"),
                "/opt/homebrew/bin/tesseract",
                "/usr/local/bin/tesseract",
            ]
        )
    for candidate in candidates:
        if (
            candidate
            and Path(candidate).is_absolute()
            and Path(candidate).is_file()
            and os.access(candidate, os.X_OK)
        ):
            return str(candidate)
    raise _error("OCR_UNAVAILABLE")


async def _run(args: list[str], timeout: float, limit: int) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=external_runtime_environment(),
        )
    except OSError:
        raise _error("OCR_UNAVAILABLE") from None
    try:
        output = bytearray()
        async with asyncio.timeout(timeout):
            while block := await proc.stdout.read(65536):
                output.extend(block)
                if len(output) > limit:
                    raise _error("OCR_OUTPUT_LIMIT")
            status = await proc.wait()
        return status, output.decode("utf-8")
    except TimeoutError:
        raise _error("OCR_TIMEOUT") from None
    except UnicodeError:
        raise _error("OCR_FAILED") from None
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.communicate()


def _image_bytes(path: Path) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImageReader

    suffix = path.suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg"):
        raise _error("OCR_INVALID_IMAGE")
    try:
        data = _read_source(path)
    except DocumentImportError:
        raise _error("OCR_INVALID_IMAGE") from None
    buf = QBuffer()
    buf.setData(data)
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    size = reader.size()
    if (
        bytes(reader.format()) != (b"png" if suffix == ".png" else b"jpeg")
        or not size.isValid()
        or not 0 < size.width() * size.height() <= 24_000_000
        or reader.imageCount() != 1
        or reader.read().isNull()
    ):
        raise _error("OCR_INVALID_IMAGE")
    return data


async def _tesseract(path: Path, language: str) -> tuple[str, list[str], None]:
    binary = _tool("tesseract")
    langs = _languages("tesseract", language)
    status, inventory = await _run([binary, "--list-langs"], 3, 65536)
    if status or not set(langs).issubset(inventory.splitlines()):
        raise _error("OCR_LANGUAGE_UNAVAILABLE")
    status, text = await _run(
        [
            binary,
            str(path),
            "stdout",
            "-l",
            "+".join(langs),
            "--oem",
            "1",
            "--psm",
            "6",
            "-c",
            "textord_noise_rejrows=0",
        ],
        25,
        MAX_DOCUMENT_BYTES,
    )
    if status:
        raise _error("OCR_FAILED")
    return text, langs, None


async def _vision(path: Path, language: str) -> tuple[str, list[str], float | None]:
    binary = _tool("apple-vision")
    langs = _languages("apple-vision", language)
    status, raw = await _run([binary, "--languages"], 3, 65536)
    try:
        inventory = json.loads(raw)
        if status or not set(langs).issubset(inventory["supported_languages"]):
            raise _error("OCR_LANGUAGE_UNAVAILABLE")
        status, raw = await _run([binary, str(path), ",".join(langs)], 8, 4 * MAX_DOCUMENT_BYTES)
        result = json.loads(raw)
        if status or result.get("ok") is not True:
            code = {
                "EMPTY_RESULT": "OCR_EMPTY",
                "LANGUAGE_MISMATCH": "OCR_LANGUAGE_MISMATCH",
                "UNSUPPORTED_LANGUAGE": "OCR_LANGUAGE_UNAVAILABLE",
                "OUTPUT_LIMIT": "OCR_OUTPUT_LIMIT",
            }
            raise _error(code.get(result.get("error_code"), "OCR_FAILED"))
        if result["effective_languages"] != langs or result["provider"] != "apple-vision":
            raise _error("OCR_LANGUAGE_MISMATCH")
        if not isinstance(result["text"], str):
            raise ValueError
        return result["text"], langs, result.get("confidence")
    except (ValueError, KeyError, TypeError, AttributeError):
        raise _error("OCR_FAILED") from None


async def recognize(
    path: Path, *, provider: str, language: str, allow_partial: bool = False
) -> dict:
    """Keep usable mismatched text only for callers that enforce explicit local review.

    A provider failure, cancellation or timeout never supplies a partial result.
    Matching the requested scripts is necessary, not proof of transcription accuracy.
    """
    if provider not in ("tesseract", "apple-vision") or language not in ("zh", "en", "mixed"):
        raise _error("OCR_FAILED")
    started = time.monotonic()
    data = _image_bytes(path)
    # One bounded snapshot for recognition and the source hash. A changed original
    # path cannot substitute another image between preflight and recognition.
    with tempfile.TemporaryDirectory(prefix="lextide-ocr-") as scratch:
        local = Path(scratch) / ("input" + path.suffix.lower())
        with open(local, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
            stream.write(data)
        adapter = _tesseract if provider == "tesseract" else _vision
        text, langs, confidence = await adapter(local, language)
    if not text.strip():
        raise _error("OCR_EMPTY")
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise _error("OCR_OUTPUT_LIMIT")
    mismatch = (language in ("zh", "mixed") and not re.search(r"[\u3400-\u9fff]", text)) or (
        language in ("en", "mixed") and not re.search(r"[a-zA-Z]", text)
    )
    if mismatch and not allow_partial:
        raise _error("OCR_LANGUAGE_MISMATCH")
    result = {
        "text": text,
        "source_name": path.name,
        "source_path": str(path.absolute()),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "encoding": "utf-8",
        "format": path.suffix.lower()[1:],
        "changes": [],
        "warnings": [],
        "ocr": {
            "provider": provider,
            "language": language,
            "requested_languages": langs,
            "effective_languages": langs,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "confidence": confidence,
        },
    }
    if mismatch:
        result.update(review_required=True, review_reason="OCR_LANGUAGE_MISMATCH")
    return result
