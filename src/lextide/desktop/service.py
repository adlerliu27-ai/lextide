"""Qt-free desktop application boundary over the accepted G1–G3 APIs.

Every request runs in a disposable local worker. No engine is downloaded and
no online request is made by discovery, preview, history or verification.
The separate Auto probe checks endpoint reachability without document text.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import stat
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from ..audio import _executable
from ..config import Settings
from ..engines.edge import EdgeEngine
from ..engines.kokoro import KokoroEngine
from ..errors import InputError, StudioError, ValidationError
from ..jobs import verify_job
from ..longform.document import MAX_DOCUMENT_BYTES, chunk_document, prepare_document
from ..longform.media import LongEngine
from ..longform.storage import atomic_json, read_json, safe_directory
from ..longform.tasks import cancel_task, create_task, default_cache_dir, run_task, task_status
from ..models import default_model_dir, model_status
from ..reading import validate_reading_text
from ..text import PreparedText

EventSink = Callable[[dict], None]
MAX_HISTORY_ITEMS = 300
MAX_HISTORY_ENTRIES = 6000
MAX_HISTORY_DEPTH = 3
MAX_ROOTS = 20
_TIME = r"(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})"
_CUE_TIME = re.compile(rf"^{_TIME} --> {_TIME}$")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path(value: object) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value:
        raise InputError("A valid local path is required.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise InputError("Text must be a string.")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise InputError("Text contains invalid Unicode.") from None
    if size > MAX_DOCUMENT_BYTES:
        raise InputError("Extracted or reviewed text exceeds the G3 1 MiB limit.")
    return value


def _runtime(request: dict) -> dict:
    value = request.get("runtime", {})
    if not isinstance(value, dict):
        raise InputError("Runtime settings must be an object.")
    result = {}
    for key in ("model_dir", "offline_python", "ffmpeg", "ffprobe"):
        item = value.get(key)
        if item not in (None, ""):
            result[key] = _path(item) if key == "model_dir" else str(_path(item))
    return result


def _settings(request: dict) -> Settings:
    supplied = request.get("settings", {})
    if not isinstance(supplied, dict) or set(supplied) - {"engine", "voice", "rate", "timeout"}:
        raise InputError("Unsupported synthesis settings.")
    engine = supplied.get("engine", "edge-tts")
    defaults = {"engine": engine, "voice": "af_heart" if engine == "kokoro" else "en-US-GuyNeural"}
    settings = Settings(**(defaults | supplied))
    try:
        settings.validate()
    except (TypeError, ValueError):
        raise InputError("Invalid synthesis settings.") from None
    return settings


def _export_options(request: dict) -> tuple[str, int]:
    audio_filter, pause_ms = request.get("audio_filter", "on"), request.get("pause_ms", 0)
    if audio_filter not in {"on", "off"} or type(pause_ms) is not int or not 0 <= pause_ms <= 2000:
        raise InputError("Audio filter must be on/off and pause must be 0–2000 ms.")
    return audio_filter, pause_ms


def preview(request: dict) -> dict:
    settings = _settings(request)
    audio_filter, pause_ms = _export_options(request)
    prepared = prepare_document(text=_text(request.get("text")))
    plan = chunk_document(prepared, settings.engine, max_bytes=request.get("chunk_bytes"))
    return {
        "text": prepared.tts_text,
        "sha256": _sha(prepared.tts_text),
        "changes": prepared.changes,
        "character_count": len(prepared.tts_text),
        "byte_count": len(prepared.tts_text.encode("utf-8")),
        "sentence_count": len(plan["segments"]),
        "chunk_count": len(plan["chunks"]),
        "chunk_bytes": plan["max_bytes"],
        "settings": asdict(settings),
        "audio_filter": audio_filter,
        "pause_ms": pause_ms,
    }


def _authorize(request: dict, text: str, engine: str) -> bool:
    if request.get("approved_sha256") != _sha(text):
        raise StudioError(
            "TEXT_REVIEW_REQUIRED", "Review the current text again before starting or resuming.", 3
        )
    allowed = request.get("allow_online") is True
    if engine == "edge-tts" and not allowed:
        raise StudioError(
            "ONLINE_NOT_AUTHORIZED", "This run requires explicit permission to send text online.", 3
        )
    return allowed if engine == "edge-tts" else False


def _read_text(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DOCUMENT_BYTES:
            raise ValidationError("Task text is not a bounded regular file.")
        data = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValidationError("Task text exceeds 1 MiB.")
    return data.decode("utf-8")


def _absolute_export(task: Path, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("Invalid task export path.")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValidationError("Task export must stay inside its task directory.")
    target = safe_directory(task / relative)
    if not target.is_relative_to(task):
        raise ValidationError("Task export escaped its task directory.")
    return str(target)


def _brief_status(task: Path, status: dict) -> dict:
    return {
        "task_path": str(task),
        **{
            name: status.get(name)
            for name in (
                "job_id",
                "status",
                "completed_chunks",
                "total_chunks",
                "run_count",
                "last_error",
            )
        },
        "export_path": _absolute_export(task, status.get("export_path")),
    }


def status(request: dict) -> dict:
    task = safe_directory(_path(request.get("task_path")))
    current = task_status(task)  # G3 validates saved inputs/config before review or resume.
    manifest = read_json(task / "task.json")
    text = _read_text(task / "tts.txt")
    return {
        **_brief_status(task, current),
        "review_text": text,
        "sha256": _sha(text),
        "settings": manifest["config"]["settings"],
        "audio_filter": manifest["config"]["audio_filter"],
        "pause_ms": manifest["config"]["pause_ms"],
        "chunk_bytes": manifest["config"]["chunk_bytes"],
    }


def _review_trace(before: str, after: str) -> list[dict]:
    """One exact, linear-time replacement; avoids quadratic diff on a 1 MiB file."""
    if before == after:
        return []
    left = 0
    limit = min(len(before), len(after))
    while left < limit and before[left] == after[left]:
        left += 1
    right = 0
    while right < limit - left and before[-1 - right] == after[-1 - right]:
        right += 1
    old_end, new_end = len(before) - right, len(after) - right
    return [
        {
            "kind": "user_review_edit",
            "original_span": [left, old_end],
            "new_span": [left, new_end],
            "before": before[left:old_end],
            "after": after[left:new_end],
        }
    ]


def _provenance(request: dict, reviewed: str) -> tuple[str, dict]:
    source = request.get("provenance") or {}
    if not isinstance(source, dict):
        raise InputError("Import provenance must be an object.")
    original = _text(source.get("text", source.get("original_text", reviewed)))
    metadata = {}
    for key in ("source_path", "source_name", "source_sha256", "format", "encoding"):
        value = source.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 4096):
            raise InputError("Invalid import provenance field.")
        metadata[key] = value
    for key in ("changes", "warnings"):
        values = source.get(key, [])
        if (
            not isinstance(values, list)
            or len(values) > 1000
            or any(not isinstance(value, str) or len(value) > 4096 for value in values)
        ):
            raise InputError("Invalid import provenance notes.")
        metadata[key] = values
    title = source.get("title")
    if title is not None and not isinstance(title, str):
        raise InputError("Document title must be text.")
    title = (title.strip() if title else "") or metadata.get("source_name") or "Pasted text"
    record = {
        "schema_version": 1,
        "kind": "desktop-document-review",
        "title": title[:256],
        "source": metadata,
        "extracted_file": "source-extracted.txt",
        "extracted_sha256": _sha(original),
        "reviewed_file": "original.txt",
        "reviewed_sha256": _sha(reviewed),
        "review_edits": _review_trace(original, reviewed),
        "approved_tts_sha256": request.get("approved_sha256"),
    }
    cleaning = source.get("cleaning")
    if cleaning is not None:
        if not isinstance(cleaning, dict) or cleaning.get("mode") not in {"original", "english"}:
            raise InputError("Invalid text preparation record.")
        strength = cleaning.get("cleaning_mode", "conservative")
        versions = {
            "conservative": "english-rules-v1",
            "remove_chinese_translations": "english-translations-v1",
        }
        if not isinstance(strength, str) or strength not in versions:
            raise InputError("Invalid text preparation record.")
        if cleaning["mode"] == "original" and strength != "conservative":
            raise InputError("Invalid text preparation record.")
        expected_version = versions[strength] if cleaning["mode"] == "english" else "original-v1"
        cleaned = _text(cleaning.get("cleaned_text"))
        if cleaning.get("version") != expected_version or cleaning.get("original_sha256") != _sha(
            original
        ):
            raise InputError("Text preparation provenance does not match the original text.")
        # Save the preparation snapshot, not a new pass of the cleaner after consent.
        record["text_preparation"] = {
            "version": expected_version,
            "mode": cleaning["mode"],
            "original_sha256": _sha(original),
            "cleaned_text": cleaned,
            "cleaned_sha256": _sha(cleaned),
            "reviewed_sha256": _sha(reviewed),
        }
        if "cleaning_mode" in cleaning:
            record["text_preparation"]["cleaning_mode"] = strength
    return original, record


async def _run(
    task: Path,
    request: dict,
    allowed: bool,
    emit: EventSink,
    *,
    expected_input: PreparedText,
    expected_options: dict,
) -> dict:
    last = None

    def progress(current: dict) -> None:
        nonlocal last
        data = _brief_status(task, current)
        signature = (data["status"], data["completed_chunks"], data["total_chunks"])
        if signature != last:
            emit({"event": "progress", **data})
            last = signature

    async def poll() -> None:
        while True:
            try:
                progress(task_status(task))
            except (StudioError, OSError):
                pass  # The authoritative runner reports errors, never the progress observer.
            await asyncio.sleep(0.5)

    observer = asyncio.create_task(poll())
    try:
        await run_task(
            task,
            allow_online=allowed,
            progress=progress,
            expected_input=expected_input,
            expected_options=expected_options,
            **_runtime(request),
        )
    except asyncio.CancelledError:
        # G3 has already atomically checkpointed paused before propagating cancellation.
        current = task_status(task)
        progress(current)
        return _brief_status(task, current)
    finally:
        observer.cancel()
        try:
            await observer
        except asyncio.CancelledError:
            pass
    current = task_status(task)
    progress(current)
    return _brief_status(task, current)


async def start(request: dict, emit: EventSink) -> dict:
    settings = _settings(request)
    audio_filter, pause_ms = _export_options(request)
    reviewed = _text(request.get("text"))
    prepared = prepare_document(text=reviewed)
    # A caller must review the exact normalized text, not only supply its hash.
    if reviewed != prepared.tts_text:
        raise StudioError(
            "TEXT_REVIEW_REQUIRED", "Review the current text again before starting or resuming.", 3
        )
    allowed = _authorize(request, prepared.tts_text, settings.engine)
    original, provenance = _provenance(request, reviewed)
    english_only = request.get("english_only", False)
    if type(english_only) is not bool:
        raise InputError("English Only must be a boolean.")
    validate_reading_text(
        prepared.tts_text,
        settings,
        english_only=english_only
        or provenance.get("text_preparation", {}).get("mode") == "english",
    )
    runtime = _runtime(request)
    engine = LongEngine(settings, **runtime)
    task = create_task(
        prepared,
        settings,
        _path(request.get("out_dir")),
        _path(request["cache_dir"]) if request.get("cache_dir") else default_cache_dir(),
        engine=engine,
        chunk_bytes=request.get("chunk_bytes"),
        audio_filter=audio_filter,
        pause_ms=pause_ms,
    )
    descriptor = os.open(task / "source-extracted.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_json(task / "document-review.json", provenance)
    emit({"event": "task_created", "task_path": str(task)})
    return await _run(
        task,
        request,
        allowed,
        emit,
        expected_input=prepared,
        expected_options={**asdict(settings), "audio_filter": audio_filter, "pause_ms": pause_ms},
    )


async def resume(request: dict, emit: EventSink) -> dict:
    current = status(request)
    if current["status"] == "completed":
        raise InputError("This task is already completed; open its verified output.")
    # No new synthesis configuration is accepted: G3 resumes its immutable saved config.
    for name in ("settings", "audio_filter", "pause_ms", "chunk_bytes", "text"):
        if name in request:
            raise InputError(
                "Resume uses the saved task settings. Create a new task to change them."
            )
    allowed = _authorize(request, current["review_text"], current["settings"]["engine"])
    validate_reading_text(current["review_text"], Settings(**current["settings"]))
    task = _path(current["task_path"])
    expected_input = prepare_document(text=_read_text(task / "original.txt"))
    if expected_input.tts_text != current["review_text"]:
        raise ValidationError("Task source and reviewed text no longer agree.")
    return await _run(
        task,
        request,
        allowed,
        emit,
        expected_input=expected_input,
        expected_options={
            **current["settings"],
            **{key: current[key] for key in ("audio_filter", "pause_ms", "chunk_bytes")},
        },
    )


def _history_item(path: Path, manifest: dict, *, task: bool) -> dict | None:
    schema = manifest.get("schema_version")
    if schema not in (1, 2, 3) or (task and manifest.get("kind") != "longform-task"):
        return None
    if (task and schema != 3) or (not task and "status" not in manifest):
        return None
    config = manifest.get("config", {})
    engine = config.get("settings", {}).get("engine") if isinstance(config, dict) else None
    record = manifest.get("engine", {})
    if engine is None and isinstance(record, dict):
        engine = record.get("id", record.get("engine_id"))
    audio = manifest.get("audio", {})
    duration = audio.get("duration_ms") if isinstance(audio, dict) else None
    export_path = _absolute_export(path, manifest.get("export_path")) if task else str(path)
    if duration is None and export_path:
        try:
            validation = read_json(Path(export_path) / "validation.json")
            duration = validation.get("audio", {}).get("duration_ms")
        except (StudioError, OSError, AttributeError):
            pass
    title = path.name
    source_path = None
    if task and (path / "document-review.json").is_file():
        try:
            review = read_json(path / "document-review.json")
            source = review.get("source", {})
            title = review.get("title") or source.get("source_name") or title
            source_path = source.get("source_path")
        except (StudioError, OSError, AttributeError):
            pass
    state = manifest.get("status", "unknown")
    if state not in {
        "created",
        "preparing",
        "chunking",
        "synthesizing",
        "assembling",
        "verifying",
        "completed",
        "failed",
        "paused",
        "cancelled",
    }:
        state = "unknown"
    chunks = manifest.get("chunks") if task else None
    known_chunks = (
        isinstance(chunks, list)
        and 1 <= len(chunks) <= 2000
        and all(isinstance(chunk, dict) for chunk in chunks)
    )
    return {
        "path": str(path),
        "kind": "task" if task else "job",
        "title": str(title)[:256],
        "source_path": source_path if isinstance(source_path, str) else None,
        "schema_version": schema,
        "created_at": manifest.get("created_at"),
        "engine": engine,
        "status": state,
        "completed_chunks": sum(chunk.get("status") == "completed" for chunk in chunks)
        if known_chunks
        else None,
        "total_chunks": len(chunks) if known_chunks else None,
        "duration_seconds": duration / 1000 if isinstance(duration, (int, float)) else None,
        "export_path": export_path,
        "verified": False,
    }


def history(request: dict) -> dict:
    roots, explicit = request.get("roots", []), request.get("paths", [])
    if (
        not isinstance(roots, list)
        or not isinstance(explicit, list)
        or len(roots + explicit) > MAX_ROOTS
    ):
        raise InputError("Choose at most 20 local history roots or explicit tasks.")
    queue = [(_path(value), 0, value in explicit) for value in roots + explicit]
    items, warnings, seen = [], [], set()
    scanned, truncated = 0, False
    while queue and len(items) < MAX_HISTORY_ITEMS and scanned < MAX_HISTORY_ENTRIES:
        path, depth, single = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        scanned += 1
        try:
            path = safe_directory(path)
            if not path.is_dir():
                continue
            is_task = (path / "task.json").is_file()
            manifest_path = path / ("task.json" if is_task else "manifest.json")
            if manifest_path.is_file():
                manifest = read_json(manifest_path)
                if isinstance(manifest, dict):
                    item = _history_item(path, manifest, task=is_task)
                    if item:
                        items.append(item)
                        # A task row opens its export; don't duplicate its exports in history.
                        continue
            if single or depth >= MAX_HISTORY_DEPTH:
                continue
            with os.scandir(path) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned >= MAX_HISTORY_ENTRIES:
                        truncated = True
                        break
                    if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False):
                        queue.append((Path(entry.path), depth + 1, False))
        except (StudioError, OSError, ValueError, TypeError, AttributeError):
            warnings.append("A history directory or manifest could not be read safely.")
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "items": items,
        "truncated": truncated or bool(queue),
        "warnings": sorted(set(warnings)),
    }


def parse_srt(text: str) -> list[dict]:
    """Read exact integer millisecond timestamps, preserving real inter-cue gaps."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > 16 * 1024 * 1024:
        raise ValidationError("Subtitles exceed the supported size.")
    if "\x00" in text:
        raise ValidationError("Subtitles contain invalid text.")
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n", normalized.strip())
    cues = []
    previous_end = 0
    for index, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(index):
            raise ValidationError("Subtitles have invalid cue numbering or text.")
        match = _CUE_TIME.fullmatch(lines[1])
        if not match:
            raise ValidationError("Subtitles have an invalid timestamp.")
        values = [int(value) for value in match.groups()]
        start, end = (
            ((values[i] * 60 + values[i + 1]) * 60 + values[i + 2]) * 1000 + values[i + 3]
            for i in (0, 4)
        )
        caption = "\n".join(lines[2:])
        if start < previous_end or end <= start or not caption.strip():
            raise ValidationError("Subtitles overlap, are out of order or contain an empty cue.")
        cues.append({"index": index, "start_ms": start, "end_ms": end, "text": caption})
        previous_end = end
    if not cues:
        raise ValidationError("Subtitles are empty.")
    return cues


