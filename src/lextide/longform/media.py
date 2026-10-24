"""Bounded raw PCM synthesis and a single final MP3 encode for G3."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import struct
import subprocess
import tempfile
import wave
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

from ..audio import resolve_media_tools, validate_audio
from ..config import Settings
from ..engines.base import EngineResult, SentenceBoundary
from ..engines.edge import EdgeEngine
from ..engines.kokoro import KokoroEngine, _runtime_environment
from ..engines.kokoro_worker import _RUNTIME_PACKAGES
from ..engines.sentence_split import split_sentences
from ..errors import InputError, StudioError, ValidationError
from ..model_catalog import MODEL
from ..models import default_model_dir, model_lease
from ..subtitles import prepare_subtitles, validate_srt
from .process import run_supervised
from .roundtrip import validate_sample_roundtrip

SAMPLE_RATE = 24000
MAX_DURATION_SECONDS = 7200
MAX_MP3_BYTES = 128 * 1024 * 1024
MAX_CHUNK_SECONDS = 600
_TICKS_PER_SECOND = 10_000_000
_IO_BYTES = 65536
_MAX_WAV_HEADER_BYTES = 65536
_MODEL_FILE = "kokoro-v1.0.int8.onnx"
_VOICES_FILE = "voices-v1.0.bin"
_PCM_PROFILE = {
    "sample_rate": SAMPLE_RATE,
    "channels": 1,
    "sample_format": "s16le",
    "container": "wav",
}


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_IO_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_pcm(path: Path) -> dict:
    """Read every WAV frame in bounded blocks; reject truncated or hidden tails.

    Cached chunks must contain at least one nonzero sample and at most 600s.
    RIFF chunk envelopes are checked separately because wave ignores trailing
    bytes and can return fewer frames than its declared data chunk length.
    """
    path = Path(path)
    try:
        if path.is_symlink():
            raise ValidationError("PCM path must identify a regular file, not a symlink.")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValidationError("PCM path must identify a regular file.")
            max_bytes = SAMPLE_RATE * MAX_CHUNK_SECONDS * 2 + _MAX_WAV_HEADER_BYTES
            if not 44 <= before.st_size <= max_bytes:
                raise ValidationError("PCM file is empty, malformed, or exceeds the chunk limit.")
            header = stream.read(12)
            if (
                header[:4] != b"RIFF"
                or header[8:12] != b"WAVE"
                or int.from_bytes(header[4:8], "little") + 8 != before.st_size
            ):
                raise ValidationError("PCM RIFF length or WAV header is invalid.")
            position = 12
            data_bytes = None
            fmt_seen = False
            while position < before.st_size:
                stream.seek(position)
                envelope = stream.read(8)
                if len(envelope) != 8:
                    raise ValidationError("PCM has an incomplete WAV chunk header.")
                name, size = struct.unpack("<4sI", envelope)
                position += 8 + size + size % 2
                if position > before.st_size:
                    raise ValidationError("PCM has a truncated WAV chunk or padding byte.")
                if name == b"fmt ":
                    if fmt_seen or size < 16 or size > _MAX_WAV_HEADER_BYTES:
                        raise ValidationError("PCM format header is invalid or duplicated.")
                    fmt_seen = True
                    kind, channels, rate, byte_rate, stride, bits = struct.unpack(
                        "<HHIIHH", stream.read(16)
                    )
                    if (kind, channels, rate, byte_rate, stride, bits) != (
                        1,
                        1,
                        SAMPLE_RATE,
                        SAMPLE_RATE * 2,
                        2,
                        16,
                    ):
                        raise ValidationError("PCM must be mono s16 WAV at 24000 Hz.")
                elif name == b"data":
                    if data_bytes is not None or not fmt_seen or size % 2:
                        raise ValidationError("PCM data chunk is invalid or duplicated.")
                    data_bytes = size
            if data_bytes is None or before.st_size - data_bytes > _MAX_WAV_HEADER_BYTES:
                raise ValidationError("PCM data is missing or WAV metadata exceeds its limit.")
            stream.seek(0)
            samples = 0
            non_silent = False
            with wave.open(stream, "rb") as audio:
                if (
                    audio.getnchannels() != 1
                    or audio.getsampwidth() != 2
                    or audio.getframerate() != SAMPLE_RATE
                    or audio.getcomptype() != "NONE"
                    or audio.getnframes() * 2 != data_bytes
                    or not 0 < audio.getnframes() <= SAMPLE_RATE * MAX_CHUNK_SECONDS
                ):
                    raise ValidationError("PCM format or sample count is invalid.")
                while block := audio.readframes(_IO_BYTES // 2):
                    if len(block) % 2:
                        raise ValidationError("PCM ends with an incomplete sample.")
                    samples += len(block) // 2
                    non_silent = non_silent or block != b"\0" * len(block)
                if samples != audio.getnframes() or samples * 2 != data_bytes:
                    raise ValidationError("PCM decoded sample count differs from its header.")
            if not non_silent:
                raise ValidationError("PCM contains only zero-valued samples.")
            stream.seek(0)
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(_IO_BYTES), b""):
                digest.update(block)
            after = os.fstat(stream.fileno())
            current = path.stat()

            def fingerprint(item):
                return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)

            if fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(
                current
            ):
                raise ValidationError("PCM file changed during verification.")
    except (OSError, EOFError, wave.Error, struct.error):
        raise ValidationError("PCM file could not be read as complete mono s16 WAV.") from None
    return {
        "sample_rate": SAMPLE_RATE,
        "samples": samples,
        "channels": 1,
        "sha256": digest.hexdigest(),
        "bytes": before.st_size,
    }


def _boundaries(values: list) -> list[SentenceBoundary]:
    if not isinstance(values, list):
        raise ValidationError("Native chunk boundaries must be a list.")
    try:
        return [
            value if isinstance(value, SentenceBoundary) else SentenceBoundary(**value)
            for value in values
        ]
    except (TypeError, ValueError):
        raise ValidationError("Native chunk boundary metadata is invalid.") from None


def _duration_ms(samples: int) -> int:
    return max(1, (samples * 1000 + SAMPLE_RATE // 2) // SAMPLE_RATE)


def _derive_chunk_boundaries(
    native: list[SentenceBoundary], report: dict, *, samples: int, chunk_index: int
) -> tuple[list[SentenceBoundary], list[dict]]:
    """Apply already accepted local end caps to an export-only boundary copy.

    Cache metadata retains native ticks. Starts are never moved or scaled. The
    final cue is also bounded by the actual PCM sample endpoint: rounding a
    200.5ms chunk to 201ms must not carry a local 100ms overshoot into the next
    chunk as a 101ms global overlap.
    """
    derived = list(native)
    corrections = []

    def cap(cue_index: int, end_ticks: int, reason: str, local_adjustment=None) -> None:
        current = derived[cue_index]
        previous_end = current.offset_ticks + current.duration_ticks
        if previous_end <= end_ticks:
            return
        if end_ticks <= current.offset_ticks:
            raise ValidationError("A locally accepted cue has no duration inside its PCM chunk.")
        derived[cue_index] = SentenceBoundary(
            current.offset_ticks, end_ticks - current.offset_ticks, current.text
        )
        original = native[cue_index]
        corrections.append(
            {
                "chunk_index": chunk_index,
                "cue_index": cue_index + 1,
                "kind": reason,
                "native_end_ticks": original.offset_ticks + original.duration_ticks,
                "previous_end_ticks": previous_end,
                "derived_end_ticks": end_ticks,
                "delta_ticks": end_ticks - previous_end,
                "local_validation_adjustment": local_adjustment,
            }
        )

    for adjustment in report["adjustments"]:
        cap(
            adjustment["cue_index"] - 1,
            adjustment["adjusted_end_ms"] * 10000,
            adjustment["kind"],
            dict(adjustment),
        )
    cap(len(derived) - 1, samples * _TICKS_PER_SECOND // SAMPLE_RATE, "cap_pcm_end")
    return derived, corrections


def _copy_new(source: Path, destination: Path) -> None:
    created = False
    try:
        with source.open("rb") as input_stream, destination.open("xb") as output:
            created = True
            os.chmod(destination, 0o600)
            shutil.copyfileobj(input_stream, output, _IO_BYTES)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _decoder_version(ffmpeg: str) -> str:
    try:
        result = subprocess.run(
            [ffmpeg, "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        first_line = result.stdout.decode("utf-8", errors="strict").splitlines()[0]
        if result.returncode or not first_line.startswith("ffmpeg version "):
            raise ValueError
        return first_line
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError, IndexError):
        raise ValidationError("ffmpeg decoder version could not be verified.") from None


class LongEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        model_dir: Path | None = None,
        offline_python: str | None = None,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
    ):
        settings.validate()
        self.settings = settings
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.kokoro = KokoroEngine(model_dir, offline_python, ffmpeg, ffprobe)
        self.edge = EdgeEngine()

    def identity(self) -> dict:
        """Describe raw synthesis deterministically, without inference or network."""
        settings = self.settings
        identity = {
            "engine_id": settings.engine,
            "online": settings.engine == "edge-tts",
            "engine_version": (
                KokoroEngine.version if settings.engine == "kokoro" else EdgeEngine.version
            ),
            "voice": settings.voice,
            "rate": settings.rate,
            "raw_pcm_profile": dict(_PCM_PROFILE),
        }
        if settings.engine == "kokoro":
            assets = {asset.filename: asset.sha256 for asset in MODEL.assets}
            identity.update(
                {
                    "model": {
                        "id": MODEL.model_id,
                        "version": MODEL.version,
                        "sha256": assets[_MODEL_FILE],
                        "voices_sha256": assets[_VOICES_FILE],
                    },
                    "native_parameters": self._native_parameters(),
                    "runtime": {
                        "python": "3.13",
                        **{name: spec[1] for name, spec in sorted(_RUNTIME_PACKAGES.items())},
                    },
                }
            )
        else:
            ffmpeg, _ = resolve_media_tools(self.ffmpeg, self.ffprobe)
            identity.update(
                {
                    "model": None,
                    "native_parameters": {
                        "boundary": "SentenceBoundary",
                        "volume": "+0%",
                        "pitch": "+0Hz",
                    },
                    "runtime": {"edge-tts": EdgeEngine.version, "aiohttp": version("aiohttp")},
                    "decoder": {
                        "ffmpeg_version": _decoder_version(ffmpeg),
                        "format": "mp3",
                        "codec": "pcm_s16le",
                        "threads": 1,
                    },
                }
            )
        return identity

    def _native_parameters(self) -> dict:
        return {
            "speed": 1 + int(self.settings.rate[:-1]) / 100,
            "lang": "en-gb" if self.settings.voice.startswith("b") else "en-us",
            "trim": True,
            "sentence_pause": 0.25,
            "clause_pause": 0.1,
            "continuous": False,
        }

    async def synthesize(self, text: str, destination: Path, *, allow_online: bool) -> dict:
        self.settings.validate()
        if not isinstance(text, str) or not text.strip():
            raise InputError("请输入非空英文文本。")
        destination = Path(destination)
        if destination.exists() or destination.is_symlink():
            raise InputError("Raw PCM destination already exists.")
        try:
            async with asyncio.timeout(self.settings.timeout):
                with tempfile.TemporaryDirectory(
                    prefix=".g3-raw-", dir=destination.parent
                ) as temporary:
                    directory = Path(temporary)
                    if self.settings.engine == "kokoro":
                        metadata = await self._kokoro(text, directory)
                    else:
                        metadata = await self._edge(text, directory, allow_online=allow_online)
                    report = validate_pcm(directory / "audio.wav")
                    if report["samples"] != metadata["samples"]:
                        raise ValidationError("Raw PCM differs from native sample metadata.")
                    prepare_subtitles(
                        _boundaries(metadata["boundaries"]),
                        audio_duration_ms=_duration_ms(report["samples"]),
                        expected_text=text,
                        timing_source=metadata["timing_source"],
                    )
                    _copy_new(directory / "audio.wav", destination)
                    return metadata
        except TimeoutError:
            code = "OFFLINE_TIMEOUT" if self.settings.engine == "kokoro" else "ONLINE_TIMEOUT"
            raise StudioError(code, "长文分段合成或 PCM 解码超时。", 4) from None
        except (ValueError, KeyError, TypeError, wave.Error):
            raise ValidationError("长文分段返回不兼容的音频或元数据。") from None
        except OSError:
            raise StudioError("LONGFORM_IO_FAILED", "无法处理长文 PCM 文件或 worker。", 6) from None

    async def _kokoro(self, text: str, directory: Path) -> dict:
        import json

        sentences = split_sentences(text)
        python = self.kokoro._python()
        native = self._native_parameters()
        with model_lease(self.kokoro.model_dir or default_model_dir()) as model:
            assets = model["assets"]
            identity = {
                "id": model["model_id"],
                "version": model["version"],
                "sha256": assets[_MODEL_FILE]["sha256"],
                "voices_sha256": assets[_VOICES_FILE]["sha256"],
            }
            request = {
                "sentences": sentences,
                "voice": self.settings.voice,
                "speed": native["speed"],
                "lang": native["lang"],
                "model_path": str(assets[_MODEL_FILE]["path"]),
                "voices_path": str(assets[_VOICES_FILE]["path"]),
            }
            request_path = directory / "request.json"
            with request_path.open("x", encoding="utf-8") as output:
                os.chmod(request_path, 0o600)
                json.dump(request, output, ensure_ascii=False)
            code = await run_supervised(
                [python, "-B", "-m", "lextide.engines.kokoro_worker", str(request_path)],
                env=_runtime_environment(),
            )
            rate, counts = self.kokoro._read_result(directory, len(sentences))
            if code:
                raise StudioError("OFFLINE_SYNTHESIS_FAILED", "本地 Kokoro worker 未正常完成。", 4)
            if rate != SAMPLE_RATE:
                raise ValidationError("Kokoro raw PCM must have a native sample rate of 24000 Hz.")
        boundaries = []
        position = 0
        for sentence, count in zip(sentences, counts, strict=True):
            start = position * _TICKS_PER_SECOND // rate
            position += count
            end = position * _TICKS_PER_SECOND // rate
            boundaries.append(asdict(SentenceBoundary(start, end - start, sentence)))
        return {
            "sample_rate": rate,
            "samples": sum(counts),
            "channels": 1,
            "boundaries": boundaries,
            "timing_source": "kokoro sentence PCM sample counts",
            "model": identity,
            "effective_settings": {
                **native,
                "sample_rate": rate,
                "samples": sum(counts),
                "pcm_format": "s16le",
                "channels": 1,
            },
        }

    async def _edge(self, text: str, directory: Path, *, allow_online: bool) -> dict:
        ffmpeg, ffprobe = resolve_media_tools(self.ffmpeg, self.ffprobe)
        mp3 = directory / "source.mp3"
        result = await self.edge.synthesize(text, mp3, self.settings, allow_online=allow_online)
        report = validate_audio(mp3, ffmpeg=ffmpeg, ffprobe=ffprobe)
        if (
            result.stream_complete is not True
            or result.audio_sha256 != report["sha256"]
            or result.audio_bytes != report["byte_size"]
        ):
            raise ValidationError("Edge audio differs from the completed native stream.")
        prepare_subtitles(
            result.boundaries,
            audio_duration_ms=report["duration_ms"],
            expected_text=text,
            timing_source=result.timing_source,
        )
        code = await run_supervised(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-xerror",
                "-err_detect",
                "explode",
                "-protocol_whitelist",
                "file,pipe",
                "-n",
                "-f",
                "mp3",
                "-i",
                str(mp3),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-threads",
                "1",
                "-map_metadata",
                "-1",
                "-c:a",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-fs",
                str(SAMPLE_RATE * MAX_CHUNK_SECONDS * 2 + _MAX_WAV_HEADER_BYTES),
                "-f",
                "wav",
                str(directory / "audio.wav"),
            ],
            env=_runtime_environment(),
        )
        if code:
            raise ValidationError("Edge MP3 to raw PCM decoding failed.")
        pcm = validate_pcm(directory / "audio.wav")
        return {
            "sample_rate": SAMPLE_RATE,
            "samples": pcm["samples"],
            "channels": 1,
            "boundaries": [asdict(boundary) for boundary in result.boundaries],
            "timing_source": result.timing_source,
            "model": result.model,
            "effective_settings": {
                **result.effective_settings,
                "sample_rate": SAMPLE_RATE,
                "samples": pcm["samples"],
                "pcm_format": "s16le",
                "channels": 1,
                "decoder": "ffmpeg",
                "native_timing_rescaled": False,
            },
        }


def postprocessing_settings(
    engine_id: str, audio_filter: str, *, saved: dict | None = None
) -> dict:
    """The effective export configuration, shared by identity and encoding."""
    if engine_id == "kokoro":
        profile = saved.get("profile") if isinstance(saved, dict) else "kokoro-hf-v3"
        if profile not in ("kokoro-hf-v1", "kokoro-hf-v2", "kokoro-hf-v3"):
            raise ValidationError("Unsupported saved Kokoro audio processing profile.")
        selected = KokoroEngine(audio_filter=audio_filter)._audio_postprocessing(
            SAMPLE_RATE, profile=profile
        )
    else:
        selected = {
            "enabled": False,
            "profile": None,
            "method": "none",
            "pcm_sample_rate": SAMPLE_RATE,
            "ffmpeg_filter": None,
        }
    if saved is not None and saved != selected:
        raise ValidationError(
            "Saved audio processing settings differ from their versioned profile."
        )
    return selected


async def assemble(
    chunks: list[dict],
    destination: Path,
    *,
    settings: Settings,
    audio_filter: str = "on",
    audio_postprocessing: dict | None = None,
    pause_ms: int = 0,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> EngineResult:
    """Stream raw chunks into one WAV, then run exactly one final MP3 encoder."""
    settings.validate()
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= 2000:
        raise InputError("Assembly requires between 1 and 2000 PCM chunks.")
    if type(pause_ms) is not int or not 0 <= pause_ms <= 10000:
        raise InputError("Inter-chunk pause must be an integer from 0 to 10000 ms.")
    if audio_filter not in {"on", "off"}:
        raise InputError("Kokoro audio filter must be on or off.")
    ffmpeg, ffprobe = resolve_media_tools(ffmpeg, ffprobe)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise InputError("Final MP3 destination already exists.")
    pause_samples = pause_ms * SAMPLE_RATE // 1000
    boundaries: list[SentenceBoundary] = []
    position = 0
    first_metadata = chunks[0].get("metadata", {})
    timing_source = first_metadata.get("timing_source")
    model = first_metadata.get("model")
    if not isinstance(timing_source, str) or not timing_source:
        raise ValidationError("Chunk timing source is missing.")
    expected_parts = []
    timing_adjustments = []
    try:
        with tempfile.TemporaryDirectory(
            prefix=".g3-assemble-", dir=destination.parent
        ) as temporary:
            directory = Path(temporary)
            concatenated = directory / "assembled.wav"
            with wave.open(str(concatenated), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(SAMPLE_RATE)
                for index, chunk in enumerate(chunks):
                    path = Path(chunk["path"])
                    metadata = chunk["metadata"]
                    report = validate_pcm(path)
                    if (
                        report["sha256"] != chunk["artifact_sha256"]
                        or metadata.get("samples") != report["samples"]
                        or metadata.get("sample_rate") != SAMPLE_RATE
                        or metadata.get("channels") != 1
                        or metadata.get("timing_source") != timing_source
                        or metadata.get("model") != model
                    ):
                        raise ValidationError(
                            "Chunk PCM, identity or metadata changed before assembly."
                        )
                    native = _boundaries(metadata["boundaries"])
                    chunk_text = " ".join(boundary.text for boundary in native)
                    _, local_timing = prepare_subtitles(
                        native,
                        audio_duration_ms=_duration_ms(report["samples"]),
                        expected_text=chunk_text,
                        timing_source=timing_source,
                    )
                    derived, corrections = _derive_chunk_boundaries(
                        native, local_timing, samples=report["samples"], chunk_index=index
                    )
                    timing_adjustments.extend(corrections)
                    if index:
                        position += pause_samples
                    if position + report["samples"] > SAMPLE_RATE * MAX_DURATION_SECONDS:
                        raise ValidationError("Final PCM exceeds the two hour duration limit.")
                    if index:
                        remaining = pause_samples * 2
                        while remaining:
                            size = min(remaining, _IO_BYTES)
                            output.writeframesraw(b"\0" * size)
                            remaining -= size
                    offset = position * _TICKS_PER_SECOND // SAMPLE_RATE
                    boundaries.extend(
                        SentenceBoundary(offset + item.offset_ticks, item.duration_ticks, item.text)
                        for item in derived
                    )
                    copied = 0
                    with wave.open(str(path), "rb") as source:
                        while block := source.readframes(_IO_BYTES // 2):
                            copied += len(block) // 2
                            output.writeframesraw(block)
                    if copied != report["samples"] or _hash(path) != report["sha256"]:
                        raise ValidationError("Chunk PCM changed while concatenating.")
                    position += copied
                    expected_parts.append(chunk_text)
            postprocessing = postprocessing_settings(
                settings.engine, audio_filter, saved=audio_postprocessing
            )
            filters = ["-af", postprocessing["ffmpeg_filter"]] if postprocessing["enabled"] else []
            encoded = directory / "audio.mp3"
            try:
                async with asyncio.timeout(600):
                    code = await run_supervised(
                        [
                            ffmpeg,
                            "-nostdin",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-n",
                            "-f",
                            "wav",
                            "-i",
                            str(concatenated),
                            "-map_metadata",
                            "-1",
                            "-vn",
                            *filters,
                            "-c:a",
                            "libmp3lame",
                            "-b:a",
                            "128k",
                            "-ac",
                            "1",
                            "-ar",
                            str(SAMPLE_RATE),
                            "-write_xing",
                            "1",
                            "-id3v2_version",
                            "3",
                            "-fs",
                            str(MAX_MP3_BYTES + 1),
                            "-f",
                            "mp3",
                            str(encoded),
                        ],
                        env=_runtime_environment(),
                    )
            except TimeoutError:
                raise StudioError("LONGFORM_ENCODE_TIMEOUT", "长文 MP3 编码超时。", 4) from None
            if code or not encoded.is_file() or not 0 < encoded.stat().st_size <= MAX_MP3_BYTES:
                raise ValidationError("Final MP3 encoding failed or exceeded 128 MiB.")
            report = validate_export_audio(encoded, ffmpeg=ffmpeg, ffprobe=ffprobe)
            roundtrip = validate_sample_roundtrip(encoded, report, position)
            prepare_export_subtitles(
                boundaries,
                audio_duration_ms=report["duration_ms"],
                expected_text=" ".join(expected_parts),
                timing_source=timing_source,
            )
            _copy_new(encoded, destination)
    except (KeyError, TypeError, ValueError, wave.Error):
        raise ValidationError("Long-form chunk metadata or WAV format is invalid.") from None
    except OSError:
        raise StudioError("LONGFORM_IO_FAILED", "无法写入长文导出音频。", 6) from None
    return EngineResult(
        boundaries,
        True,
        report["byte_size"],
        report["sha256"],
        timing_source=timing_source,
        model=model,
        effective_settings={
            "sample_rate": SAMPLE_RATE,
            "samples": position,
            "sample_roundtrip": roundtrip,
            "channels": 1,
            "pcm_format": "s16le",
            "chunk_count": len(chunks),
            "pause_ms": pause_ms,
            "pause_samples": pause_samples,
            "encoder": "libmp3lame",
            "bitrate": "128k",
            "write_xing": True,
            "audio_postprocessing": postprocessing,
            "chunk_timing_adjustments": timing_adjustments,
            "native_settings": first_metadata.get("effective_settings", {}),
        },
        timing_notes=(
            "Native starts use cumulative PCM samples; accepted local end caps are recorded.",
            "Pause samples occur only between chunks; the final PCM is MP3-encoded once.",
        ),
    )


def validate_export_audio(
    path: Path, *, ffmpeg: str | None = None, ffprobe: str | None = None
) -> dict:
    ffmpeg, ffprobe = resolve_media_tools(ffmpeg, ffprobe)
    return validate_audio(
        path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        max_input_bytes=MAX_MP3_BYTES,
        max_duration_seconds=MAX_DURATION_SECONDS,
        decode_timeout_seconds=600,
    )


def prepare_export_subtitles(
    boundaries: list,
    *,
    audio_duration_ms: int,
    expected_text: str,
    timing_source: str = "edge-tts SentenceBoundary",
) -> tuple[str, dict]:
    return prepare_subtitles(
        _boundaries(boundaries),
        audio_duration_ms=audio_duration_ms,
        expected_text=expected_text,
        timing_source=timing_source,
        max_audio_duration_ms=MAX_DURATION_SECONDS * 1000,
    )


def validate_export_srt(content: str, *, audio_duration_ms: int) -> dict:
    return validate_srt(
        content,
        audio_duration_ms=audio_duration_ms,
        max_audio_duration_ms=MAX_DURATION_SECONDS * 1000,
    )
