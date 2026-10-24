"""Offline Kokoro adapter with an isolated, cancellable optional Python runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from ..audio import resolve_media_tools
from ..config import MAX_AUDIO_BYTES, Settings
from ..errors import InputError, StudioError, ValidationError
from ..models import default_model_dir, model_lease, model_status
from ..runtime import bundled_source_root, external_runtime_environment, is_frozen
from .base import EngineCapabilities, EngineResult, SentenceBoundary
from .sentence_split import split_sentences

_TIMING = "kokoro sentence PCM sample counts"
_MODEL_FILE = "kokoro-v1.0.int8.onnx"
_VOICES_FILE = "voices-v1.0.bin"
_TICKS_PER_SECOND = 10_000_000
_AUDIO_FILTER = (
    "bandreject=frequency=9600:width_type=h:width=2800:blocksize=0,"
    "lowpass=frequency=10000:poles=2:width_type=q:width=0.707:blocksize=0"
)
# Preserve the exact v1 chain for resuming tasks that already recorded that profile.
_LEGACY_AUDIO_FILTER = (
    "bandreject=frequency=9600:width_type=h:width=1400:blocksize=0,"
    "lowpass=frequency=10500:poles=2:width_type=q:width=0.707:blocksize=0"
)
_AUDIO_FILTER_V3 = (
    "bandreject=frequency=9600:width_type=h:width=2800:blocksize=0,"
    "bandreject=frequency=4800:width_type=h:width=60:blocksize=0,"
    "lowpass=frequency=10000:poles=2:width_type=q:width=0.707:blocksize=0"
)


def _runtime_environment() -> dict[str, str]:
    environment = external_runtime_environment()
    environment.update(
        {
            "PYTHONPATH": str(bundled_source_root()),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
    )
    for key in ("PHONEMIZER_ESPEAK_LIBRARY", "ESPEAK_DATA_PATH"):
        environment.pop(key, None)
    return environment


def _runtime_missing() -> StudioError:
    return StudioError(
        "OFFLINE_RUNTIME_UNAVAILABLE",
        "Kokoro 独立 Python/runtime 未就绪；请先按 offline-runtime 说明准备运行环境。",
        7,
    )


class KokoroEngine:
    engine_id = "kokoro"
    version = "0.6.1"
    online = False

    def __init__(
        self,
        model_dir: Path | None = None,
        offline_python: str | None = None,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
        audio_filter: str = "on",
    ):
        # Construction neither checks dependencies nor loads/downloads models.
        if audio_filter not in ("on", "off"):
            raise InputError("Kokoro audio filter 必须为 on 或 off。")
        self.model_dir = model_dir
        self.offline_python = offline_python
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.audio_filter = audio_filter

    def _audio_postprocessing(self, sample_rate: int, *, profile: str = "kokoro-hf-v3") -> dict:
        """Describe only the processing actually selected for the original PCM rate."""
        if profile == "kokoro-hf-v3":
            band_width, lowpass_frequency, filter_chain = 2800, 10000, _AUDIO_FILTER_V3
        elif profile == "kokoro-hf-v2":
            band_width, lowpass_frequency, filter_chain = 2800, 10000, _AUDIO_FILTER
        elif profile == "kokoro-hf-v1":
            band_width, lowpass_frequency, filter_chain = 1400, 10500, _LEGACY_AUDIO_FILTER
        else:
            raise InputError("Kokoro audio filter profile 必须为 kokoro-hf-v1、v2 或 v3。")
        enabled = self.audio_filter == "on"
        report = {
            "enabled": enabled,
            "profile": profile,
            "method": "ffmpeg-pcm-biquad" if enabled else "none",
            "pcm_sample_rate": sample_rate,
            "bandreject": None,
            "lowpass": None,
            "ffmpeg_filter": None,
        }
        # Extend only v3; v1/v2 persisted snapshots must remain exactly unchanged.
        if profile == "kokoro-hf-v3":
            report.update({"config_version": 3, "bandreject_secondary": None})
        if enabled:
            # The cutoff and the full reject band must both lie strictly below Nyquist.
            minimum_rate = max(lowpass_frequency * 2, 9600 * 2 + band_width)
            if sample_rate <= minimum_rate:
                raise StudioError(
                    "KOKORO_FILTER_SAMPLE_RATE",
                    f"Kokoro 高频滤波要求 PCM 采样率高于 {minimum_rate} Hz；"
                    "可显式 --kokoro-audio-filter off，程序不会自动重采样或关闭滤波。",
                    5,
                )
            report.update(
                {
                    "bandreject": {
                        "frequency_hz": 9600,
                        "width": band_width,
                        "width_unit": "Hz",
                        "width_type": "h",
                        "blocksize": 0,
                    },
                    "lowpass": {
                        "frequency_hz": lowpass_frequency,
                        "poles": 2,
                        "width_type": "q",
                        "width": 0.707,
                        "blocksize": 0,
                    },
                    "ffmpeg_filter": filter_chain,
                }
            )
            if profile == "kokoro-hf-v3":
                # This 4770–4830 Hz band is inside the existing Nyquist bound.
                report["bandreject_secondary"] = {
                    "frequency_hz": 4800,
                    "width": 60,
                    "width_unit": "Hz",
                    "width_type": "h",
                    "blocksize": 0,
                }
        return report

    def _python(self) -> str:
        requested = self.offline_python or os.environ.get("LEXTIDE_OFFLINE_PYTHON")
        if not requested:
            if is_frozen():
                raise _runtime_missing()
            requested = str(
                Path(__file__).resolve().parents[3] / "offline-runtime/.venv/bin/python"
            )
        located = shutil.which(requested)
        if located is None or not Path(located).is_file():
            raise _runtime_missing()
        if is_frozen() and Path(located).resolve() == Path(sys.executable).resolve():
            raise _runtime_missing()
        # Preserve the venv executable path: resolving its symlink discards the venv.
        return os.path.abspath(located)

    def _runtime_status(self) -> dict:
        try:
            completed = subprocess.run(
                [self._python(), "-B", "-m", "lextide.engines.kokoro_worker", "--check"],
                env=_runtime_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if completed.returncode or len(completed.stdout) > 8192:
                return {"ready": False}
            result = json.loads(completed.stdout)
            return result if isinstance(result, dict) else {"ready": False}
        except (OSError, subprocess.TimeoutExpired, ValueError, StudioError):
            return {"ready": False}

    def capabilities(self) -> EngineCapabilities:
        runtime_ready = self._runtime_status().get("ready") is True
        installed = False
        identity = None
        try:
            status = model_status(self.model_dir or default_model_dir())
            installed = status["installed"] and all(
                asset["present"] and asset["actual_size"] == asset["expected_size"]
                for asset in status["assets"]
            )
            identity = {"id": status["model_id"], "version": status["version"]}
        except StudioError:
            pass
        return EngineCapabilities(
            self.engine_id,
            self.version,
            False,
            installed,
            runtime_ready and installed,
            ("af_heart",),
            timing_source=_TIMING,
            model=identity,
            notes=(
                "available 仅表示独立 runtime 元数据和模型文件/尺寸满足静态启动条件。",
                "未加载模型、未验证推理；完整 SHA256 在每次合成前校验。",
                "af_heart 为默认英文声线；其他 af/am/bf/bm 声线在模型内核实。",
                "按句生成 PCM 后拼接，时间来自样本计数；不提供词级对齐。",
                f"runtime_metadata_ready={str(runtime_ready).lower()}",
            ),
        )

    async def _run_process(self, arguments: list[str], *, env: dict | None = None) -> int:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return await process.wait()
        except BaseException:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            raise

    @staticmethod
    def _read_result(directory: Path, sentence_count: int) -> tuple[int, list[int]]:
        path = directory / "result.json"
        if not path.is_file() or path.stat().st_size > 65536:
            raise ValidationError("Kokoro 未返回有效的句级样本元数据。")
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValidationError("Kokoro 返回的样本元数据格式无效。")
        if result.get("ok") is not True:
            code = result.get("code")
            if code == "OFFLINE_VOICE_UNAVAILABLE":
                raise InputError("本地 Kokoro 模型未提供所选英文 voice。")
            if code == "OFFLINE_DEPENDENCY_MISSING":
                raise _runtime_missing()
            if code == "OFFLINE_MODEL_LOAD_FAILED":
                raise StudioError(code, "本地 Kokoro 模型加载失败；检查 runtime 与模型兼容性。", 7)
            if code == "OFFLINE_IO_FAILED":
                raise StudioError(code, "本地 Kokoro 音频写入失败；检查磁盘空间与权限。", 6)
            if code == "OFFLINE_AUDIO_INVALID":
                raise ValidationError("本地 Kokoro 返回无效音频样本。")
            raise StudioError("OFFLINE_SYNTHESIS_FAILED", "本地 Kokoro 推理失败。", 4)
        rate = result.get("sample_rate")
        counts = result.get("sample_counts")
        if (
            type(rate) is not int
            or not 8000 <= rate <= 48000
            or not isinstance(counts, list)
            or len(counts) != sentence_count
            or not all(type(count) is int and count > 0 for count in counts)
            or sum(counts) > rate * 600
            or result.get("total_samples") != sum(counts)
        ):
            raise ValidationError("Kokoro 返回无效或超限的句级样本数。")
        wav = directory / "audio.wav"
        if not wav.is_file() or wav.stat().st_size != 44 + sum(counts) * 2:
            raise ValidationError("Kokoro PCM 文件长度与样本计数不符。")
        with wave.open(str(wav), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != rate
                or audio.getnframes() != sum(counts)
                or audio.getcomptype() != "NONE"
            ):
                raise ValidationError("Kokoro PCM 格式与句级元数据不符。")
        return rate, counts

    async def synthesize(
        self,
        text: str,
        destination: Path,
        settings: Settings,
        *,
        allow_online: bool,
    ) -> EngineResult:
        settings.validate()
        if settings.engine != "kokoro":
            raise InputError("Kokoro adapter 需要 engine=kokoro 的设置。")
        sentences = split_sentences(text)
        if not sentences:
            raise InputError("请输入非空英文文本。")
        python = self._python()
        ffmpeg, _ = resolve_media_tools(self.ffmpeg, self.ffprobe)
        speed = 1 + int(settings.rate[:-1]) / 100
        lang = "en-gb" if settings.voice.startswith("b") else "en-us"
        created_destination = False
        try:
            async with asyncio.timeout(settings.timeout):
                # The lease verifies hashes and prevents concurrent uninstall through inference.
                with model_lease(self.model_dir or default_model_dir()) as model:
                    assets = model["assets"]
                    identity = {
                        "id": model["model_id"],
                        "version": model["version"],
                        "sha256": assets[_MODEL_FILE]["sha256"],
                        "voices_sha256": assets[_VOICES_FILE]["sha256"],
                    }
                    with tempfile.TemporaryDirectory(
                        prefix=".kokoro-",
                        dir=destination.parent,
                    ) as temporary:
                        directory = Path(temporary)
                        request = {
                            "sentences": sentences,
                            "voice": settings.voice,
                            "speed": speed,
                            "lang": lang,
                            "model_path": str(assets[_MODEL_FILE]["path"]),
                            "voices_path": str(assets[_VOICES_FILE]["path"]),
                        }
                        request_path = directory / "request.json"
                        with request_path.open("x", encoding="utf-8") as stream:
                            os.chmod(request_path, 0o600)
                            json.dump(request, stream, ensure_ascii=False)
                        worker_code = await self._run_process(
                            [
                                python,
                                "-B",
                                "-m",
                                "lextide.engines.kokoro_worker",
                                str(request_path),
                            ],
                            env=_runtime_environment(),
                        )
                        rate, counts = self._read_result(directory, len(sentences))
                        if worker_code:
                            raise StudioError(
                                "OFFLINE_SYNTHESIS_FAILED",
                                "本地 Kokoro worker 未正常完成。",
                                4,
                            )
                        postprocessing = self._audio_postprocessing(rate)
                        # Filter the concatenated WAV's PCM inside the existing encoder pass.
                        # blocksize=0 uses causal IIR filtering, with no reverse-IIR block delay.
                        filter_args = (
                            ["-af", postprocessing["ffmpeg_filter"]]
                            if postprocessing["enabled"]
                            else []
                        )
                        encoded = directory / "audio.mp3"
                        code = await self._run_process(
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
                                str(directory / "audio.wav"),
                                "-map_metadata",
                                "-1",
                                "-vn",
                                *filter_args,
                                "-c:a",
                                "libmp3lame",
                                "-b:a",
                                "128k",
                                "-ac",
                                "1",
                                "-ar",
                                str(rate),
                                "-write_xing",
                                "1",
                                "-id3v2_version",
                                "3",
                                "-f",
                                "mp3",
                                str(encoded),
                            ]
                        )
                        if code or not encoded.is_file():
                            raise ValidationError("本地 PCM 到 MP3 编码失败。")
                        byte_count = encoded.stat().st_size
                        if not 0 < byte_count <= MAX_AUDIO_BYTES:
                            raise ValidationError("Kokoro 音频为空或超过 32 MiB 上限。")
                        digest = hashlib.sha256()
                        with encoded.open("rb") as source, destination.open("xb") as target:
                            created_destination = True
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                target.write(chunk)
                                digest.update(chunk)
                            target.flush()
                            os.fsync(target.fileno())
        except TimeoutError:
            raise StudioError("OFFLINE_TIMEOUT", "本地 Kokoro 合成或编码超时。", 4) from None
        except (ValueError, KeyError, TypeError, wave.Error):
            raise ValidationError("Kokoro 返回不兼容的音频或元数据。") from None
        except OSError:
            if created_destination:
                destination.unlink(missing_ok=True)
            raise StudioError(
                "OFFLINE_IO_FAILED",
                "无法运行离线 worker 或写入本地音频。",
                6,
            ) from None
        boundaries = []
        position = 0
        for sentence, count in zip(sentences, counts, strict=True):
            start = position * _TICKS_PER_SECOND // rate
            position += count
            end = position * _TICKS_PER_SECOND // rate
            boundaries.append(SentenceBoundary(start, end - start, sentence))
        return EngineResult(
            boundaries,
            True,
            byte_count,
            digest.hexdigest(),
            timing_source=_TIMING,
            model=identity,
            effective_settings={
                "speed": speed,
                "lang": lang,
                "sample_rate": rate,
                "samples": sum(counts),
                "pcm_format": "s16le",
                "channels": 1,
                "encoder": "libmp3lame",
                "bitrate": "128k",
                "write_xing": True,
                "trim": True,
                "sentence_pause": 0.25,
                "clause_pause": 0.1,
                "continuous": False,
                "audio_postprocessing": postprocessing,
            },
            timing_notes=(
                "句边界由各句生成 PCM 的实际样本数累加获得，不使用 phoneme/词级时间戳。",
                "PCM 仅拼接后编码一次；MP3 encoder delay/padding 由 Xing/LAME 信息表示。",
            ),
        )