def verify(request: dict) -> dict:
    path = safe_directory(_path(request.get("path")))
    job = path
    if (path / "task.json").is_file():
        current = task_status(path)
        if current["status"] != "completed" or not current.get("export_path"):
            raise ValidationError("This task has no completed output to play.")
        job = Path(_absolute_export(path, current["export_path"]))
    runtime = _runtime(request)
    report = verify_job(
        job, **{key: value for key, value in runtime.items() if key in {"ffmpeg", "ffprobe"}}
    )
    if report.get("status") != "valid" or report.get("hashes_match") is not True:
        raise ValidationError("Output verification did not confirm complete valid artifacts.")
    srt_path = job / "subtitles.srt"
    # Core verification has already checked the regular-file and artifact hash constraints.
    if srt_path.stat().st_size > 16 * 1024 * 1024:
        raise ValidationError("Subtitles exceed the supported size.")
    cues = parse_srt(srt_path.read_text(encoding="utf-8"))
    duration = report["audio"]["duration_ms"]
    if cues[-1]["end_ms"] > duration:
        raise ValidationError("Subtitles exceed the actual audio duration.")
    manifest = read_json(job / "manifest.json")
    settings = manifest.get("settings", manifest.get("config", {}).get("settings", {}))
    if not isinstance(settings, dict):
        settings = {}
    engine_record = manifest.get("engine", {})
    engine_id = (
        engine_record.get("engine_id", engine_record.get("id"))
        if isinstance(engine_record, dict)
        else None
    )
    return {
        "path": str(path),
        "job_path": str(job),
        "audio_path": str(job / "audio.mp3"),
        "srt_path": str(srt_path),
        "duration_seconds": duration / 1000,
        "engine": engine_id,
        "voice": settings.get("voice"),
        "rate": settings.get("rate"),
        "cues": cues,
        "report": report,
    }


