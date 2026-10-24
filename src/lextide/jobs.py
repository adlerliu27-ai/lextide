"""One short synthesis job: stage privately, validate, then publish a complete directory."""

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from . import __version__
from .audio import resolve_media_tools, validate_audio
from .config import Settings
from .engines.base import TTSEngine
from .errors import StudioError, ValidationError
from .subtitles import prepare_subtitles, validate_srt
from .text import RULE_VERSION, PreparedText

ARTIFACT_NAMES = (
    "original.txt",
    "tts.txt",
    "subtitle.txt",
    "changes.json",
    "sentences.json",
    "audio.mp3",
    "subtitles.srt",
    "validation.json",
)


def _validate_model_record(model: object) -> None:
    if not isinstance(model, dict) or not all(
        isinstance(model.get(key), str) and model[key] for key in ("id", "version")
    ):
        raise ValidationError("离线任务缺少模型标识或版本。")
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", str(model.get(key, "")))
        for key in ("sha256", "voices_sha256")
    ):
        raise ValidationError("离线任务缺少有效的模型/声线 SHA-256。")


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def synthesize_job(
    prepared: PreparedText,
    settings: Settings,
    out_dir: Path,
    engine: TTSEngine,
    *,
    allow_online: bool,
    confirmed_sha256: str,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> Path:
    settings.validate()
    if engine.online and not allow_online:
        raise StudioError("ONLINE_NOT_AUTHORIZED", "缺少 --allow-online，未发送请求。", 3)
    if confirmed_sha256 != prepared.sha256:
        raise StudioError("TEXT_NOT_CONFIRMED", "确认指纹不匹配；请重新预览当前文本。", 3)
    media_tools = resolve_media_tools(ffmpeg, ffprobe)
    stage = None
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        job_id = uuid4().hex
        final = out_dir / f"job-{job_id}"
        stage = Path(tempfile.mkdtemp(prefix=".lextide-", suffix=".tmp", dir=out_dir))
        (stage / "original.txt").write_bytes(prepared.original_bytes)
        (stage / "tts.txt").write_text(prepared.tts_text, encoding="utf-8")
        (stage / "subtitle.txt").write_text(prepared.tts_text, encoding="utf-8")
        _write_json(
            stage / "changes.json",
            {
                "rule_version": RULE_VERSION,
                "span_unit": "unicode_codepoint_half_open",
                "text_confirmed_sha256": confirmed_sha256,
                "changes": prepared.changes,
            },
        )
        result = await engine.synthesize(
            prepared.tts_text, stage / "audio.mp3", settings, allow_online=allow_online
        )
        if not result.stream_complete:
            raise ValidationError("合成流未正常完成。")
        if result.audio_format != "mp3":
            raise ValidationError("引擎未返回当前支持的 MP3 格式。")
        if not engine.online:
            _validate_model_record(result.model)
        audio_path = stage / "audio.mp3"
        if (
            not audio_path.is_file()
            or audio_path.stat().st_size != result.audio_bytes
            or _sha256(audio_path) != result.audio_sha256
        ):
            raise ValidationError("音频与收到的流字节数/哈希不一致。")
        audio_report = validate_audio(audio_path, ffmpeg=media_tools[0], ffprobe=media_tools[1])
        srt, subtitle_report = prepare_subtitles(
            result.boundaries,
            audio_duration_ms=audio_report["duration_ms"],
            expected_text=prepared.tts_text,
            timing_source=result.timing_source,
        )
        (stage / "subtitles.srt").write_text(srt, encoding="utf-8")
        _write_json(stage / "sentences.json", [asdict(item) for item in result.boundaries])
        _write_json(
            stage / "validation.json", {"audio": audio_report, "subtitles": subtitle_report}
        )
        _write_json(
            stage / "manifest.json",
            {
                "schema_version": 2,
                "app_version": __version__,
                "job_id": job_id,
                "created_at": datetime.now(UTC).isoformat(),
                "status": "completed",
                "input_type": prepared.input_type,
                "original_sha256": _sha256(stage / "original.txt"),
                "text_sha256": prepared.sha256,
                "text_rules_version": RULE_VERSION,
                "engine": {
                    "id": engine.engine_id,
                    "version": engine.version,
                    "online": engine.online,
                },
                "settings": asdict(settings),
                "effective_settings": result.effective_settings or asdict(settings),
                "model": result.model,
                "audio_format": result.audio_format,
                "timing": {
                    "level": "sentence",
                    "source": result.timing_source,
                    "notes": list(result.timing_notes),
                },
                "boundary": "SentenceBoundary" if engine.online else "SentencePCMSamples",
                "consent": {
                    "allow_online": allow_online if engine.online else False,
                    "confirmed_text_sha256": confirmed_sha256,
                },
                "stream": {
                    "complete": True,
                    "audio_bytes": result.audio_bytes,
                    "audio_sha256": result.audio_sha256,
                },
                "artifacts": {
                    name: {"sha256": _sha256(stage / name), "bytes": (stage / name).stat().st_size}
                    for name in ARTIFACT_NAMES
                },
            },
        )
        for item in stage.iterdir():
            with item.open("rb") as handle:
                os.fsync(handle.fileno())
        if final.exists():
            raise StudioError("OUTPUT_CONFLICT", "输出标识发生冲突；请重新运行。", 6)
        stage.rename(final)  # Same filesystem; readers see all completed artifacts together.
        stage = None
        return final
    except OSError:
        raise StudioError(
            "LOCAL_IO_FAILED", "本地读写失败；检查路径、权限或剩余空间。未发布成功产物。", 6
        ) from None
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def verify_job(job_dir: Path, *, ffmpeg: str | None = None, ffprobe: str | None = None) -> dict:
    """Offline revalidation against the trusted manifest saved when the stream completed."""
    try:
        manifest_path = job_dir / "manifest.json"
        if manifest_path.stat().st_size > 16 * 1024 * 1024:
            raise ValidationError("manifest 超过大小上限。")
        from .longform.storage import read_json

        candidate = read_json(manifest_path)
        if isinstance(candidate, dict) and candidate.get("schema_version") == 3:
            from .longform.tasks import verify_export

            return verify_export(job_dir, ffmpeg=ffmpeg, ffprobe=ffprobe)
        if manifest_path.stat().st_size > 128 * 1024:
            raise ValidationError("manifest 超过大小上限。")
        manifest = candidate
        if (
            manifest["schema_version"] not in (1, 2)
            or manifest["status"] != "completed"
            or manifest["stream"]["complete"] is not True
        ):
            raise ValidationError("任务未完成或 manifest 版本不支持。")
        if manifest["schema_version"] == 2:
            if manifest["audio_format"] != "mp3" or manifest["timing"]["level"] != "sentence":
                raise ValidationError("任务使用了不支持的音频或字幕格式。")
            if not manifest["engine"]["online"]:
                _validate_model_record(manifest["model"])
        for name in ARTIFACT_NAMES:
            path = job_dir / name
            if path.is_symlink() or not path.is_file():
                raise ValidationError(f"产物缺失或不为普通文件：{name}")
            expected = manifest["artifacts"][name]
            if path.stat().st_size != expected["bytes"] or _sha256(path) != expected["sha256"]:
                raise ValidationError(f"产物与成功时的 manifest 不符：{name}")
        tools = resolve_media_tools(ffmpeg, ffprobe)
        report = validate_audio(job_dir / "audio.mp3", ffmpeg=tools[0], ffprobe=tools[1])
        srt_report = validate_srt(
            (job_dir / "subtitles.srt").read_text(encoding="utf-8"),
            audio_duration_ms=report["duration_ms"],
        )
        return {
            "status": "valid",
            "audio": report,
            "subtitles": srt_report,
            "hashes_match": True,
            "network_used": False,
        }
    except (OSError, ValueError, KeyError, TypeError):
        raise ValidationError("无法读取完整任务或 manifest 格式错误。") from None
