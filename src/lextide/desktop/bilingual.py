"""Independent local translations tied to an unchanged, verified English SRT.

No synthesis, translation service, job metadata or original artifact is changed.
TXT provides only positional correspondence: callers must show every source and
translation cue for human confirmation because semantic reordering is unknowable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ..errors import StudioError, ValidationError
from ..longform.storage import (
    _open_read,
    atomic_json,
    canonical_key,
    exclusive_lock,
    safe_directory,
)
from .service import parse_srt

MAX_SUBTITLE_BYTES = 16 * 1024 * 1024
MAX_TRANSLATION_BYTES = 16 * 1024 * 1024
MAX_TRANSLATION_TEXT_BYTES = 16 * 1024
_SHA = re.compile(r"[a-f0-9]{64}")
_CUE_KEYS = {"index", "start_ms", "end_ms", "text"}
_RECORD_KEYS = {"index", "start_ms", "end_ms", "source_text_sha256", "text"}
_DOCUMENT_KEYS = {
    "schema_version",
    "language",
    "source_srt_sha256",
    "cue_digest",
    "cues",
    "origin",
}


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _read_bytes(path: Path, limit: int, *, missing_ok: bool = False) -> bytes | None:
    """Use the same regular-file, ancestor and no-follow guards as job storage."""
    try:
        with _open_read(path) as stream:
            if os.fstat(stream.fileno()).st_size > limit:
                raise ValidationError("Subtitle or translation file exceeds its size limit.")
            body = stream.read(limit + 1)
        if len(body) > limit:
            raise ValidationError("Subtitle or translation file exceeds its size limit.")
        return body
    except FileNotFoundError:
        if missing_ok:
            return None
        raise StudioError("STORAGE_IO", "Subtitle or translation file is missing.", 6) from None
    except (OSError, ValueError):
        raise StudioError(
            "STORAGE_IO", "Cannot safely read subtitle or translation storage.", 6
        ) from None


def _utf8(body: bytes) -> str:
    try:
        return body.decode("utf-8-sig")
    except UnicodeError:
        raise ValidationError("Subtitles and translations must use valid UTF-8 text.") from None


def _check_controls(value: str) -> None:
    if any(
        character != "\n" and unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in value
    ):
        raise ValidationError("Translation text contains unsupported control characters.")


def validate_translation_text(value: object) -> str:
    """Validate text without claiming its language or altering its punctuation."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Each translated cue must contain nonempty text.")
    _check_controls(value)
    try:
        if len(value.encode("utf-8")) > MAX_TRANSLATION_TEXT_BYTES:
            raise ValidationError("A translated cue exceeds its text size limit.")
    except UnicodeError:
        raise ValidationError("Translation text contains invalid Unicode.") from None
    return value