def engines(request: dict) -> dict:
    runtime = _runtime(request)
    media = {}
    for name in ("ffmpeg", "ffprobe"):
        try:
            media[name] = {"available": True, "path": _executable(name, runtime.get(name))}
        except StudioError as exc:
            media[name] = {"available": False, "path": None, "error": exc.code}
    media_ready = all(tool["available"] for tool in media.values())
    kokoro = KokoroEngine(**runtime)
    capability = asdict(kokoro.capabilities())
    runtime_ready = "runtime_metadata_ready=true" in capability.get("notes", ())
    model = model_status(runtime.get("model_dir") or default_model_dir(), verify=True)
    # The desktop just performed full hashing; don't advertise size-only readiness
    # when that stronger check has already found a corrupt local model.
    capability["available"] = capability["available"] and model.get("ready") is True and media_ready
    edge = asdict(EdgeEngine().capabilities())
    edge["available"] = edge["available"] and media_ready
    return {
        "engines": [edge, capability],
        "model": model,
        "offline_runtime": {"ready": runtime_ready},
        "media_tools": media,
    }


async def auto_probe(request: dict) -> dict:
    """Return verified local readiness and a separate, text-free Auto decision."""
    from .engine_policy import probe_edge_reachability, resolve_engine

    if set(request) - {"command", "runtime"}:
        raise InputError("Auto connectivity checks accept only local runtime settings, never text.")
    runtime = request.get("runtime", {})
    if not isinstance(runtime, dict) or set(runtime) - {
        "model_dir",
        "offline_python",
        "ffmpeg",
        "ffprobe",
    }:
        raise InputError("Auto connectivity checks accept only local runtime paths.")
    discovered = engines(request)
    decision = resolve_engine("auto", discovered)
    probe = await probe_edge_reachability() if decision["probe_required"] else None
    decision = resolve_engine("auto", discovered, probe)
    return {**discovered, **decision, "decision": decision, "edge_probe": probe}


