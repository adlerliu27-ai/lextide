import asyncio
import hashlib

import aiohttp
import pytest

from lextide.config import Settings
from lextide.engines.edge import EdgeEngine
from lextide.errors import StudioError


def test_adapter_requires_explicit_authorization(tmp_path):
    with pytest.raises(StudioError, match="allow-online"):
        asyncio.run(
            EdgeEngine().synthesize(
                "Hello.", tmp_path / "audio.mp3", Settings(), allow_online=False
            )
        )
    assert not list(tmp_path.iterdir())


def test_public_adapter_contract(monkeypatch, tmp_path):
    async def voices():
        return [{"ShortName": "en-US-GuyNeural"}]

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs):
            assert text == "Hello."
            assert voice == "en-US-GuyNeural"
            assert kwargs["boundary"] == "SentenceBoundary"
            assert kwargs["rate"] == "-4%"

        async def stream(self):
            yield {"type": "audio", "data": b"fake audio for adapter contract only"}
            yield {"type": "SentenceBoundary", "offset": 0, "duration": 10000000, "text": "Hello."}

    monkeypatch.setattr("edge_tts.list_voices", voices)
    monkeypatch.setattr("edge_tts.Communicate", FakeCommunicate)
    target = tmp_path / "audio.mp3"
    result = asyncio.run(EdgeEngine().synthesize("Hello.", target, Settings(), allow_online=True))
    assert result.stream_complete
    assert result.audio_bytes == target.stat().st_size
    assert result.audio_sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert len(result.boundaries) == 1


def test_http_error_does_not_expose_upstream_exception(monkeypatch, tmp_path):
    async def forbidden():
        raise aiohttp.ClientResponseError(None, (), status=403, message="PRIVATE_URL_AND_COOKIE")

    monkeypatch.setattr("edge_tts.list_voices", forbidden)
    with pytest.raises(StudioError) as caught:
        asyncio.run(
            EdgeEngine().synthesize("Hello.", tmp_path / "a.mp3", Settings(), allow_online=True)
        )
    assert caught.value.code == "ONLINE_HTTP_ERROR"
    assert "403" in str(caught.value)
    assert "PRIVATE" not in str(caught.value)
