"""Synthetic PCM/real FFmpeg checks; no Kokoro inference or listening claims."""

import asyncio
import copy

import pytest
from test_g3_media import chunk, wav_file
from test_g3_media import media_tools as _media_tools
from test_g3_tasks import harness as _task_harness

from lextide.config import Settings
from lextide.errors import ValidationError
from lextide.jobs import verify_job
from lextide.longform import media, tasks
from lextide.longform.document import prepare_document
from lextide.longform.roundtrip import MAX_SAMPLE_DELTA, validate_sample_roundtrip
from lextide.longform.storage import atomic_json, read_json

harness = _task_harness
media_tools = _media_tools


@pytest.fixture
def encoded(tmp_path, media_tools):
    ffmpeg, ffprobe = media_tools
    path = tmp_path / "audio.mp3"
    result = asyncio.run(
        media.assemble(
            [chunk(wav_file(tmp_path / "raw.wav", 224671))],
            path,
            settings=Settings(engine="kokoro", voice="af_heart"),
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
    )
    report = media.validate_export_audio(path, ffmpeg=ffmpeg, ffprobe=ffprobe)
    return path, report, result


@pytest.mark.parametrize("samples", [224100, 224640, 224671])
@pytest.mark.parametrize("mode", ["on", "off"])
def test_real_roundtrip_known_input_lengths(samples, mode, tmp_path, media_tools, monkeypatch):
    ffmpeg, ffprobe = media_tools
    commands = []
    original = media.run_supervised

    async def capture(args, **kwargs):
        commands.append(args)
        return await original(args, **kwargs)

    monkeypatch.setattr(media, "run_supervised", capture)
    path = tmp_path / "audio.mp3"
    result = asyncio.run(
        media.assemble(
            [chunk(wav_file(tmp_path / "raw.wav", samples))],
            path,
            settings=Settings(engine="kokoro", voice="af_heart"),
            audio_filter=mode,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
    )
    record = result.effective_settings["sample_roundtrip"]
    assert record["expected_samples"] == samples == result.effective_settings["samples"]
    assert abs(record["delta_samples"]) <= 1728
    assert record["decoded_samples"] == samples + record["delta_samples"]
    assert record["samples_per_frame"] == 576 and record["tolerance_ms"] == 72
    # Preserve G3's existing local millisecond end cap, without scaling for MP3 padding.
    assert result.boundaries[0].duration_ticks == min(
        samples * 10_000_000 // 24000, media._duration_ms(samples) * 10000
    )
    assert len(commands) == 1 and commands[0].count("libmp3lame") == 1
    assert commands[0][commands[0].index("-i") + 1].endswith("assembled.wav")
    assert ("-af" in commands[0]) == (mode == "on")
    if mode == "on":
        assert commands[0][commands[0].index("-af") + 1] == (
            "bandreject=frequency=9600:width_type=h:width=2800:blocksize=0,"
            "bandreject=frequency=4800:width_type=h:width=60:blocksize=0,"
            "lowpass=frequency=10000:poles=2:width_type=q:width=0.707:blocksize=0"
        )
    report = media.validate_export_audio(path, ffmpeg=ffmpeg, ffprobe=ffprobe)
    assert validate_sample_roundtrip(path, report, samples) == record


@pytest.mark.parametrize("delta", [0, -16, 11, 16, -MAX_SAMPLE_DELTA, MAX_SAMPLE_DELTA])
def test_inclusive_tolerance_in_pcm_samples(encoded, delta):
    path, report, _ = encoded
    expected = report["decoded_samples_per_channel"] - delta
    record = validate_sample_roundtrip(path, report, expected)
    assert record["delta_samples"] == delta
    assert record["tolerance_samples"] == 1728


@pytest.mark.parametrize("delta", [-1729, 1729, -24000, 24000])
def test_beyond_tolerance_is_rejected(encoded, delta):
    path, report, _ = encoded
    with pytest.raises(ValidationError, match="exceeds 3 MPEG-2"):
        validate_sample_roundtrip(path, report, report["decoded_samples_per_channel"] - delta)


@pytest.mark.parametrize("frames", [1, 2])
def test_whole_frame_truncation_rejected_even_with_new_hash_and_decodable_audio(
    encoded, frames, media_tools
):
    path, _, _ = encoded
    path.write_bytes(path.read_bytes()[: -384 * frames])  # 128 kb/s, 24 kHz MPEG-2.
    ffmpeg, ffprobe = media_tools
    report = media.validate_export_audio(path, ffmpeg=ffmpeg, ffprobe=ffprobe)
    assert abs(report["decoded_samples_per_channel"] - 224671) <= 1728
    with pytest.raises(ValidationError, match="Info/Xing counts"):
        validate_sample_roundtrip(path, report, 224671)


def test_partial_frame_truncation_rejected_by_full_validator(encoded, media_tools):
    path, _, _ = encoded
    path.write_bytes(path.read_bytes()[:-1])
    ffmpeg, ffprobe = media_tools
    with pytest.raises(ValidationError, match="Incomplete final MP3 frame"):
        media.validate_export_audio(path, ffmpeg=ffmpeg, ffprobe=ffprobe)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "0" * 64),
        ("byte_size", 1),
        ("decoder_ok", False),
        ("non_silent", False),
        ("sample_rate", 48000),
        ("channels", 2),
        ("decoded_samples_per_channel", 224672),
    ],
)
def test_missing_decode_or_inconsistent_integrity_report_rejected(encoded, field, value):
    path, report, _ = encoded
    changed = copy.deepcopy(report)
    changed[field] = value
    with pytest.raises(ValidationError):
        validate_sample_roundtrip(path, changed, 224671)