async def dispatch(request: dict, emit: EventSink | None = None) -> dict:
    if not isinstance(request, dict):
        raise InputError("Worker request must be an object.")
    emit = emit or (lambda event: None)
    command = request.get("command")
    if command == "clean":
        from ..cleaning import clean_english

        text = _text(request.get("text"))
        if request.get("mode") == "original":
            return {
                "original_text": text,
                "cleaned_text": text,
                "version": "original-v1",
                "removed_chinese_lines": 0,
                "changes": [],
                "warnings": [],
            }
        if request.get("mode") != "english":
            raise InputError("Unknown text preparation mode.")
        return asdict(clean_english(text, mode=request.get("cleaning_mode", "conservative")))
    if command == "import":
        from .importing import DocumentImportError, import_document

        try:
            return import_document(
                _path(request.get("path")), encoding=request.get("encoding", "auto")
            )
        except DocumentImportError as exc:
            raise StudioError("DOCUMENT_IMPORT_FAILED", str(exc), 2) from None
    if command == "preview":
        return preview(request)
    if command == "start":
        return await start(request, emit)
    if command == "resume":
        return await resume(request, emit)
    if command == "cancel":
        return cancel_task(_path(request.get("task_path")))
    if command == "status":
        return status(request)
    if command == "history":
        return history(request)
    if command == "verify":
        return verify(request)
    if command == "engines":
        return engines(request)
    if command == "auto_probe":
        return await auto_probe(request)
    raise InputError("Unknown desktop worker command.")
