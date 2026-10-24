from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..config import Settings


@dataclass(frozen=True)
class SentenceBoundary:
    offset_ticks: int  # 100 ns ticks from the engine, retained without scaling.
    duration_ticks: int
    text: str


@dataclass(frozen=True)
class EngineResult:
    boundaries: list[SentenceBoundary]
    stream_complete: bool
    audio_bytes: int
    audio_sha256: str
    timing_source: str = "edge-tts SentenceBoundary"
    model: dict | None = None
    effective_settings: dict = field(default_factory=dict)
    audio_format: str = "mp3"
    timing_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EngineCapabilities:
    engine_id: str
    version: str
    online: bool
    installed: bool
    available: bool
    voices: tuple[str, ...]
    rate_range: tuple[int, int] = (-50, 100)
    output_formats: tuple[str, ...] = ("mp3",)
    timing_level: str = "sentence"
    timing_source: str = "edge-tts SentenceBoundary"
    model: dict | None = None
    notes: tuple[str, ...] = ()


class TTSEngine(Protocol):
    engine_id: str
    version: str
    online: bool

    def capabilities(self) -> EngineCapabilities: ...

    async def synthesize(
        self, text: str, destination: Path, settings: Settings, *, allow_online: bool
    ) -> EngineResult: ...