def _origin(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Translation origin must be nonempty text.")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValidationError("Translation origin contains unsupported characters.")
    if len(value.encode("utf-8")) > 128:
        raise ValidationError("Translation origin exceeds its size limit.")
    return value


def _cue_metadata(cue: Mapping) -> dict:
    return {
        "index": cue["index"],
        "start_ms": cue["start_ms"],
        "end_ms": cue["end_ms"],
        "source_text_sha256": _sha(cue["text"].encode("utf-8")),
    }


def _validated_cues(cues: object) -> tuple[Mapping, ...]:
    if not isinstance(cues, (list, tuple)) or not cues:
        raise ValidationError("A verified, nonempty subtitle cue list is required.")
    result = []
    previous_end = 0
    for index, cue in enumerate(cues, 1):
        if (
            not isinstance(cue, Mapping)
            or set(cue) != _CUE_KEYS
            or any(type(cue[key]) is not int for key in ("index", "start_ms", "end_ms"))
            or cue["index"] != index
            or cue["start_ms"] < previous_end
            or cue["end_ms"] <= cue["start_ms"]
            or not isinstance(cue["text"], str)
            or not cue["text"].strip()
        ):
            raise ValidationError("Verified subtitle cue identity or timing is invalid.")
        try:
            cue["text"].encode("utf-8")
        except UnicodeError:
            raise ValidationError("Verified subtitle text contains invalid Unicode.") from None
        result.append(MappingProxyType(dict(cue)))
        previous_end = cue["end_ms"]
    return tuple(result)


@dataclass(frozen=True)
class SubtitleSource:
    """Immutable cue snapshot; its fingerprint includes exact original SRT bytes."""

    cues: tuple[Mapping, ...]
    srt_sha256: str
    cue_digest: str
    path: Path

    def __post_init__(self):
        cues = _validated_cues(self.cues)
        if (
            not isinstance(self.srt_sha256, str)
            or not _SHA.fullmatch(self.srt_sha256)
            or self.cue_digest != canonical_key({"cues": [_cue_metadata(cue) for cue in cues]})
        ):
            raise ValidationError("Subtitle source fingerprints are invalid.")
        object.__setattr__(self, "cues", cues)
        object.__setattr__(self, "path", Path(self.path).expanduser().absolute())

    @classmethod
    def from_verified(cls, verified: dict) -> SubtitleSource:
        if (
            not isinstance(verified, dict)
            or not isinstance(verified.get("srt_path"), (str, Path))
            or not str(verified["srt_path"])
        ):
            raise ValidationError("A verified English subtitle path is required.")
        path = Path(verified["srt_path"]).expanduser().absolute()
        expected = _validated_cues(verified.get("cues"))
        body = _read_bytes(path, MAX_SUBTITLE_BYTES)
        parsed = parse_srt(_utf8(body))
        if tuple(parsed) != expected:
            raise ValidationError("English SRT no longer matches the verified cue list.")
        return cls(
            cues=expected,
            srt_sha256=_sha(body),
            cue_digest=canonical_key({"cues": [_cue_metadata(cue) for cue in expected]}),
            path=path,
        )

    def assert_current(self) -> None:
        body = _read_bytes(self.path, MAX_SUBTITLE_BYTES)
        if _sha(body) != self.srt_sha256:
            raise StudioError(
                "SUBTITLE_SOURCE_CHANGED", "English subtitles changed; verify the output again.", 5
            )


@dataclass(frozen=True)
class TranslationDocument:
    texts: tuple[str | None, ...]
    revision: str


def _json_object(body: bytes) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    def invalid_constant(_):
        raise ValueError("non-finite number")

    try:
        value = json.loads(
            body.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant
        )
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ValidationError("Saved translation JSON is invalid.") from None


def _texts(texts: object, source: SubtitleSource) -> tuple[str | None, ...]:
    if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence):
        raise ValidationError("Translations must be a sequence of cue text records.")
    if len(texts) != len(source.cues):
        raise ValidationError("Translation cue count must match the English subtitles exactly.")
    translated = []
    total_bytes = 0
    for text in texts:
        if text is not None:
            text = validate_translation_text(text)
            total_bytes += len(text.encode("utf-8"))
            if total_bytes > MAX_TRANSLATION_BYTES:
                raise ValidationError("Translations exceed their total text size limit.")
        translated.append(text)
    return tuple(translated)


