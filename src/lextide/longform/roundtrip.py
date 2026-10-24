"""Bounded sample tolerance for the G3 mono 24 kHz libmp3lame export profile.

Full decoding remains the caller's responsibility. Tolerance alone cannot detect
whole-frame truncation, so reconcile the encoder's Info/Xing and gapless record
with the already validated file before comparing PCM input and decoded output.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..audio import _frame_header, _id3v2_end
from ..errors import ValidationError

SAMPLE_RATE = 24000
SAMPLES_PER_FRAME = 576  # MPEG-2 Layer III, not one PCM sample.
MAX_FRAME_DELTA = 3
MAX_SAMPLE_DELTA = MAX_FRAME_DELTA * SAMPLES_PER_FRAME


def validate_sample_roundtrip(path: Path, report: dict, expected_samples: int) -> dict:
    """Return reproducible evidence, accepting at most 1728 samples (72 ms).

    This is deliberately specific to newly encoded G3 exports, not a relaxed
    rule for arbitrary MP3 files or legacy schema 1/2 jobs. A SHA-256 is an
    integrity check, not authentication against coordinated file/manifest edits.
    """
    if (
        type(expected_samples) is not int
        or expected_samples <= 0
        or report.get("decoder_ok") is not True
        or report.get("non_silent") is not True
        or report.get("codec") != "mp3"
        or report.get("sample_rate") != SAMPLE_RATE
        or report.get("channels") != 1
        or any(
            type(report.get(key)) is not int or report[key] <= 0
            for key in ("decoded_samples_per_channel", "frames", "byte_size")
        )
    ):
        raise ValidationError("MP3 roundtrip requires a complete 24 kHz mono decode report.")
    try:
        with path.open("rb") as stream:
            # Our encoder writes a tiny ID3 tag. Do not scan for sync words or
            # allocate unbounded metadata if an unexpected file is substituted.
            prefix = stream.read(65536)
            start = _id3v2_end(prefix)
            frame = _frame_header(prefix, start)
            if (
                frame["mpeg_version"] != "2"
                or frame["sample_rate"] != SAMPLE_RATE
                or frame["channels"] != 1
                or frame["samples_per_frame"] != SAMPLES_PER_FRAME
            ):
                raise ValidationError("MP3 roundtrip format differs from the G3 encoder profile.")
            first = prefix[start : start + frame["frame_bytes"]]
            crc_bytes = 0 if first[1] & 1 else 2
            offset = 4 + crc_bytes + 9  # MPEG-2 mono side information.
            if first[offset : offset + 4] not in (b"Info", b"Xing"):
                raise ValidationError("MP3 roundtrip requires an Info/Xing frame.")
            flags = int.from_bytes(first[offset + 4 : offset + 8], "big")
            if flags & 3 != 3 or flags & ~15:
                raise ValidationError("MP3 Info/Xing frame/byte counts are missing or invalid.")
            offset += 8
            frames = int.from_bytes(first[offset : offset + 4], "big")
            audio_bytes = int.from_bytes(first[offset + 4 : offset + 8], "big")
            offset += 8 + (100 if flags & 4 else 0) + (4 if flags & 8 else 0)
            extension = first[offset : offset + 24]
            if len(extension) != 24 or extension[:4] not in (b"Lavc", b"LAME"):
                raise ValidationError("MP3 gapless encoder delay/padding record is unavailable.")
            encoder = extension[:9].rstrip(b"\0").decode("ascii")
            gapless = int.from_bytes(extension[21:24], "big")
            delay, padding = gapless >> 12, gapless & 0xFFF
            stream.seek(0)
            digest = hashlib.sha256()
            size = 0
            for block in iter(lambda: stream.read(65536), b""):
                digest.update(block)
                size += len(block)
    except (OSError, UnicodeError, IndexError):
        raise ValidationError("MP3 roundtrip metadata could not be read.") from None
    if size != report["byte_size"] or digest.hexdigest() != report.get("sha256"):
        raise ValidationError("MP3 changed since complete decoding: hash or size mismatch.")
    # The Info frame counts itself in bytes, but excludes itself from frames.
    # Our export profile writes no terminal ID3v1 tag. Reject unexpected tails.
    if frames != report["frames"] - 1 or audio_bytes != size - start:
        raise ValidationError("MP3 Info/Xing counts differ from the complete file (truncation).")
    decoded = report["decoded_samples_per_channel"]
    if frames * SAMPLES_PER_FRAME - delay - padding != decoded:
        raise ValidationError("MP3 decoded samples disagree with encoder delay/padding.")
    delta = decoded - expected_samples
    if abs(delta) > MAX_SAMPLE_DELTA:
        raise ValidationError("MP3 roundtrip exceeds 3 MPEG-2 frames / 1728 samples / 72 ms.")
    return {
        "profile": "mp3-roundtrip-v1",
        "expected_samples": expected_samples,
        "decoded_samples": decoded,
        "delta_samples": delta,
        "sample_rate": SAMPLE_RATE,
        "samples_per_frame": SAMPLES_PER_FRAME,
        "tolerance_frames": MAX_FRAME_DELTA,
        "tolerance_samples": MAX_SAMPLE_DELTA,
        "tolerance_ms": 72,
        "xing_frames": frames,
        "xing_bytes": audio_bytes,
        "encoder": encoder,
        "encoder_delay": delay,
        "encoder_padding": padding,
    }
