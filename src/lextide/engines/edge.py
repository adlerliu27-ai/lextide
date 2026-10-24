"""Unmodified upstream edge-tts adapter. No private protocol code or raw error logging."""

import asyncio
import hashlib
import os
from importlib.metadata import version
from pathlib import Path

import aiohttp
import edge_tts

from ..config import MAX_AUDIO_BYTES, Settings
from ..errors import InputError, StudioError, ValidationError
from .base import EngineCapabilities, EngineResult, SentenceBoundary


class EdgeEngine:
    engine_id = "edge-tts"
    version = version("edge-tts")
    online = True

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            self.engine_id,
            self.version,
            True,
            True,
            True,
            ("en-US-GuyNeural",),
            notes=("声线列表与服务可用性仅在授权后的合成阶段联网确认。",),
        )

    async def synthesize(
        self, text: str, destination: Path, settings: Settings, *, allow_online: bool
    ) -> EngineResult:
        # Defense in depth: callers cannot bypass the CLI's consent gate.
        if not allow_online:
            raise StudioError("ONLINE_NOT_AUTHORIZED", "缺少 --allow-online，未发送请求。", 3)
        settings.validate()
        boundaries = []
        digest = hashlib.sha256()
        byte_count = 0
        try:
            async with asyncio.timeout(settings.timeout):
                # A plausible but unavailable voice must not silently become a different voice.
                voices = await edge_tts.list_voices()
                if settings.voice not in {voice["ShortName"] for voice in voices}:
                    raise InputError("服务未提供所选 voice；请核对英文 voice 名称后重试。")
                communicate = edge_tts.Communicate(
                    text,
                    settings.voice,
                    rate=settings.rate,
                    boundary="SentenceBoundary",
                    connect_timeout=10,
                    receive_timeout=30,
                )
                with destination.open("xb") as target:
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            data = chunk["data"]
                            if not isinstance(data, bytes):
                                raise ValidationError("服务返回无效音频数据。")
                            byte_count += len(data)
                            if byte_count > MAX_AUDIO_BYTES:
                                raise ValidationError("音频超过 G1 的 32 MiB 上限。")
                            target.write(data)
                            digest.update(data)
                        elif chunk["type"] == "SentenceBoundary":
                            boundaries.append(
                                SentenceBoundary(chunk["offset"], chunk["duration"], chunk["text"])
                            )
                        elif chunk["type"] == "WordBoundary":
                            raise ValidationError("服务返回词级边界，未满足句级字幕契约。")
                    target.flush()
                    os.fsync(target.fileno())
        except TimeoutError:
            raise StudioError("ONLINE_TIMEOUT", "在线请求超时；检查网络后重新运行。", 4) from None
        except aiohttp.ClientResponseError as exc:
            status = exc.status
            if status == 403:
                message = "在线服务拒绝请求（HTTP 403）；请稍后重试，不要修改服务协议。"
            else:
                message = f"在线服务返回 HTTP {status}；请稍后重试。"
            raise StudioError("ONLINE_HTTP_ERROR", message, 4) from None
        except aiohttp.ClientError:
            raise StudioError(
                "ONLINE_CONNECTION_FAILED", "网络、DNS 或 TLS 连接失败；检查连接后重试。", 4
            ) from None
        except edge_tts.exceptions.NoAudioReceived:
            raise StudioError(
                "ONLINE_NO_AUDIO", "服务未返回音频；检查文本/voice 或稍后重试。", 4
            ) from None
        except edge_tts.exceptions.EdgeTTSException:
            raise StudioError(
                "ONLINE_RESPONSE_ERROR", "在线服务响应异常；请稍后重试。", 4
            ) from None
        except (KeyError, TypeError, ValueError):
            raise StudioError(
                "ONLINE_METADATA_INVALID", "服务返回了不兼容的元数据；请保留错误码反馈。", 4
            ) from None
        if not byte_count or not boundaries:
            raise ValidationError("音频为空或缺少句级时间元数据。")
        # Public generator normal exhaustion is the library completion signal, not an SLA.
        return EngineResult(boundaries, True, byte_count, digest.hexdigest())