class TranslationStore:
    """Sidecar-only storage with optimistic revisions and locked atomic writes."""

    def __init__(self, root: Path):
        self.root = safe_directory(root)

    def _path(self, source: SubtitleSource) -> Path:
        if not isinstance(source, SubtitleSource):
            raise ValidationError("A verified subtitle source is required.")
        return self.root / "translations" / f"{source.srt_sha256}.zh-CN.json"

    def current_revision(self, source: SubtitleSource) -> str | None:
        """Read the actual bounded file hash, including corrupt JSON, for repair confirmation."""
        path = self._path(source)
        source.assert_current()
        body = _read_bytes(path, MAX_TRANSLATION_BYTES, missing_ok=True)
        return None if body is None else _sha(body)

    def load(self, source: SubtitleSource) -> TranslationDocument | None:
        path = self._path(source)
        source.assert_current()
        body = _read_bytes(path, MAX_TRANSLATION_BYTES, missing_ok=True)
        if body is None:
            return None
        value = _json_object(body)
        if (
            set(value) != _DOCUMENT_KEYS
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["language"] != "zh-CN"
            or value["source_srt_sha256"] != source.srt_sha256
            or value["cue_digest"] != source.cue_digest
            or not isinstance(value["cues"], list)
            or len(value["cues"]) != len(source.cues)
        ):
            raise ValidationError(
                "Saved translation identity does not match the English subtitles."
            )
        _origin(value["origin"])
        translated = []
        for record, cue in zip(value["cues"], source.cues, strict=True):
            if (
                not isinstance(record, dict)
                or set(record) != _RECORD_KEYS
                or any(type(record[key]) is not int for key in ("index", "start_ms", "end_ms"))
                or {key: record[key] for key in _RECORD_KEYS - {"text"}} != _cue_metadata(cue)
            ):
                raise ValidationError(
                    "Saved translation cue order, text identity or timing is invalid."
                )
            translated.append(record["text"])
        texts = _texts(translated, source)
        source.assert_current()
        return TranslationDocument(texts=texts, revision=_sha(body))

    def save(
        self,
        source: SubtitleSource,
        texts: Sequence[str | None],
        *,
        expected_revision: str | None,
        origin: str,
    ) -> TranslationDocument:
        path = self._path(source)
        translated = _texts(texts, source)
        if expected_revision is not None and (
            not isinstance(expected_revision, str) or not _SHA.fullmatch(expected_revision)
        ):
            raise ValidationError("Translation revision must be a SHA256 fingerprint or None.")
        value = {
            "schema_version": 1,
            "language": "zh-CN",
            "source_srt_sha256": source.srt_sha256,
            "cue_digest": source.cue_digest,
            "origin": _origin(origin),
            "cues": [
                _cue_metadata(cue) | {"text": text}
                for cue, text in zip(source.cues, translated, strict=True)
            ],
        }
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) + 1 > MAX_TRANSLATION_BYTES:
            raise ValidationError("Saved translation exceeds its size limit.")
        # Check before creating any sidecar directories, and again just before publication.
        source.assert_current()
        with exclusive_lock(path.parent / ".locks" / f"{source.srt_sha256}.lock"):
            actual_revision = self.current_revision(source)
            if actual_revision != expected_revision:
                raise StudioError(
                    "TRANSLATION_CONFLICT", "Translations changed; reopen the editor or preview.", 5
                )
            source.assert_current()
            atomic_json(path, value)
            body = _read_bytes(path, MAX_TRANSLATION_BYTES)
        return TranslationDocument(texts=translated, revision=_sha(body))


def import_translation(path: Path, source: SubtitleSource) -> tuple[str, ...]:
    """Strict SRT identity or one nonblank physical TXT line per original cue.

    Matching TXT line counts cannot establish semantic correspondence. The UI must
    present the complete bilingual preview and request human order confirmation.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in {".srt", ".txt"}:
        raise ValidationError("Import a local UTF-8 .srt or .txt translation file.")
    source.assert_current()
    text = _utf8(_read_bytes(path, MAX_SUBTITLE_BYTES))
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    # parse_srt uses splitlines(); reject controls before they could become newlines.
    _check_controls(normalized)
    if suffix == ".srt":
        cues = parse_srt(normalized)
        if len(cues) != len(source.cues) or any(
            any(imported[key] != original[key] for key in ("index", "start_ms", "end_ms"))
            for imported, original in zip(cues, source.cues)
        ):
            raise ValidationError("Imported SRT cue count, numbering and times must match exactly.")
        translated = tuple(validate_translation_text(cue["text"]) for cue in cues)
    else:
        # Remove exactly one normal terminal newline; do not strip blank records.
        lines = normalized.removesuffix("\n").split("\n")
        translated = tuple(validate_translation_text(line) for line in lines)
        if len(translated) != len(source.cues):
            raise ValidationError(
                "TXT must have exactly one nonblank physical line per English cue."
            )
    source.assert_current()
    return translated
