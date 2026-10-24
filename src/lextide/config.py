"""Conservative G1 limits, validated before any network activity."""

import math
import re
from dataclasses import dataclass

from .errors import InputError

MAX_INPUT_BYTES = 64 * 1024
MAX_TTS_BYTES = 3000  # Escaped UTF-8, kept below the upstream per-request split limit.
MAX_AUDIO_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    voice: str = "en-US-GuyNeural"
    rate: str = "-4%"
    timeout: float = 90.0
    engine: str = "edge-tts"

    def validate(self) -> None:
        if self.engine not in {"edge-tts", "kokoro"}:
            raise InputError("engine 必须是 edge-tts 或 kokoro。")
        voice_pattern = (
            r"en-[A-Z]{2}-[A-Za-z]+Neural" if self.engine == "edge-tts" else r"[ab][fm]_[a-z0-9_]+"
        )
        if not re.fullmatch(voice_pattern, self.voice):
            raise InputError("请提供与所选引擎匹配的英文 voice 名称。")
        if not re.fullmatch(r"[+-]\d{1,3}%", self.rate):
            raise InputError("rate 格式为带正负号的百分比，例如 --rate=-4%。")
        if not -50 <= int(self.rate[:-1]) <= 100:
            raise InputError("G1 rate 支持 -50% 至 +100%。")
        if not math.isfinite(self.timeout) or not 1 <= self.timeout <= 300:
            raise InputError("timeout 必须在 1 至 300 秒之间。")
