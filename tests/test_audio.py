"""Offline synthetic frame and mocked PCM tests; these do not run a codec or TTS."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from lextide.audio import inspect_mp3, resolve_media_tools, validate_audio
from lextide.errors import StudioError, ValidationError


def frame(*, version=3, rate_index=0, bitrate_index=9, padding=0, mono=False):
    header = (
        (0x7FF << 21)
        | (version << 19)
        | (1 << 17)
        | (1 << 16)
        | (bitrate_index << 12)
        | (rate_index << 10)
        | (padding << 9)
        | ((3 if mono else 0) << 6)
    )
    rates = (44100, 48000, 32000)
    bitrates = (
        (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
        if version == 3
        else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    )
    sample_rate = rates[rate_index] // {3: 1, 2: 2, 0: 4}[version]
    size = (144 if version == 3 else 72) * bitrates[bitrate_index] * 1000 // sample_rate
    return header.to_bytes(4, "big") + bytes(size + padding - 4)


def id3(payload=b"", *, major=4, footer=False):
    size = len(payload)
    size_bytes = bytes((size >> shift) & 127 for shift in (21, 14, 7, 0))
    suffix = bytes((major, 0, 0x10 if footer else 0)) + size_bytes
    return b"ID3" + suffix + payload + (b"3DI" + suffix if footer else b"")


@pytest.mark.parametrize("version", [3, 2, 0])
@pytest.mark.parametrize("mono", [True, False])
def test_valid_contiguous_frames(version, mono):
    first = frame(version=version, mono=mono)
    second = frame(version=version, mono=mono, padding=1, bitrate_index=10)
    result = inspect_mp3(first + second)
    assert result["frames"] == 2
    assert result["channels"] == (1 if mono else 2)
    assert result["audio_end"] == len(first + second)
    assert result["duration_ms"] > 0


@pytest.mark.parametrize("cut", [1, 2, 3, 100, 208])
def test_byte_and_partial_frame_truncation_rejected(cut):
    data = frame() * 4
    with pytest.raises(ValidationError, match="Incomplete"):
        inspect_mp3(data[:-cut])


@pytest.mark.parametrize("tail", [b"\xff", b"\xff\xfb", b"\xff\xfb\x90", b"garbage"])
def test_incomplete_next_header_and_trailing_bytes_rejected(tail):
    with pytest.raises(ValidationError):
        inspect_mp3(frame() * 2 + tail)


def test_complete_frame_removal_is_explicit_structural_limitation():
    original = inspect_mp3(frame() * 4)
    shortened = inspect_mp3(frame() * 3)
    assert original["frames"] == shortened["frames"] + 1
    assert shortened["duration_ms"] < original["duration_ms"]
    assert shortened["complete_frame_removal_detectable"] is False
    assert "trusted manifest" in shortened["structural_limitations"][0]


@pytest.mark.parametrize("data", [b"", id3(), b"TAG" + bytes(125)])
def test_empty_or_tag_only_input_rejected(data):
    with pytest.raises(ValidationError):
        inspect_mp3(data)


@pytest.mark.parametrize("major,footer", [(2, False), (3, False), (4, False), (4, True)])
def test_id3_envelopes_and_terminal_id3v1(major, footer):
    leading = id3(b"metadata", major=major, footer=footer)
    data = leading + frame() * 2 + b"TAG" + bytes(125)
    result = inspect_mp3(data)
    assert result["frames"] == 2
    assert result["id3v2_bytes"] == len(leading)
    assert result["id3v1_bytes"] == 128


def test_tag_signature_inside_frame_payload_is_not_a_tag():
    audio_frame = bytearray(frame())
    audio_frame[-128:-125] = b"TAG"
    result = inspect_mp3(bytes(audio_frame))
    assert result["frames"] == 1
    assert result["id3v1_bytes"] == 0


@pytest.mark.parametrize(
    "bad_tag",
    [
        b"ID3",
        b"ID3\x04\x00\x00\x80\x00\x00\x00",
        b"ID3\x04\x00\x01\x00\x00\x00\x00",
        b"ID3\x04\x00\x00\x00\x00\x7f\x7f",
        b"ID3\x04\x00\x10\x00\x00\x00\x00",
    ],
)
def test_invalid_or_incomplete_id3_rejected(bad_tag):
    with pytest.raises(ValidationError):
        inspect_mp3(bad_tag + frame())


def test_no_resynchronization_across_gap():
    with pytest.raises(ValidationError, match="header"):
        inspect_mp3(frame() + b"\x00" + frame())


def test_format_change_rejected():
    with pytest.raises(ValidationError, match="changes"):
        inspect_mp3(frame() + frame(mono=True))


def test_explicit_missing_tool_does_not_fall_back(tmp_path):
    with pytest.raises(StudioError) as caught:
        resolve_media_tools(str(tmp_path / "not-installed"), "/opt/homebrew/bin/ffprobe")
    assert caught.value.code == "MEDIA_TOOL_UNAVAILABLE"


def test_explicit_nonexecutable_tool_rejected(tmp_path):
    tool = tmp_path / "ffmpeg"
    tool.write_text("not executable")
    tool.chmod(0o600)
    with pytest.raises(StudioError):
        resolve_media_tools(str(tool), str(tool))


def fake_media_tools(monkeypatch, *, samples=8820, silent=False, failure=None, probe_rate=44100):
    monkeypatch.setattr("lextide.audio.resolve_media_tools", lambda *_: ("ffmpeg", "ffprobe"))

    def run(command, **options):
        assert options["stderr"] == subprocess.DEVNULL
        assert options["timeout"] > 0
        assert "shell" not in options
        if command[0] == "ffprobe":
            result = {
                "streams": [{"codec_name": "mp3", "sample_rate": str(probe_rate), "channels": 2}],
                "format": {"duration": "0.208979592"},
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(result).encode())
        assert "pcm_s16le" in command
        assert "-xerror" in command
        assert "-fs" in command
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"])
        if failure == "decode":
            return subprocess.CompletedProcess(command, 1, stderr=b"SECRET UPSTREAM DETAIL")
        pcm = bytes(samples * 4)
        if not silent and pcm:
            pcm = b"\x01" + pcm[1:]  # One least-significant-bit sample is nonzero.
        Path(command[-1]).write_bytes(pcm)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("lextide.audio.subprocess.run", run)


def test_validation_uses_decoded_samples_and_cleans_temporary_files(tmp_path, monkeypatch):
    path = tmp_path / "speech.mp3"
    data = frame() * 8
    path.write_bytes(data)
    fake_media_tools(monkeypatch)
    result = validate_audio(path, ffmpeg="ffmpeg", ffprobe="ffprobe")
    assert result["duration_ms"] == 200
    assert isinstance(result["duration_ms"], int)
    assert result["decoded_samples_per_channel"] == 8820
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["byte_size"] == len(data)
    assert result["non_silent"] is True
    assert result["decoder_ok"] is True
    assert list(tmp_path.iterdir()) == [path]
    json.dumps(result)


@pytest.mark.parametrize(
    "settings,expected",
    [
        ({"silent": True}, "zero-valued"),
        ({"samples": 0}, "sample count"),
        ({"samples": 44100}, "durations disagree"),
        ({"failure": "timeout"}, "timed out"),
        ({"failure": "decode"}, "rejected"),
        ({"probe_rate": 48000}, "ffprobe"),
    ],
)
def test_validation_failure_is_safe_and_cleans_files(tmp_path, monkeypatch, settings, expected):
    path = tmp_path / "speech.mp3"
    path.write_bytes(frame() * 8)
    fake_media_tools(monkeypatch, **settings)
    with pytest.raises(ValidationError, match=expected) as caught:
        validate_audio(path, ffmpeg="ffmpeg", ffprobe="ffprobe")
    assert "SECRET" not in str(caught.value)
    assert caught.value.exit_code == 5
    assert list(tmp_path.iterdir()) == [path]
