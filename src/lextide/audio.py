"""Bounded, offline MP3 validation with strict frame boundaries and PCM decoding.

Frame structure cannot establish completeness: removing whole frames may leave a
valid MP3. Compare SHA256 with a separately trusted manifest for that purpose.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .errors import StudioError, ValidationError

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_DURATION_SECONDS = 600
PROBE_TIMEOUT_SECONDS = 20
DECODE_TIMEOUT_SECONDS = 120
_BITRATES_MPEG1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_BITRATES_MPEG2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
_LIMITATION = (
    "Whole-frame removal can remain structurally valid; compare SHA256 with a trusted manifest."
)


def _executable(name: str, requested: str | None) -> str:
    candidates = (
        [requested] if requested is not None else [shutil.which(name), f"/opt/homebrew/bin/{name}"]
    )
    for candidate in candidates:
        if not candidate:
            continue
        located = shutil.which(candidate)
        if located is None:
            continue
        path = Path(located)
        try:
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.resolve(strict=True))
        except OSError:
            continue
    raise StudioError(
        "MEDIA_TOOL_UNAVAILABLE",
        f"未找到可用的 {name}；请用对应参数指定可执行文件路径。",
        7,
    )


def resolve_media_tools(ffmpeg: str | None = None, ffprobe: str | None = None) -> tuple[str, str]:
    """Resolve executable files; an invalid explicit choice never falls back."""
    return _executable("ffmpeg", ffmpeg), _executable("ffprobe", ffprobe)


def _id3v2_end(data: bytes) -> int:
    if not data.startswith(b"ID3"):
        return 0
    if len(data) < 10:
        raise ValidationError("Incomplete ID3v2 header.")
    major, revision, flags = data[3:6]
    permitted_flags = {2: 0xC0, 3: 0xE0, 4: 0xF0}
    if major not in permitted_flags or revision != 0 or flags & ~permitted_flags[major]:
        raise ValidationError("Unsupported or invalid ID3v2 version or flags.")
    if major == 2 and flags & 0x40:
        raise ValidationError("Compressed ID3v2.2 tags are not supported.")
    size_bytes = data[6:10]
    if any(byte & 0x80 for byte in size_bytes):
        raise ValidationError("Invalid ID3v2 synchsafe size.")
    size = sum(byte << shift for byte, shift in zip(size_bytes, (21, 14, 7, 0), strict=True))
    end = 10 + size
    if end > len(data):
        raise ValidationError("Incomplete ID3v2 tag.")
    if major == 4 and flags & 0x10:
        footer_end = end + 10
        if data[end:footer_end] != b"3DI" + data[3:10]:
            raise ValidationError("Missing or inconsistent ID3v2.4 footer.")
        end = footer_end
    # Metadata payloads are opaque. Only the declared tag envelope is accepted;
    # no search for a subsequent audio sync word is performed.
    return end


def _frame_header(data: bytes, offset: int) -> dict:
    header = int.from_bytes(data[offset : offset + 4], "big")
    version_bits = (header >> 19) & 3
    layer_bits = (header >> 17) & 3
    bitrate_index = (header >> 12) & 15
    rate_index = (header >> 10) & 3
    if (
        header >> 21 != 0x7FF
        or version_bits == 1
        or layer_bits != 1
        or bitrate_index in (0, 15)
        or rate_index == 3
        or header & 3 == 2
    ):
        raise ValidationError(f"Invalid or unsupported MP3 frame header at byte {offset}.")
    version, divisor = {3: ("1", 1), 2: ("2", 2), 0: ("2.5", 4)}[version_bits]
    sample_rate = (44100, 48000, 32000)[rate_index] // divisor
    bitrates = _BITRATES_MPEG1 if version_bits == 3 else _BITRATES_MPEG2
    bitrate = bitrates[bitrate_index]
    coefficient = 144 if version_bits == 3 else 72
    frame_bytes = coefficient * bitrate * 1000 // sample_rate + ((header >> 9) & 1)
    channels = 1 if (header >> 6) & 3 == 3 else 2
    crc_bytes = 0 if (header >> 16) & 1 else 2
    side_info_bytes = (
        (17 if channels == 1 else 32) if version_bits == 3 else (9 if channels == 1 else 17)
    )
    if frame_bytes < 4 + crc_bytes + side_info_bytes:
        raise ValidationError("MP3 frame is too short for its header and side information.")
    return {
        "mpeg_version": version,
        "sample_rate": sample_rate,
        "channels": channels,
        "samples_per_frame": 1152 if version_bits == 3 else 576,
        "frame_bytes": frame_bytes,
        "bitrate_kbps": bitrate,
    }


def inspect_mp3(
    data: bytes,
    *,
    max_input_bytes: int = MAX_INPUT_BYTES,
    max_duration_seconds: int = MAX_DURATION_SECONDS,
) -> dict:
    """Inspect contiguous MPEG1/2/2.5 Layer III frames without resynchronizing.

    Free-format bitrate, format changes, compressed ID3v2.2, repeated tags, and
    trailing data other than one terminal ID3v1 tag are deliberately rejected.
    ID3v2 metadata contents are not interpreted or semantically validated.
    """
    if (
        type(max_input_bytes) is not int
        or max_input_bytes <= 0
        or type(max_duration_seconds) is not int
        or max_duration_seconds <= 0
    ):
        raise ValidationError("Audio verification limits must be positive integers.")
    if not data:
        raise ValidationError("Audio file is empty.")
    if len(data) > max_input_bytes:
        raise ValidationError(
            f"Audio exceeds the {max_input_bytes // (1024 * 1024)} MiB input limit."
        )
    start = _id3v2_end(data)
    end = len(data)
    id3v1_bytes = 0
    offset = start
    count = 0
    first = None
    bitrates = set()
    while offset < end:
        if end - offset == 128 and data[offset : offset + 3] == b"TAG":
            # Recognize ID3v1 only at a frame boundary, never inside frame data.
            id3v1_bytes = 128
            end = offset
            break
        if end - offset < 4:
            raise ValidationError("Incomplete final MP3 frame header or trailing bytes.")
        frame = _frame_header(data, offset)
        if offset + frame["frame_bytes"] > end:
            raise ValidationError("Incomplete final MP3 frame.")
        if first is None:
            first = frame
        elif any(frame[key] != first[key] for key in ("mpeg_version", "sample_rate", "channels")):
            raise ValidationError("MP3 sample rate, channel count, or MPEG version changes.")
        count += 1
        bitrates.add(frame["bitrate_kbps"])
        offset += frame["frame_bytes"]
        if count * first["samples_per_frame"] > max_duration_seconds * first["sample_rate"]:
            raise ValidationError(
                f"Audio exceeds the {max_duration_seconds / 60:g} minute duration limit."
            )
    if not first or count == 0:
        raise ValidationError("No MP3 audio frames were found.")
    encoded_samples = count * first["samples_per_frame"]
    return {
        "frames": count,
        "mpeg_version": first["mpeg_version"],
        "sample_rate": first["sample_rate"],
        "channels": first["channels"],
        "samples_per_frame": first["samples_per_frame"],
        "encoded_samples_per_channel": encoded_samples,
        "duration_ms": round(encoded_samples * 1000 / first["sample_rate"], 3),
        "bitrates_kbps": sorted(bitrates),
        "audio_start": start,
        "audio_end": end,
        "id3v2_bytes": start,
        "id3v1_bytes": id3v1_bytes,
        "complete_frame_removal_detectable": False,
        "structural_limitations": [_LIMITATION],
    }


def _run(command: list[str], *, timeout: int, capture_output: bool = False) -> bytes:
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValidationError("Local audio verification timed out.") from None
    except OSError:
        raise ValidationError("A local audio verification tool could not be executed.") from None
    if completed.returncode != 0:
        raise ValidationError("A local audio verification tool rejected the audio.")
    return completed.stdout or b""


def _probe(path: Path, ffprobe: str, parsed: dict) -> float:
    raw = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-f",
            "mp3",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        timeout=PROBE_TIMEOUT_SECONDS,
        capture_output=True,
    )
    try:
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError
        streams = document["streams"]
        if not isinstance(streams, list) or len(streams) != 1:
            raise ValueError
        stream = streams[0]
        if not isinstance(stream, dict):
            raise ValueError
        if (
            stream["codec_name"] != "mp3"
            or int(stream["sample_rate"]) != parsed["sample_rate"]
            or int(stream["channels"]) != parsed["channels"]
        ):
            raise ValueError
        duration_value = stream.get("duration")
        if duration_value in (None, "N/A"):
            duration_value = document["format"]["duration"]
        duration = float(duration_value)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError, IndexError, UnicodeDecodeError):
        raise ValidationError("ffprobe did not confirm a valid MP3 format and duration.") from None
    return duration * 1000


def _read_audio(path: Path, *, max_input_bytes: int = MAX_INPUT_BYTES) -> bytes:
    if type(max_input_bytes) is not int or max_input_bytes <= 0:
        raise ValidationError("Audio verification byte limit must be a positive integer.")
    try:
        if not path.is_file():
            raise ValidationError("Audio path must identify a regular file.")
        with path.open("rb") as handle:
            data = handle.read(max_input_bytes + 1)
    except OSError:
        raise ValidationError("Audio file could not be read.") from None
    if len(data) > max_input_bytes:
        raise ValidationError(
            f"Audio exceeds the {max_input_bytes // (1024 * 1024)} MiB input limit."
        )
    return data


def validate_audio(
    path: Path,
    *,
    ffmpeg: str,
    ffprobe: str,
    max_input_bytes: int = MAX_INPUT_BYTES,
    max_duration_seconds: int = MAX_DURATION_SECONDS,
    decode_timeout_seconds: int = DECODE_TIMEOUT_SECONDS,
) -> dict:
    """Validate one short MP3; duration is measured from decoded PCM samples.

    Tolerance is max(120 ms, three encoded frames), at most 216 ms for supported
    rates. This allows a metadata frame and encoder delay/padding, while bounding
    disagreements between parsed, probed, and decoded durations. Temporary files
    live beside the input and are removed on success and failure.
    """
    if type(decode_timeout_seconds) is not int or decode_timeout_seconds <= 0:
        raise ValidationError("Audio verification timeout must be a positive integer.")
    ffmpeg, ffprobe = resolve_media_tools(ffmpeg, ffprobe)
    path = Path(path).absolute()
    data = _read_audio(path, max_input_bytes=max_input_bytes)
    parsed = inspect_mp3(
        data, max_input_bytes=max_input_bytes, max_duration_seconds=max_duration_seconds
    )
    tolerance_ms = max(120.0, 3000 * parsed["samples_per_frame"] / parsed["sample_rate"])
    sample_rate, channels = parsed["sample_rate"], parsed["channels"]
    stride = channels * 2  # Signed 16-bit little-endian interleaved PCM.
    max_pcm_bytes = max_duration_seconds * sample_rate * stride
    try:
        with tempfile.TemporaryDirectory(prefix=".tts-studio-validate-", dir=path.parent) as tmp:
            temp = Path(tmp)
            snapshot = temp / "input.mp3"
            pcm = temp / "decoded.s16le"
            snapshot.write_bytes(data)
            probe_ms = _probe(snapshot, ffprobe, parsed)
            if abs(probe_ms - parsed["duration_ms"]) > tolerance_ms:
                raise ValidationError("Parsed and ffprobe audio durations disagree.")
            _run(
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
                    "-f",
                    "mp3",
                    "-i",
                    str(snapshot),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-sn",
                    "-dn",
                    "-threads",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    str(sample_rate),
                    "-ac",
                    str(channels),
                    "-f",
                    "s16le",
                    "-fs",
                    str(max_pcm_bytes + stride),
                    "-y",
                    str(pcm),
                ],
                timeout=decode_timeout_seconds,
            )
            pcm_bytes = pcm.stat().st_size
            if pcm_bytes <= 0 or pcm_bytes > max_pcm_bytes or pcm_bytes % stride:
                raise ValidationError("Decoded PCM has an invalid or excessive sample count.")
            non_silent = False
            with pcm.open("rb") as handle:
                while chunk := handle.read(65536):
                    if chunk != b"\x00" * len(chunk):
                        non_silent = True
                        break
            if not non_silent:
                raise ValidationError("Decoded audio contains only zero-valued PCM samples.")
    except OSError:
        raise ValidationError(
            "Temporary audio verification files could not be processed."
        ) from None
    samples = pcm_bytes // stride
    decoded_ms = samples * 1000 / sample_rate
    if (
        max(decoded_ms, probe_ms, parsed["duration_ms"])
        - min(decoded_ms, probe_ms, parsed["duration_ms"])
        > tolerance_ms
    ):
        raise ValidationError("Decoded, parsed, and ffprobe audio durations disagree.")
    if _read_audio(path, max_input_bytes=max_input_bytes) != data:
        raise ValidationError("Audio file changed during verification.")
    return {
        "duration_ms": round(decoded_ms),
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "frames": parsed["frames"],
        "sample_rate": sample_rate,
        "channels": channels,
        "codec": "mp3",
        "decoder_ok": True,
        "non_silent": True,
        "decoded_samples_per_channel": samples,
        "parsed_duration_ms": parsed["duration_ms"],
        "probe_duration_ms": round(probe_ms, 3),
        "duration_tolerance_ms": round(tolerance_ms, 3),
        "complete_frame_removal_detectable": False,
        "structural_limitations": [_LIMITATION],
    }
