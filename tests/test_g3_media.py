"""G3 media invariants using synthetic PCM; no model loading or online calls."""

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

from lextide.audio import inspect_mp3, resolve_media_tools
from lextide.config import Settings
from lextide.engines.base import EngineResult, SentenceBoundary
from lextide.engines.kokoro import _AUDIO_FILTER_V3, _runtime_environment
from lextide.errors import StudioError, ValidationError
from lextide.longform import media
from lextide.longform.process import run_supervised
from lextide.subtitles import prepare_subtitles, validate_srt


def wav_file(path, samples=4801, *, rate=24000, channels=1, silent=False):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        data = (
            b"\0\0" * samples
            if silent
            else b"".join(
                struct.pack("<h", 2000 if index % 60 < 30 else -2000) for index in range(samples)
            )
        )
        output.writeframes(data * channels)
    return path


def chunk(path, text="Hello.", *, engine="kokoro"):
    report = media.validate_pcm(path)
    return {
        "path": path,
        "artifact_sha256": report["sha256"],
        "metadata": {
            "samples": report["samples"],
            "sample_rate": 24000,
            "channels": 1,
            "timing_source": (
                "kokoro sentence PCM sample counts"
                if engine == "kokoro"
                else "edge-tts SentenceBoundary"
            ),
            "model": None,
            "boundaries": [
                {
                    "offset_ticks": 0,
                    "duration_ticks": report["samples"] * 10_000_000 // 24000,
                    "text": text,
                }
            ],
            "effective_settings": {"samples": report["samples"], "sample_rate": 24000},
        },
    }


@pytest.fixture
def media_tools():
    try:
        return resolve_media_tools()
    except StudioError:
        pytest.skip("Local ffmpeg/ffprobe not available for synthetic PCM smoke test")


def test_pcm_reads_all_samples_and_hashes_artifact(tmp_path, monkeypatch):
    path = wav_file(tmp_path / "raw.wav", 100003)
    original = wave.Wave_read.readframes
    reads = []

    def bounded(self, count):
        reads.append(count)
        assert count <= 32768
        return original(self, count)

    monkeypatch.setattr(wave.Wave_read, "readframes", bounded)
    result = media.validate_pcm(path)
    assert result == {
        "sample_rate": 24000,
        "samples": 100003,
        "channels": 1,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": 200050,
    }
    assert len(reads) >= 4


@pytest.mark.parametrize(
    "mutation",
    [
        "silent",
        "empty",
        "truncated",
        "tail",
        "riff_tail",
        "odd_data",
        "stereo",
        "rate",
    ],
)
def test_pcm_rejects_invalid_frames_and_lengths(tmp_path, mutation):
    path = wav_file(
        tmp_path / "raw.wav",
        samples=0 if mutation == "empty" else 101,
        rate=16000 if mutation == "rate" else 24000,
        channels=2 if mutation == "stereo" else 1,
        silent=mutation == "silent",
    )
    raw = bytearray(path.read_bytes())
    if mutation == "truncated":
        raw = raw[:-2]
    elif mutation == "tail":
        raw += b"junk"
    elif mutation == "riff_tail":
        raw += b"junk"
        raw[4:8] = struct.pack("<I", len(raw) - 8)
    elif mutation == "odd_data":
        raw[40:44] = struct.pack("<I", len(raw) - 45)
    path.write_bytes(raw)
    with pytest.raises(ValidationError):
        media.validate_pcm(path)


def test_pcm_rejects_symlink_and_fifo_without_blocking(tmp_path):
    path = wav_file(tmp_path / "raw.wav")
    link = tmp_path / "link.wav"
    link.symlink_to(path)
    fifo = tmp_path / "fifo.wav"
    os.mkfifo(fifo)
    for candidate in (link, fifo):
        with pytest.raises(ValidationError):
            media.validate_pcm(candidate)


def test_pcm_duration_limit_checked_on_actual_samples(tmp_path, monkeypatch):
    path = wav_file(tmp_path / "long.wav", samples=24001)
    monkeypatch.setattr(media, "MAX_CHUNK_SECONDS", 1)
    with pytest.raises(ValidationError):
        media.validate_pcm(path)


