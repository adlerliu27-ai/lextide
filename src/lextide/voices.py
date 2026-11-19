"""Curated American voices. Gender and locale are provider catalogue attributes."""

from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile


@dataclass(frozen=True)
class Voice:
    voice_id: str
    engine_id: str
    display_name: str
    gender: str
    provider: str
    locale: str = "en-US"

    @property
    def online(self) -> bool:
        return self.engine_id == "edge-tts"


AMERICAN_VOICES = (
    Voice("en-US-GuyNeural", "edge-tts", "Guy", "male", "Microsoft Edge"),
    Voice("en-US-AriaNeural", "edge-tts", "Aria", "female", "Microsoft Edge"),
    Voice("am_michael", "kokoro", "Michael", "male", "Kokoro"),
    Voice("af_heart", "kokoro", "Heart", "female", "Kokoro"),
)


def kokoro_voice_ids(path: Path) -> tuple[str, ...]:
    """Inspect the installed NPZ directory without extracting arrays or loading ONNX."""
    try:
        with ZipFile(path) as archive:
            members = set(archive.namelist())
    except (BadZipFile, OSError):
        return ()
    return tuple(
        v.voice_id
        for v in AMERICAN_VOICES
        if v.engine_id == "kokoro" and f"{v.voice_id}.npy" in members
    )