def test_changed_gapless_padding_cannot_hide_decoded_sample_difference(encoded):
    import hashlib

    path, report, _ = encoded
    raw = bytearray(path.read_bytes())
    encoder = raw.index(b"Lavc")  # The fixed FFmpeg/libmp3lame export profile.
    raw[encoder + 23] ^= 1  # Leave frame and byte counts intact.
    path.write_bytes(raw)
    changed = {**report, "sha256": hashlib.sha256(raw).hexdigest()}
    with pytest.raises(ValidationError, match="encoder delay/padding"):
        validate_sample_roundtrip(path, changed, 224671)


def test_decoder_failure_cannot_publish_export(tmp_path, media_tools, monkeypatch):
    def reject(*args, **kwargs):
        raise ValidationError("Local decoder rejected audio")

    monkeypatch.setattr(media, "validate_export_audio", reject)
    ffmpeg, ffprobe = media_tools
    destination = tmp_path / "audio.mp3"
    with pytest.raises(ValidationError, match="decoder rejected"):
        asyncio.run(
            media.assemble(
                [chunk(wav_file(tmp_path / "raw.wav", 224671))],
                destination,
                settings=Settings(engine="kokoro", voice="af_heart"),
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
            )
        )
    assert not destination.exists()


def test_manifest_roundtrip_recomputed_not_trusted(harness):
    _, create, run = harness
    output = run(create())
    assert verify_job(output)["status"] == "valid"
    manifest = read_json(output / "manifest.json")
    manifest["effective_settings"]["sample_roundtrip"]["tolerance_samples"] = 99999
    atomic_json(output / "manifest.json", manifest)
    with pytest.raises(ValidationError):
        verify_job(output)


def test_artifact_hash_mismatch_still_rejected(harness):
    _, create, run = harness
    output = run(create())
    path = output / "audio.mp3"
    data = bytearray(path.read_bytes())
    data[-20] ^= 1
    path.write_bytes(data)
    with pytest.raises(ValidationError):
        verify_job(output)


def test_task_publishes_and_verifies_padding_delta(harness, tmp_path, monkeypatch):
    engine, _, run = harness
    original = engine.synthesize

    async def length_224671(self, text, destination, *, allow_online):
        metadata = await original(self, text, destination, allow_online=allow_online)
        wav_file(destination, 224671)
        metadata["samples"] = metadata["effective_settings"]["samples"] = 224671
        metadata["boundaries"][0]["duration_ticks"] = 224671 * 10_000_000 // 24000
        return metadata

    monkeypatch.setattr(engine, "synthesize", length_224671)
    settings = Settings(engine="kokoro", voice="af_heart")
    task = tasks.create_task(
        prepare_document(text="A small bird rests beside the quiet river."),
        settings,
        tmp_path / "tasks",
        tmp_path / "cache",
        engine=engine(settings),
    )
    output = run(task)
    manifest = read_json(output / "manifest.json")
    record = manifest["effective_settings"]["sample_roundtrip"]
    assert record["expected_samples"] == 224671
    assert abs(record["delta_samples"]) <= 1728
    assert record["decoded_samples"] == 224671 + record["delta_samples"]
    assert verify_job(output)["status"] == "valid"
    assert len(engine.calls) == 1