def test_kokoro_identity_is_local_pinned_and_excludes_export_controls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Model/runtime execution must not occur during identity")

    monkeypatch.setattr(media, "model_lease", forbidden)
    monkeypatch.setattr(media.subprocess, "run", forbidden)
    settings = Settings(engine="kokoro", voice="af_heart", rate="-4%")
    identity = media.LongEngine(settings).identity()
    assert identity["online"] is False
    assert identity["engine_version"] == "0.6.1"
    assert identity["runtime"]["onnxruntime"] == "1.31.0"
    assert len(identity["model"]["sha256"]) == 64
    assert len(identity["model"]["voices_sha256"]) == 64
    assert identity["native_parameters"]["speed"] == 0.96
    assert identity["native_parameters"]["trim"] is True
    assert identity["raw_pcm_profile"]["sample_rate"] == 24000
    assert "audio_filter" not in identity and "pause_ms" not in identity
    assert (
        media.LongEngine(Settings(engine="kokoro", voice="bf_emma")).identity()[
            "native_parameters"
        ]["lang"]
        == "en-gb"
    )


def test_edge_identity_includes_decoder_version(media_tools):
    ffmpeg, ffprobe = media_tools
    identity = media.LongEngine(Settings(), ffmpeg=ffmpeg, ffprobe=ffprobe).identity()
    assert identity["online"] is True
    assert identity["decoder"]["ffmpeg_version"].startswith("ffmpeg version ")
    assert identity["native_parameters"]["boundary"] == "SentenceBoundary"


def test_kokoro_reuses_raw_worker_contract_without_encoder(tmp_path, monkeypatch):
    settings = Settings(engine="kokoro", voice="af_heart")
    engine = media.LongEngine(settings, offline_python=sys.executable)
    identity = engine.identity()["model"]
    commands = []

    @contextlib.contextmanager
    def lease(_root):
        yield {
            "model_id": identity["id"],
            "version": identity["version"],
            "assets": {
                media._MODEL_FILE: {"path": "model.onnx", "sha256": identity["sha256"]},
                media._VOICES_FILE: {"path": "voices.bin", "sha256": identity["voices_sha256"]},
            },
        }

    async def worker(args, *, env):
        commands.append(args)
        assert args[1:4] == ["-B", "-m", "lextide.engines.kokoro_worker"]
        assert env["HF_HUB_OFFLINE"] == "1"
        request_path = Path(args[-1])
        assert request_path.stat().st_mode & 0o777 == 0o600
        request = json.loads(request_path.read_text())
        assert request == {
            "sentences": ["Hello. ", "Again."],
            "voice": "af_heart",
            "speed": 0.96,
            "lang": "en-us",
            "model_path": "model.onnx",
            "voices_path": "voices.bin",
        }
        wav_file(request_path.parent / "audio.wav", 9602)
        (request_path.parent / "result.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "sample_rate": 24000,
                    "sample_counts": [4801, 4801],
                    "total_samples": 9602,
                }
            )
        )
        return 0

    monkeypatch.setattr(media, "model_lease", lease)
    monkeypatch.setattr(media, "run_supervised", worker)
    output = tmp_path / "output.wav"
    result = asyncio.run(engine.synthesize("Hello. Again.", output, allow_online=False))
    assert len(commands) == 1
    assert result["samples"] == media.validate_pcm(output)["samples"] == 9602
    assert result["boundaries"][1]["offset_ticks"] == 4801 * 10_000_000 // 24000
    assert "audio_postprocessing" not in result["effective_settings"]
    assert result["model"] == identity
    assert sorted(item.name for item in tmp_path.iterdir()) == ["output.wav"]


def test_edge_requires_fresh_online_authorization(tmp_path, media_tools):
    ffmpeg, ffprobe = media_tools
    engine = media.LongEngine(Settings(), ffmpeg=ffmpeg, ffprobe=ffprobe)
    with pytest.raises(StudioError) as failure:
        asyncio.run(engine.synthesize("Hello.", tmp_path / "raw.wav", allow_online=False))
    assert failure.value.code == "ONLINE_NOT_AUTHORIZED"
    assert list(tmp_path.iterdir()) == []


def test_fake_edge_preserves_native_timings_during_real_pcm_decode(
    tmp_path, media_tools, monkeypatch
):
    ffmpeg, ffprobe = media_tools
    source = wav_file(tmp_path / "source.wav", 4801)
    mp3 = tmp_path / "source.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(mp3),
        ],
        check=True,
    )
    native = [SentenceBoundary(123, 1_900_001, "Hello.")]

    async def edge_synthesize(text, destination, settings, *, allow_online):
        assert allow_online
        raw = mp3.read_bytes()
        destination.write_bytes(raw)
        return EngineResult(native, True, len(raw), hashlib.sha256(raw).hexdigest())

    engine = media.LongEngine(Settings(), ffmpeg=ffmpeg, ffprobe=ffprobe)
    monkeypatch.setattr(engine.edge, "synthesize", edge_synthesize)
    result = asyncio.run(engine.synthesize("Hello.", tmp_path / "raw.wav", allow_online=True))
    assert result["samples"] == 4801
    assert result["boundaries"] == [
        {
            "offset_ticks": 123,
            "duration_ticks": 1_900_001,
            "text": "Hello.",
        }
    ]
    assert result["effective_settings"]["native_timing_rescaled"] is False


