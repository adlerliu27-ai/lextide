"""BUG-1 contract tests: synthetic worker/encoder, never real Kokoro inference."""

import asyncio
import contextlib
import json
import sys
import wave
from pathlib import Path

import pytest

from lextide.cli import main
from lextide.config import Settings
from lextide.engines import kokoro
from lextide.errors import InputError, StudioError, ValidationError
from lextide.jobs import synthesize_job, verify_job
from lextide.text import prepare_text


@pytest.fixture
def pipeline(monkeypatch):
    state = {"calls": [], "rate": 24000}

    @contextlib.contextmanager
    def lease(root):
        yield {
            "model_id": "fake",
            "version": "1",
            "assets": {
                kokoro._MODEL_FILE: {"path": "fake.onnx", "sha256": "a" * 64},
                kokoro._VOICES_FILE: {"path": "fake.bin", "sha256": "b" * 64},
            },
        }

    async def run(self, args, **kwargs):
        state["calls"].append(args)
        destination = Path(args[-1])
        if "lextide.engines.kokoro_worker" in args:
            count = state["rate"]
            with wave.open(str(destination.parent / "audio.wav"), "wb") as audio:
                audio.setparams((1, 2, state["rate"], 0, "NONE", "not compressed"))
                audio.writeframes(b"\x01\x00" * count * 2)
            (destination.parent / "result.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "sample_rate": state["rate"],
                        "sample_counts": [count, count],
                        "total_samples": count * 2,
                    }
                )
            )
        else:
            assert args[args.index("-i") + 1].endswith("audio.wav")
            assert args.count("libmp3lame") == 1
            destination.write_bytes(b"fake-mp3-on" if "-af" in args else b"fake-mp3-off")
        return 0

    monkeypatch.setattr(kokoro, "model_lease", lease)
    monkeypatch.setattr(kokoro.KokoroEngine, "_run_process", run)
    monkeypatch.setattr(kokoro, "resolve_media_tools", lambda *a: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr("lextide.jobs.resolve_media_tools", lambda *a: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr("lextide.jobs.validate_audio", lambda *a, **kw: {"duration_ms": 2000})
    return state


def make_job(tmp_path, **kwargs):
    prepared = prepare_text(text="One sentence. Another sentence.")
    return asyncio.run(
        synthesize_job(
            prepared,
            Settings(voice="af_heart", engine="kokoro"),
            tmp_path,
            kokoro.KokoroEngine(offline_python=sys.executable, **kwargs),
            allow_online=False,
            confirmed_sha256=prepared.sha256,
        )
    )


@pytest.mark.parametrize("mode", [None, "on", "off"])
def test_pcm_filter_single_encode_and_manifest(mode, pipeline, tmp_path):
    output = make_job(tmp_path, **({} if mode is None else {"audio_filter": mode}))
    manifest = json.loads((output / "manifest.json").read_text())
    post = manifest["effective_settings"]["audio_postprocessing"]
    enabled = mode != "off"
    assert post["enabled"] is enabled
    assert post["profile"] == "kokoro-hf-v3"
    assert post["config_version"] == 3
    assert post["pcm_sample_rate"] == 24000
    assert len(pipeline["calls"]) == 2  # Exactly one worker then one WAV -> MP3 encoder.
    encoder = pipeline["calls"][-1]
    if enabled:
        chain = encoder[encoder.index("-af") + 1]
        assert chain == post["ffmpeg_filter"]
        assert chain == (
            "bandreject=frequency=9600:width_type=h:width=2800:blocksize=0,"
            "bandreject=frequency=4800:width_type=h:width=60:blocksize=0,"
            "lowpass=frequency=10000:poles=2:width_type=q:width=0.707:blocksize=0"
        )
        assert post["bandreject"]["width_unit"] == "Hz"
        assert post["bandreject"]["width"] == 2800
        assert post["bandreject_secondary"] == {
            "frequency_hz": 4800,
            "width": 60,
            "width_unit": "Hz",
            "width_type": "h",
            "blocksize": 0,
        }
        assert post["lowpass"]["frequency_hz"] == 10000
        assert post["lowpass"]["poles"] == 2
    else:
        assert "-af" not in encoder
        assert post["ffmpeg_filter"] is None
        assert post["bandreject"] is None and post["lowpass"] is None
        assert post["bandreject_secondary"] is None
    assert manifest["schema_version"] == 2
    boundaries = json.loads((output / "sentences.json").read_text())
    assert [(b["offset_ticks"], b["duration_ticks"]) for b in boundaries] == [
        (0, 10_000_000),
        (10_000_000, 10_000_000),
    ]
    assert verify_job(output)["hashes_match"]


@pytest.mark.parametrize("sample_rate", [16000, 21000, 21999, 22000])
def test_invalid_nyquist_fails_before_encoder_without_job(sample_rate, pipeline, tmp_path):
    pipeline["rate"] = sample_rate
    with pytest.raises(StudioError) as caught:
        make_job(tmp_path)
    assert caught.value.code == "KOKORO_FILTER_SAMPLE_RATE"
    assert caught.value.exit_code != 0
    assert len(pipeline["calls"]) == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "profile,width,cutoff",
    [
        ("kokoro-hf-v1", 1400, 10500),
        ("kokoro-hf-v2", 2800, 10000),
    ],
)
def test_legacy_profile_preserves_filter_and_report(profile, width, cutoff):
    post = kokoro.KokoroEngine()._audio_postprocessing(24000, profile=profile)
    assert post == {
        "enabled": True,
        "profile": profile,
        "method": "ffmpeg-pcm-biquad",
        "pcm_sample_rate": 24000,
        "bandreject": {
            "frequency_hz": 9600,
            "width": width,
            "width_unit": "Hz",
            "width_type": "h",
            "blocksize": 0,
        },
        "lowpass": {
            "frequency_hz": cutoff,
            "poles": 2,
            "width_type": "q",
            "width": 0.707,
            "blocksize": 0,
        },
        "ffmpeg_filter": (
            f"bandreject=frequency=9600:width_type=h:width={width}:blocksize=0,"
            f"lowpass=frequency={cutoff}:poles=2:width_type=q:width=0.707:blocksize=0"
        ),
    }


@pytest.mark.parametrize(
    "profile,minimum_rate",
    [("kokoro-hf-v1", 21000), ("kokoro-hf-v2", 22000), ("kokoro-hf-v3", 22000)],
)
def test_profile_nyquist_boundary(profile, minimum_rate):
    engine = kokoro.KokoroEngine()
    with pytest.raises(StudioError, match=f"高于 {minimum_rate} Hz") as caught:
        engine._audio_postprocessing(minimum_rate, profile=profile)
    assert caught.value.code == "KOKORO_FILTER_SAMPLE_RATE"
    post = engine._audio_postprocessing(minimum_rate + 1, profile=profile)
    assert post["enabled"] is True
    assert post["profile"] == profile
    assert post["pcm_sample_rate"] == minimum_rate + 1


@pytest.mark.parametrize("profile", ["kokoro-hf-v1", "kokoro-hf-v2", "kokoro-hf-v3"])
@pytest.mark.parametrize("sample_rate", [8000, 16000, 21000, 22000])
def test_filter_off_preserves_profile_without_nyquist_restriction(profile, sample_rate):
    post = kokoro.KokoroEngine(audio_filter="off")._audio_postprocessing(
        sample_rate, profile=profile
    )
    assert post == {
        "enabled": False,
        "profile": profile,
        "method": "none",
        "pcm_sample_rate": sample_rate,
        "bandreject": None,
        "lowpass": None,
        "ffmpeg_filter": None,
        **(
            {"config_version": 3, "bandreject_secondary": None} if profile == "kokoro-hf-v3" else {}
        ),
    }


@pytest.mark.parametrize("mode", ["on", "off"])
@pytest.mark.parametrize("profile", ["", "kokoro-hf-v4"])
def test_unknown_profile_is_rejected(mode, profile):
    with pytest.raises(InputError, match="profile"):
        kokoro.KokoroEngine(audio_filter=mode)._audio_postprocessing(24000, profile=profile)


def test_filter_off_allows_lower_rate_and_keeps_validation(pipeline, tmp_path, monkeypatch):
    pipeline["rate"] = 16000

    def invalid(*args, **kwargs):
        raise ValidationError("synthetic decode failure")

    monkeypatch.setattr("lextide.jobs.validate_audio", invalid)
    with pytest.raises(ValidationError, match="synthetic decode failure"):
        make_job(tmp_path, audio_filter="off")
    assert len(pipeline["calls"]) == 2 and "-af" not in pipeline["calls"][-1]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("engine_id", ["kokoro", "edge-tts"])
@pytest.mark.parametrize("mode", [None, "on", "off"])
def test_cli_filter_switch_only_reaches_kokoro(engine_id, mode, monkeypatch, tmp_path):
    prepared = prepare_text(text="Local contract test.")

    async def job(prepared, settings, out_dir, engine, **kwargs):
        assert engine.engine_id == engine_id
        if engine_id == "kokoro":
            assert engine.audio_filter == (mode or "on")
        else:
            assert not hasattr(engine, "audio_filter")
        return out_dir / "mock-only"

    monkeypatch.setattr("lextide.jobs.synthesize_job", job)
    args = [
        "synthesize",
        "--engine",
        engine_id,
        "--text",
        prepared.tts_text,
        "--out-dir",
        str(tmp_path),
        "--confirm-text",
        prepared.sha256,
        "--allow-online",
    ]
    if mode:
        args += ["--kokoro-audio-filter", mode]
    assert main(args) == 0
