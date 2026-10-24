"""V3 production encoding on synthetic PCM; no model inference or listening."""

import asyncio
import json
import subprocess

import pytest
from test_g3_media import media_tools as _media_tools
from test_g3_media import wav_file
from test_kokoro import fake_runtime as _fake_runtime
from test_kokoro import write_pcm_result

from lextide.config import Settings
from lextide.engines import kokoro
from lextide.errors import ValidationError
from lextide.jobs import synthesize_job, verify_job
from lextide.longform.media import postprocessing_settings
from lextide.text import prepare_text

media_tools = _media_tools
fake_runtime = _fake_runtime
FILTER_V3 = (
    "bandreject=frequency=9600:width_type=h:width=2800:blocksize=0,"
    "bandreject=frequency=4800:width_type=h:width=60:blocksize=0,"
    "lowpass=frequency=10000:poles=2:width_type=q:width=0.707:blocksize=0"
)


@pytest.mark.parametrize("mode", [None, "on", "off"])
def test_short_production_encoder_and_manifest_on_synthetic_pcm(
    mode, fake_runtime, media_tools, monkeypatch, tmp_path
):
    engine, _ = fake_runtime
    ffmpeg, ffprobe = media_tools
    monkeypatch.setattr(kokoro, "resolve_media_tools", lambda *args: (ffmpeg, ffprobe))
    if mode is not None:
        engine.audio_filter = mode
    real_run = engine._run_process
    commands = []

    async def run(arguments, *, env=None):
        commands.append(arguments)
        if "lextide.engines.kokoro_worker" in arguments:
            from pathlib import Path

            directory = Path(arguments[-1]).parent
            write_pcm_result(directory)  # Actual counts: 24001 + 12001 samples.
            wav_file(directory / "audio.wav", 36002)
            return 0
        return await real_run(arguments, env=env)

    monkeypatch.setattr(engine, "_run_process", run)
    prepared = prepare_text(text="One sentence. Another sentence.")
    output = asyncio.run(
        synthesize_job(
            prepared,
            Settings(voice="af_heart", engine="kokoro"),
            tmp_path / "jobs",
            engine,
            allow_online=False,
            confirmed_sha256=prepared.sha256,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
    )
    manifest = json.loads((output / "manifest.json").read_text())
    post = manifest["effective_settings"]["audio_postprocessing"]
    assert post["profile"] == "kokoro-hf-v3" and post["config_version"] == 3
    assert post == kokoro.KokoroEngine(audio_filter=mode or "on")._audio_postprocessing(24000)
    assert manifest["effective_settings"]["samples"] == 36002
    assert len(commands) == 2 and commands[-1].count("libmp3lame") == 1
    assert commands[-1][commands[-1].index("-i") + 1].endswith("audio.wav")
    assert ("-af" in commands[-1]) == (mode != "off")
    if mode != "off":
        assert commands[-1][commands[-1].index("-af") + 1] == FILTER_V3
    report = verify_job(output, ffmpeg=ffmpeg, ffprobe=ffprobe)
    assert report["status"] == "valid" and report["audio"]["decoder_ok"]
    assert report["audio"]["decoded_samples_per_channel"] == 36002
    boundaries = json.loads((output / "sentences.json").read_text())
    assert [b["offset_ticks"] for b in boundaries] == [0, 24001 * 10_000_000 // 24000]


@pytest.mark.parametrize("mode", ["on", "off"])
def test_real_filter_preserves_pcm_sample_count(mode, media_tools, tmp_path):
    ffmpeg, _ = media_tools
    source = wav_file(tmp_path / "input.wav", 48001)
    output = tmp_path / "filtered.s16le"
    post = kokoro.KokoroEngine(audio_filter=mode)._audio_postprocessing(24000)
    filters = ["-af", post["ffmpeg_filter"]] if post["enabled"] else []
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-n",
            "-i",
            str(source),
            *filters,
            "-acodec",
            "pcm_s16le",
            "-f",
            "s16le",
            str(output),
        ],
        check=True,
        timeout=10,
        capture_output=True,
    )
    assert output.stat().st_size == 48001 * 2


def test_v3_snapshot_requires_both_notches_and_config_version():
    for mutation in ("missing", "width", "config_version"):
        saved = kokoro.KokoroEngine()._audio_postprocessing(24000)
        if mutation == "missing":
            saved.pop("bandreject_secondary")
        elif mutation == "width":
            saved["bandreject_secondary"]["width"] = 100
        else:
            saved["config_version"] = 2
        with pytest.raises(ValidationError):
            postprocessing_settings("kokoro", "on", saved=saved)