@pytest.mark.parametrize("engine,filter_enabled", [("kokoro", True), ("edge-tts", False)])
def test_single_final_encode_preserves_exact_samples_filter_and_offsets(
    tmp_path, media_tools, monkeypatch, engine, filter_enabled
):
    ffmpeg, ffprobe = media_tools
    chunks = [
        chunk(wav_file(tmp_path / "one.wav", 4801), "Hello.", engine=engine),
        chunk(wav_file(tmp_path / "two.wav", 4807), "Again.", engine=engine),
        chunk(wav_file(tmp_path / "three.wav", 4813), "End.", engine=engine),
    ]
    commands = []

    async def capture(args, **kwargs):
        commands.append(args)
        return await run_supervised(args, **kwargs)

    monkeypatch.setattr(media, "run_supervised", capture)
    settings = Settings(
        engine=engine, voice="af_heart" if engine == "kokoro" else "en-US-GuyNeural"
    )
    path = tmp_path / "final.mp3"
    result = asyncio.run(
        media.assemble(
            chunks,
            path,
            settings=settings,
            pause_ms=7,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
    )
    total = 4801 + 4807 + 4813 + 2 * 168
    assert result.effective_settings["samples"] == total
    assert len(commands) == 1 and commands[0].count("libmp3lame") == 1
    assert ("-af" in commands[0]) is filter_enabled
    if filter_enabled:
        assert commands[0][commands[0].index("-af") + 1] == _AUDIO_FILTER_V3
    assert result.boundaries[1].offset_ticks == (4801 + 168) * 10_000_000 // 24000
    assert result.boundaries[2].offset_ticks == (4801 + 4807 + 336) * 10_000_000 // 24000
    report = media.validate_export_audio(path, ffmpeg=ffmpeg, ffprobe=ffprobe)
    assert report["decoded_samples_per_channel"] == total
    srt, subtitle_report = media.prepare_export_subtitles(
        result.boundaries,
        audio_duration_ms=report["duration_ms"],
        expected_text="Hello. Again. End.",
        timing_source=result.timing_source,
    )
    assert subtitle_report["text_coverage"]["verified"]
    assert media.validate_export_srt(srt, audio_duration_ms=report["duration_ms"])["valid"]
    assert not list(tmp_path.glob(".g3-assemble-*"))


def test_assembly_rejects_changed_chunk_before_encoding(tmp_path, media_tools, monkeypatch):
    ffmpeg, ffprobe = media_tools
    one = chunk(wav_file(tmp_path / "raw.wav"))
    one["artifact_sha256"] = "0" * 64

    async def forbidden(*args, **kwargs):
        raise AssertionError("Corrupt PCM must not reach encoding")

    monkeypatch.setattr(media, "run_supervised", forbidden)
    with pytest.raises(ValidationError):
        asyncio.run(
            media.assemble(
                [one],
                tmp_path / "final.mp3",
                settings=Settings(engine="kokoro", voice="af_heart"),
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
            )
        )
    assert not (tmp_path / "final.mp3").exists()


def test_assembly_derives_accepted_edge_end_caps_at_fractional_pcm_boundary(tmp_path, media_tools):
    ffmpeg, ffprobe = media_tools
    first = chunk(wav_file(tmp_path / "one.wav", 4812), "One.", engine="edge-tts")
    # 200.5ms rounds to 201ms for local validation, allowing this exact 100ms cap.
    first["metadata"]["boundaries"][0]["duration_ticks"] = 3_010_000
    native_before = json.loads(json.dumps(first["metadata"]))
    second = chunk(wav_file(tmp_path / "two.wav", 4800), "Two.", engine="edge-tts")
    output = tmp_path / "final.mp3"
    result = asyncio.run(
        media.assemble(
            [first, second],
            output,
            settings=Settings(),
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
    )
    assert first["metadata"] == native_before
    assert result.boundaries[0].offset_ticks == 0
    assert result.boundaries[0].duration_ticks == 2_005_000
    assert result.boundaries[1].offset_ticks == 2_005_000
    assert (
        result.boundaries[1].duration_ticks == second["metadata"]["boundaries"][0]["duration_ticks"]
    )
    adjustments = result.effective_settings["chunk_timing_adjustments"]
    assert [item["kind"] for item in adjustments] == ["cap_audio_end", "cap_pcm_end"]
    assert all(item["native_end_ticks"] == 3_010_000 for item in adjustments)
    assert adjustments[0]["derived_end_ticks"] == 2_010_000
    assert adjustments[1]["derived_end_ticks"] == 2_005_000
    assert adjustments[0]["local_validation_adjustment"]["delta_ms"] == -100
    report = media.validate_export_audio(output, ffmpeg=ffmpeg, ffprobe=ffprobe)
    assert report["decoded_samples_per_channel"] == 9612
    _, subtitles = media.prepare_export_subtitles(
        result.boundaries, audio_duration_ms=report["duration_ms"], expected_text="One. Two."
    )
    assert subtitles["valid"] is True
    assert max(abs(item["delta_ms"]) for item in subtitles["adjustments"]) == 1


def test_assembly_duration_bound_before_encoding(tmp_path, media_tools, monkeypatch):
    ffmpeg, ffprobe = media_tools
    one = chunk(wav_file(tmp_path / "raw.wav", 16000))
    monkeypatch.setattr(media, "MAX_DURATION_SECONDS", 1)
    with pytest.raises(ValidationError, match="two hour"):
        asyncio.run(
            media.assemble(
                [one, one],
                tmp_path / "final.mp3",
                settings=Settings(engine="kokoro", voice="af_heart"),
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
            )
        )
    assert not (tmp_path / "final.mp3").exists()


def test_long_subtitle_limits_are_opt_in_and_preserve_alignment_checks():
    boundaries = [SentenceBoundary(0, 7_000_000_000, "Long.")]
    with pytest.raises(ValidationError):
        prepare_subtitles(boundaries, audio_duration_ms=700000, expected_text="Long.")
    content, report = media.prepare_export_subtitles(
        boundaries, audio_duration_ms=700000, expected_text="Long."
    )
    assert report["limits"]["max_audio_duration_ms"] == 7200000
    with pytest.raises(ValidationError):
        validate_srt(content, audio_duration_ms=700000)
    assert media.validate_export_srt(content, audio_duration_ms=700000)["valid"]
    with pytest.raises(ValidationError):
        media.prepare_export_subtitles(boundaries, audio_duration_ms=704000, expected_text="Long.")
    with pytest.raises(ValidationError):
        media.prepare_export_subtitles(boundaries, audio_duration_ms=700000, expected_text="Wrong.")


def test_long_mp3_structural_limits_are_opt_in():
    # 128 kb/s MPEG1, 44.1 kHz, one 417-byte frame; duration 26.12ms.
    frame = bytes.fromhex("fffb9000") + bytes(413)
    data = frame * 23000
    with pytest.raises(ValidationError, match="10 minute"):
        inspect_mp3(data)
    assert inspect_mp3(data, max_duration_seconds=7200)["frames"] == 23000


def _wait_pid_file(path, *, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return int(path.read_text())
        time.sleep(0.02)
    raise AssertionError("Synthetic child did not start")


def _wait_child_gone(pid, *, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    raise AssertionError("Supervised synthetic child was not killed and reaped")


def test_supervisor_cancellation_reaps_synthetic_child(tmp_path):
    pid_file = tmp_path / "child.pid"
    script = "import os,sys,time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(60)"

    async def scenario():
        task = asyncio.create_task(
            run_supervised(
                [sys.executable, "-c", script, str(pid_file)], env=_runtime_environment()
            )
        )
        for _ in range(250):
            if pid_file.exists() and pid_file.read_text():
                break
            await asyncio.sleep(0.02)
        assert pid_file.exists()
        child_pid = int(pid_file.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        _wait_child_gone(child_pid)

    asyncio.run(scenario())


def test_supervisor_parent_sigkill_reaps_synthetic_child(tmp_path):
    pid_file = tmp_path / "child.pid"
    child_script = (
        "import os,sys,time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(60)"
    )
    parent_script = (
        "import asyncio,sys; from lextide.longform.process import run_supervised; "
        "asyncio.run(run_supervised([sys.executable, '-c', sys.argv[1], sys.argv[2]]))"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_script, child_script, str(pid_file)],
        env=_runtime_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid = None
    try:
        child_pid = _wait_pid_file(pid_file)
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        _wait_child_gone(child_pid)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
