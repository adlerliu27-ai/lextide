"""Durable G3 tasks. Input identity, cache identity and artifact integrity are distinct."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import time_ns
from uuid import uuid4

from .. import __version__
from ..audio import resolve_media_tools
from ..config import Settings
from ..errors import InputError, StudioError, ValidationError
from ..jobs import ARTIFACT_NAMES, _validate_model_record
from ..reading import validate_reading_text
from ..text import PreparedText
from .document import TEXT_RULE_VERSION, chunk_document
from .media import (
    LongEngine,
    _decoder_version,
    assemble,
    postprocessing_settings,
    prepare_export_subtitles,
    validate_export_audio,
    validate_export_srt,
    validate_pcm,
)
from .roundtrip import validate_sample_roundtrip
from .storage import (
    Cache,
    atomic_json,
    canonical_key,
    exclusive_lock,
    file_sha256,
    read_json,
    safe_directory,
    validate_chunk_metadata,
)

SCHEMA = 3
G3_ARTIFACT_NAMES = (*ARTIFACT_NAMES, "plan.json")
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_TASK_SAMPLES = 24000 * 7200
ARTIFACT_LIMITS = {
    **{name: MAX_JSON_BYTES for name in G3_ARTIFACT_NAMES},
    **{name: 1024 * 1024 for name in ("original.txt", "tts.txt", "subtitle.txt")},
    "audio.mp3": 128 * 1024 * 1024,
}


def _check_file(path: Path, limit: int) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValidationError("任务文件不是有界普通文件。")


def _read_text(path: Path, limit: int) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValidationError("任务文本必须为普通文件。")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValidationError("任务文本超过大小上限。")
    return data.decode("utf-8")


def _bounded_json(value: dict) -> None:
    if (
        len(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")) + 1
        > MAX_JSON_BYTES
    ):
        raise InputError("任务计划或元数据超过 16 MiB 上限；请拆分输入为独立任务。")


ACTIVE = {"preparing", "chunking", "synthesizing", "assembling", "verifying"}
TRANSITIONS = {
    "created": {"preparing", "cancelled"},
    "preparing": {"chunking", "failed", "paused", "cancelled"},
    "chunking": {"synthesizing", "failed", "paused", "cancelled"},
    "synthesizing": {"assembling", "failed", "paused", "cancelled"},
    "assembling": {"verifying", "failed", "paused", "cancelled"},
    "verifying": {"completed", "failed", "paused", "cancelled"},
    "completed": {"preparing"},
    "failed": {"preparing", "cancelled"},
    "paused": {"preparing", "cancelled"},
    "cancelled": {"preparing"},
}


def default_cache_dir() -> Path:
    return Path(
        os.environ.get(
            "LEXTIDE_CACHE_DIR",
            str(Path.home() / "Library/Application Support/Lextide/chunk-cache"),
        )
    ).expanduser()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_text(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def _copy(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file() or target.is_symlink():
        raise ValidationError("任务文件必须为普通文件，不能是符号链接。")
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _separate(task_parent: Path, cache_root: Path) -> None:
    if (
        task_parent == cache_root
        or task_parent.is_relative_to(cache_root)
        or cache_root.is_relative_to(task_parent)
    ):
        raise InputError("任务输出目录与缓存目录不能互相包含；请使用相邻独立目录。")


def _request(chunk: dict, identity: dict, plan: dict) -> dict:
    return {
        "schema_version": 1,
        "text": chunk["text"],
        "engine": identity,
        "text_rules_version": TEXT_RULE_VERSION,
        "chunk_rules_version": plan["rule_version"],
        "cache_artifact": "raw-mono-s16-wav-24000-v1",
    }


def _export_key(chunks: list[dict], config: dict) -> str:
    return canonical_key(
        {
            "raw_chunk_keys": [chunk["cache_key"] for chunk in chunks],
            "export_profile": "g3-pcm-concat-mp3-v1",
            "audio_filter": config["audio_filter"]
            if config["settings"]["engine"] == "kokoro"
            else None,
            "pause_ms": config["pause_ms"],
            "encoder": "libmp3lame-128k-mono-24000-xing",
            "audio_postprocessing": config["audio_postprocessing"],
            "raw_engine": config["raw_engine"],
            "ffmpeg_version": config["ffmpeg_version"],
        }
    )


def create_task(
    prepared: PreparedText,
    settings: Settings,
    out_dir: Path,
    cache_dir: Path,
    *,
    engine: LongEngine,
    chunk_bytes: int | None = None,
    audio_filter: str = "on",
    pause_ms: int = 0,
) -> Path:
    settings.validate()
    validate_reading_text(prepared.tts_text, settings)
    if audio_filter not in {"on", "off"} or type(pause_ms) is not int or not 0 <= pause_ms <= 2000:
        raise InputError("filter 为 on/off，块间停顿必须为 0–2000ms 整数。")
    out_dir = safe_directory(out_dir, create=True)
    cache_dir = safe_directory(cache_dir)
    _separate(out_dir, cache_dir)
    plan = chunk_document(prepared, settings.engine, max_bytes=chunk_bytes)
    identity = engine.identity()
    tools = resolve_media_tools(getattr(engine, "ffmpeg", None), getattr(engine, "ffprobe", None))
    _bounded_json(plan)
    _bounded_json({"changes": prepared.changes})
    config = {
        "settings": asdict(settings),
        "audio_filter": audio_filter,
        "pause_ms": pause_ms,
        "chunk_bytes": plan["max_bytes"],
        "raw_engine": identity,
        "ffmpeg_version": _decoder_version(tools[0]),
        "audio_postprocessing": postprocessing_settings(settings.engine, audio_filter),
    }
    job_id = uuid4().hex
    destination = out_dir / f"task-{job_id}"
    temporary = Path(tempfile.mkdtemp(prefix=".creating-task-", dir=out_dir))
    try:
        (temporary / "original.txt").write_bytes(prepared.original_bytes)
        _write_text(temporary / "tts.txt", prepared.tts_text)
        atomic_json(temporary / "changes.json", {"changes": prepared.changes})
        atomic_json(temporary / "plan.json", plan)
        chunks = []
        for item in plan["chunks"]:
            request = _request(item, identity, plan)
            chunks.append(
                {
                    "chunk_id": item["chunk_id"],
                    "index": item["index"],
                    "cache_key": canonical_key(request),
                    "config_key": canonical_key(config),
                    "retry_count": 0,
                    "input_sha256": item["text_sha256"],
                    "status": "pending",
                    "attempts": 0,
                    "cache_hit": False,
                    "path": f"chunks/{item['index']:06d}.wav",
                    "artifact_sha256": None,
                    "metadata": None,
                    "failure": None,
                }
            )
        manifest = {
            "schema_version": SCHEMA,
            "kind": "longform-task",
            "app_version": __version__,
            "job_id": job_id,
            "status": "created",
            "created_at": _now(),
            "updated_at": _now(),
            "run_count": 0,
            "last_error": None,
            "config": config,
            "config_key": canonical_key(config),
            "cache_dir": str(cache_dir),
            "input_type": prepared.input_type,
            "inputs": {
                name: file_sha256(temporary / name)
                for name in ("original.txt", "tts.txt", "changes.json", "plan.json")
            },
            "chunks": chunks,
            "export_key": _export_key(chunks, config),
            "export_path": None,
        }
        _bounded_json(manifest)
        atomic_json(temporary / "task.json", manifest)
        with (temporary / "original.txt").open("rb") as original:
            os.fsync(original.fileno())
        temporary.rename(destination)
        _fsync_dir(out_dir)
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _load(task: Path) -> tuple[dict, dict]:
    task = safe_directory(task)
    manifest = read_json(task / "task.json")
    try:
        if (
            manifest["schema_version"] != SCHEMA
            or manifest["kind"] != "longform-task"
            or manifest["status"] not in TRANSITIONS
        ):
            raise ValueError
        if canonical_key(manifest["config"]) != manifest["config_key"]:
            raise ValueError
        for name in ("original.txt", "tts.txt", "changes.json", "plan.json"):
            _check_file(task / name, ARTIFACT_LIMITS[name])
            if file_sha256(task / name) != manifest["inputs"][name]:
                raise ValueError
        plan = read_json(task / "plan.json")
        if (
            len(plan["chunks"]) != len(manifest["chunks"])
            or not 1 <= len(manifest["chunks"]) <= 2000
        ):
            raise ValueError
        for index, (chunk, saved) in enumerate(
            zip(plan["chunks"], manifest["chunks"], strict=True)
        ):
            request = _request(chunk, manifest["config"]["raw_engine"], plan)
            if (
                chunk["index"] != index
                or saved["index"] != index
                or saved["chunk_id"] != chunk["chunk_id"]
                or saved["cache_key"] != canonical_key(request)
                or saved["config_key"] != manifest["config_key"]
                or saved["input_sha256"] != chunk["text_sha256"]
                or saved["path"] != f"chunks/{index:06d}.wav"
            ):
                raise ValueError
        if manifest["export_key"] != _export_key(manifest["chunks"], manifest["config"]):
            raise ValueError
    except (KeyError, TypeError, ValueError, OSError):
        raise ValidationError("任务输入、配置或分块计划不完整/已改变；拒绝混用结果。") from None
    return manifest, plan


def _save(task: Path, manifest: dict) -> None:
    manifest["updated_at"] = _now()
    _bounded_json(manifest)
    atomic_json(task / "task.json", manifest)


def _transition(task: Path, manifest: dict, state: str) -> None:
    if state not in TRANSITIONS[manifest["status"]]:
        raise ValidationError("不允许的任务状态转换。")
    manifest["status"] = state
    _save(task, manifest)


def task_status(task: Path) -> dict:
    manifest, _ = _load(task)
    return {
        "job_id": manifest["job_id"],
        "status": manifest["status"],
        "completed_chunks": sum(c["status"] == "completed" for c in manifest["chunks"]),
        "total_chunks": len(manifest["chunks"]),
        "run_count": manifest["run_count"],
        "chunks": [
            {
                k: c[k]
                for k in (
                    "index",
                    "status",
                    "attempts",
                    "cache_hit",
                    "cache_key",
                    "artifact_sha256",
                    "failure",
                )
            }
            for c in manifest["chunks"]
        ],
        "export_path": manifest["export_path"],
        "last_error": manifest["last_error"],
        "note": "状态为最近一次原子检查点；SIGKILL 后可能保留 active 状态，resume 将重新核验。",
    }


def cancel_task(task: Path) -> dict:
    task = safe_directory(task)
    with exclusive_lock(task / ".cancel.lock"):
        manifest, _ = _load(task)
        if manifest["status"] == "completed":
            return {"status": "completed", "cancel_requested": False}
        atomic_json(
            task / "cancel.request", {"job_id": manifest["job_id"], "requested_ns": time_ns()}
        )
    try:
        with exclusive_lock(task / ".task.lock"):
            manifest, _ = _load(task)
            if manifest["status"] != "cancelled" and manifest["status"] != "completed":
                _transition(task, manifest, "cancelled")
    except StudioError as exc:
        if exc.code != "TASK_BUSY":
            raise
    return {
        "status": "cancel_requested",
        "note": "运行中任务在当前块/阶段结束后安全停止；Ctrl-C 可立即暂停。",
    }


def _cancelled(task: Path, manifest: dict) -> bool:
    if (task / "cancel.request").exists():
        if manifest["status"] != "cancelled":
            _transition(task, manifest, "cancelled")
        return True
    return False


def _clean_work(task: Path) -> Path:
    work = safe_directory(task / "work")
    if work.exists():
        # Only this task's known disposable workspace, while holding its exclusive lock.
        for path in work.rglob("*"):
            if path.is_symlink():
                raise ValidationError("任务临时目录包含符号链接，拒绝自动清理。")
        shutil.rmtree(work)
    work.mkdir(mode=0o700)
    return work


def _same_local(path: Path, cached: dict) -> bool:
    try:
        report = validate_pcm(path)
        return (
            report["sha256"] == cached["artifact_sha256"]
            and report["samples"] == cached["metadata"]["samples"]
        )
    except (StudioError, OSError, KeyError, TypeError):
        return False


async def run_task(
    task: Path,
    *,
    allow_online: bool = False,
    model_dir: Path | None = None,
    offline_python: str | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    expected_input: PreparedText | None = None,
    expected_options: dict | None = None,
    progress=None,
    engine_factory=LongEngine,
) -> Path | None:
    run_started_ns = time_ns()
    task = safe_directory(task)
    with exclusive_lock(task / ".task.lock"):
        manifest, plan = _load(task)
        config = manifest["config"]
        settings = Settings(**config["settings"])
        settings.validate()
        postprocessing_settings(
            settings.engine, config["audio_filter"], saved=config["audio_postprocessing"]
        )
        if settings.engine == "edge-tts" and not allow_online:
            raise StudioError(
                "ONLINE_NOT_AUTHORIZED", "恢复在线任务也必须提供本次 --allow-online。", 3
            )
        validate_reading_text(_read_text(task / "tts.txt", ARTIFACT_LIMITS["tts.txt"]), settings)
        if expected_input is not None:
            import hashlib

            if (
                hashlib.sha256(expected_input.original_bytes).hexdigest()
                != manifest["inputs"]["original.txt"]
            ):
                raise InputError("恢复输入与已保存原文不一致；请创建新任务。")
        for name, value in (expected_options or {}).items():
            saved = config["settings"].get(name, config.get(name))
            if value is not None and saved != value:
                raise InputError("恢复参数与已保存任务不一致；请创建新任务。")
        engine = engine_factory(
            settings,
            model_dir=model_dir,
            offline_python=offline_python,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        tools = resolve_media_tools(ffmpeg, ffprobe)
        if _decoder_version(tools[0]) != config["ffmpeg_version"]:
            raise InputError("FFmpeg 版本已改变；请创建新任务以使用新的导出配置。")
        if engine.identity() != config["raw_engine"]:
            raise InputError("引擎/模型/runtime 配置已改变；不能继续旧任务，请创建新任务。")
        cache_root = safe_directory(Path(manifest["cache_dir"]))
        _separate(task.parent, cache_root)
        cache = Cache(cache_root)
        # Initial G3 intentionally serializes work sharing one cache, bounding model memory.
        with exclusive_lock(cache_root / ".runner.lock"):
            work = _clean_work(task)
            safe_directory(task / "chunks", create=True)
            safe_directory(task / "exports", create=True)
            with exclusive_lock(task / ".cancel.lock"):
                if (task / "cancel.request").exists() and read_json(task / "cancel.request").get(
                    "requested_ns", 0
                ) < run_started_ns:
                    (
                        task / "cancel.request"
                    ).unlink()  # Explicit resume supersedes only an older request.
            if manifest["status"] in ACTIVE:
                manifest["status"] = "paused"
                manifest["last_error"] = {"code": "INTERRUPTED_CHECKPOINT", "phase": "recovery"}
                _save(task, manifest)
            _transition(task, manifest, "preparing")
            manifest["run_count"] += 1
            manifest["last_error"] = None
            manifest["last_run_allow_online"] = bool(allow_online and settings.engine == "edge-tts")
            _save(task, manifest)
            try:
                _transition(task, manifest, "chunking")
                _transition(task, manifest, "synthesizing")
                completed = []
                actual_samples = 0
                for chunk, saved in zip(plan["chunks"], manifest["chunks"], strict=True):
                    if _cancelled(task, manifest):
                        return None
                    key = saved["cache_key"]
                    request = _request(chunk, config["raw_engine"], plan)
                    with cache.lock(key):
                        local = task / saved["path"]
                        cached = cache.load(key, request)
                        local_checkpoint = None
                        if saved["status"] == "completed" and _same_local(local, saved):
                            try:
                                validate_chunk_metadata(saved["metadata"], request, local)
                                local_checkpoint = {
                                    "path": str(local),
                                    "cache_key": key,
                                    "artifact_sha256": saved["artifact_sha256"],
                                    "metadata": saved["metadata"],
                                }
                            except (ValidationError, KeyError, TypeError):
                                pass
                        if local_checkpoint is not None:
                            if cached is None:
                                cache.publish(key, request, local, saved["metadata"])
                            # Preserve this task's actual nondeterministic output even if a
                            # different valid rendering now occupies the same request cache.
                            cached = local_checkpoint
                        saved["cache_hit"] = cached is not None
                        saved["reused_from"] = (
                            "task" if local_checkpoint else ("cache" if cached else None)
                        )
                        if cached is None:
                            saved.update(
                                status="synthesizing", failure=None, attempts=saved["attempts"] + 1
                            )
                            saved["retry_count"] = saved["attempts"] - 1
                            _save(task, manifest)
                            generated = work / f"chunk-{saved['index']:06d}.wav"
                            generated.unlink(missing_ok=True)
                            metadata = await engine.synthesize(
                                chunk["text"], generated, allow_online=allow_online
                            )
                            cached = cache.publish(key, request, generated, metadata)
                            generated.unlink(missing_ok=True)
                        local = task / saved["path"]
                        if not _same_local(local, cached):
                            temporary = work / f"copy-{saved['index']:06d}.wav"
                            _copy(Path(cached["path"]), temporary)
                            if file_sha256(temporary) != cached["artifact_sha256"]:
                                raise ValidationError("缓存复制完整性失败。")
                            os.replace(temporary, local)
                            _fsync_dir(local.parent)
                        saved.update(
                            status="completed",
                            metadata=cached["metadata"],
                            artifact_sha256=cached["artifact_sha256"],
                            failure=None,
                        )
                        _save(task, manifest)
                        actual_samples += cached["metadata"]["samples"]
                        if completed:
                            actual_samples += config["pause_ms"] * 24
                        if actual_samples > MAX_TASK_SAMPLES:
                            raise ValidationError(
                                "累计真实 PCM 已超过两小时；保留有效块，请拆分为较短任务。"
                            )
                        completed.append({**cached, "path": str(local)})
                    if progress:
                        progress(
                            {
                                **task_status(task),
                                "current_index": saved["index"],
                                "current_cache_hit": saved["cache_hit"],
                            }
                        )
                if _cancelled(task, manifest):
                    return None
                _transition(task, manifest, "assembling")
                export_stage = work / "export"
                export_stage.mkdir(mode=0o700)
                result = await assemble(
                    completed,
                    export_stage / "audio.mp3",
                    settings=settings,
                    audio_filter=config["audio_filter"],
                    audio_postprocessing=config["audio_postprocessing"],
                    pause_ms=config["pause_ms"],
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                )
                if _cancelled(task, manifest):
                    return None
                _transition(task, manifest, "verifying")
                output = _finish_export(task, export_stage, manifest, result, ffmpeg, ffprobe)
                return output
            except (asyncio.CancelledError, KeyboardInterrupt):
                if manifest["status"] in ACTIVE:
                    _transition(task, manifest, "paused")
                raise
            except Exception as exc:
                code = (
                    exc.code
                    if isinstance(exc, StudioError)
                    else ("LOCAL_IO_FAILED" if isinstance(exc, OSError) else "TASK_INTERNAL_ERROR")
                )
                manifest["last_error"] = {"code": code, "phase": manifest["status"]}
                for saved in manifest["chunks"]:
                    if saved["status"] == "synthesizing":
                        saved.update(status="failed", failure=code)
                if manifest["status"] in ACTIVE:
                    _transition(task, manifest, "failed")
                if isinstance(exc, StudioError):
                    raise
                raise StudioError(
                    code, "长文任务未完成；已完成块保留，可检查后 resume。", 6
                ) from None


def _finish_export(
    task: Path, stage: Path, task_manifest: dict, result, ffmpeg, ffprobe
) -> Path | None:
    from ..audio import resolve_media_tools

    tools = resolve_media_tools(ffmpeg, ffprobe)
    if (
        result.audio_format != "mp3"
        or not result.stream_complete
        or file_sha256(stage / "audio.mp3") != result.audio_sha256
        or (stage / "audio.mp3").stat().st_size != result.audio_bytes
    ):
        raise ValidationError("最终音频与完整流记录不符。")
    audio = validate_export_audio(stage / "audio.mp3", ffmpeg=tools[0], ffprobe=tools[1])
    roundtrip = validate_sample_roundtrip(
        stage / "audio.mp3", audio, result.effective_settings["samples"]
    )
    if roundtrip != result.effective_settings.get("sample_roundtrip"):
        raise ValidationError("最终音频样本往返校验记录与实际文件不一致。")
    text = _read_text(task / "tts.txt", ARTIFACT_LIMITS["tts.txt"])
    srt, subtitles = prepare_export_subtitles(
        result.boundaries,
        audio_duration_ms=audio["duration_ms"],
        expected_text=text,
        timing_source=result.timing_source,
    )
    for name in ("original.txt", "tts.txt", "changes.json", "plan.json"):
        _copy(task / name, stage / name)
        if file_sha256(stage / name) != task_manifest["inputs"][name]:
            raise ValidationError("运行期间任务输入发生变化；拒绝发布混合产物。")
    _write_text(stage / "subtitle.txt", text)
    _write_text(stage / "subtitles.srt", srt)
    sentences = {"boundaries": [asdict(b) for b in result.boundaries]}
    _bounded_json(sentences)
    atomic_json(stage / "sentences.json", sentences)
    atomic_json(stage / "validation.json", {"audio": audio, "subtitles": subtitles})
    config = task_manifest["config"]
    for name in G3_ARTIFACT_NAMES:
        _check_file(stage / name, ARTIFACT_LIMITS[name])
    manifest = {
        "schema_version": 3,
        "kind": "longform-export",
        "status": "completed",
        "app_version": __version__,
        "job_id": task_manifest["job_id"],
        "created_at": _now(),
        "export_key": task_manifest["export_key"],
        "config": config,
        "config_key": task_manifest["config_key"],
        "engine": config["raw_engine"],
        "settings": config["settings"],
        "model": result.model,
        "effective_settings": {
            **result.effective_settings,
            "ffmpeg_version": config["ffmpeg_version"],
        },
        "audio_format": "mp3",
        "consent": {
            "allow_online": task_manifest["last_run_allow_online"],
            "confirmed_text_sha256": task_manifest["inputs"]["tts.txt"],
        },
        "timing": {
            "level": "sentence",
            "source": result.timing_source,
            "notes": list(result.timing_notes),
        },
        "stream": {
            "complete": True,
            "audio_bytes": result.audio_bytes,
            "audio_sha256": result.audio_sha256,
        },
        "original_sha256": task_manifest["inputs"]["original.txt"],
        "text_sha256": task_manifest["inputs"]["tts.txt"],
        "chunks": [
            {
                k: c[k]
                for k in (
                    "index",
                    "chunk_id",
                    "cache_key",
                    "input_sha256",
                    "artifact_sha256",
                    "metadata",
                )
            }
            for c in task_manifest["chunks"]
        ],
        "artifacts": {
            name: {"sha256": file_sha256(stage / name), "bytes": (stage / name).stat().st_size}
            for name in G3_ARTIFACT_NAMES
        },
    }
    _validate_export_identity(manifest)
    _bounded_json(manifest)
    atomic_json(stage / "manifest.json", manifest)
    _fsync_dir(stage)
    destination = task / "exports" / f"job-{uuid4().hex}"
    # Serialize the last cancellation check with publication and its completion checkpoint.
    with exclusive_lock(task / ".cancel.lock"):
        if _cancelled(task, task_manifest):
            return None
        stage.rename(destination)
        _fsync_dir(destination.parent)
        task_manifest["export_path"] = str(destination.relative_to(task))
        _transition(task, task_manifest, "completed")
    return destination


def _validate_export_identity(manifest: dict) -> None:
    config = manifest["config"]
    engine = manifest["engine"]
    model = manifest["model"]
    if (
        canonical_key(config) != manifest["config_key"]
        or engine != config["raw_engine"]
        or engine["engine_id"] != config["settings"]["engine"]
        or type(engine["online"]) is not bool
        or engine["online"] != (engine["engine_id"] == "edge-tts")
        or manifest["export_key"] != _export_key(manifest["chunks"], config)
        or manifest["effective_settings"]["audio_postprocessing"] != config["audio_postprocessing"]
        or model != engine["model"]
        or any(c["metadata"]["model"] != model for c in manifest["chunks"])
    ):
        raise ValidationError("导出引擎、模型或处理配置记录不一致。")
    if not engine["online"]:
        _validate_model_record(model)


def verify_export(path: Path, *, ffmpeg: str | None = None, ffprobe: str | None = None) -> dict:
    from ..audio import resolve_media_tools

    path = safe_directory(path)
    manifest = read_json(path / "manifest.json")
    try:
        if (
            manifest["schema_version"] != 3
            or manifest["kind"] != "longform-export"
            or manifest["status"] != "completed"
            or manifest["stream"]["complete"] is not True
        ):
            raise ValueError
        _validate_export_identity(manifest)
        for name in G3_ARTIFACT_NAMES:
            item = path / name
            expected = manifest["artifacts"][name]
            _check_file(item, ARTIFACT_LIMITS[name])
            if (
                item.is_symlink()
                or item.stat().st_size != expected["bytes"]
                or file_sha256(item) != expected["sha256"]
            ):
                raise ValueError
        if (
            manifest["audio_format"] != "mp3"
            or manifest["timing"]["level"] != "sentence"
            or manifest["original_sha256"] != manifest["artifacts"]["original.txt"]["sha256"]
            or manifest["text_sha256"] != manifest["artifacts"]["tts.txt"]["sha256"]
            or manifest["stream"]["audio_bytes"] != manifest["artifacts"]["audio.mp3"]["bytes"]
            or manifest["stream"]["audio_sha256"] != manifest["artifacts"]["audio.mp3"]["sha256"]
        ):
            raise ValueError
        tools = resolve_media_tools(ffmpeg, ffprobe)
        audio = validate_export_audio(path / "audio.mp3", ffmpeg=tools[0], ffprobe=tools[1])
        subtitles = validate_export_srt(
            _read_text(path / "subtitles.srt", ARTIFACT_LIMITS["subtitles.srt"]),
            audio_duration_ms=audio["duration_ms"],
        )
        effective = manifest["effective_settings"]
        if "sample_roundtrip" in effective:
            roundtrip = validate_sample_roundtrip(path / "audio.mp3", audio, effective["samples"])
            if roundtrip != effective["sample_roundtrip"]:
                raise ValueError
        elif audio["decoded_samples_per_channel"] != effective["samples"]:
            # Legacy G3 exports were accepted only with exact sample equality.
            raise ValueError
        expected_srt, coverage = prepare_export_subtitles(
            read_json(path / "sentences.json")["boundaries"],
            audio_duration_ms=audio["duration_ms"],
            expected_text=_read_text(path / "tts.txt", ARTIFACT_LIMITS["tts.txt"]),
            timing_source=manifest["timing"]["source"],
        )
        if expected_srt != _read_text(path / "subtitles.srt", ARTIFACT_LIMITS["subtitles.srt"]):
            raise ValueError
        return {
            "status": "valid",
            "text_coverage": coverage["text_coverage"],
            "audio": audio,
            "subtitles": subtitles,
            "hashes_match": True,
            "network_used": False,
        }
    except (OSError, ValueError, KeyError, TypeError):
        raise ValidationError("G3 导出不完整或与成功 manifest 不一致。") from None
