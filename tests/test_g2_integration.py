"""G2 contract tests. Fake bytes/model records do not claim actual Kokoro inference."""

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest

from lextide.cli import main
from lextide.config import Settings
from lextide.engines.base import EngineResult, SentenceBoundary
from lextide.errors import InputError, ValidationError
from lextide.jobs import synthesize_job, verify_job
from lextide.text import prepare_text


class OfflineFixture:
    engine_id = "kokoro"
    version = "0.6.1"
    online = False

    async def synthesize(self, text, destination, settings, *, allow_online):
        assert allow_online is False
        data = b"offline contract fixture only"
        destination.write_bytes(data)
        return EngineResult(
            [SentenceBoundary(0, 10_000_000, text)],
            True,
            len(data),
            hashlib.sha256(data).hexdigest(),
            timing_source="kokoro sentence PCM sample counts",
            model={
                "id": "tiny-test-model",
                "version": "1",
                "sha256": "a" * 64,
                "voices_sha256": "b" * 64,
            },
            effective_settings={"speed": 0.96, "lang": "en-us", "sample_rate": 24000},
        )


@pytest.fixture
def media_stub(monkeypatch):
    monkeypatch.setattr("lextide.jobs.resolve_media_tools", lambda *a: ("fake", "fake"))
    monkeypatch.setattr("lextide.jobs.validate_audio", lambda *a, **k: {"duration_ms": 1000})


def test_offline_schema2_and_schema1_compatibility(tmp_path, media_stub):
    prepared = prepare_text(text="Local speech.")
    output = asyncio.run(
        synthesize_job(
            prepared,
            Settings(voice="af_heart", engine="kokoro"),
            tmp_path,
            OfflineFixture(),
            allow_online=False,
            confirmed_sha256=prepared.sha256,
        )
    )
    p = output / "manifest.json"
    manifest = json.loads(p.read_text())
    assert manifest["schema_version"] == 2
    assert manifest["engine"]["online"] is False
    assert manifest["model"]["sha256"] == "a" * 64
    assert manifest["effective_settings"]["speed"] == 0.96
    assert manifest["timing"]["source"] == "kokoro sentence PCM sample counts"
    assert verify_job(output)["network_used"] is False
    # A schema 1 job needs no installed models or added G2 keys to be read.
    manifest["schema_version"] = 1
    for key in ("model", "timing", "audio_format", "effective_settings"):
        manifest.pop(key)
    p.write_text(json.dumps(manifest))
    assert verify_job(output)["hashes_match"] is True


def test_missing_offline_identity_never_publishes(tmp_path, media_stub):
    class Broken(OfflineFixture):
        async def synthesize(self, *args, **kwargs):
            return replace(await super().synthesize(*args, **kwargs), model={})

    prepared = prepare_text(text="Local speech.")
    with pytest.raises(ValidationError):
        asyncio.run(
            synthesize_job(
                prepared,
                Settings(voice="af_heart", engine="kokoro"),
                tmp_path,
                Broken(),
                allow_online=False,
                confirmed_sha256=prepared.sha256,
            )
        )
    assert not list(tmp_path.iterdir())


def test_engine_specific_settings():
    Settings().validate()
    Settings(voice="af_heart", engine="kokoro").validate()
    with pytest.raises(InputError):
        Settings(voice="af_heart", engine="edge-tts").validate()
    with pytest.raises(InputError):
        Settings(engine="kokoro").validate()


def test_offline_cli_does_not_require_online_flag(monkeypatch, tmp_path, capsys):
    prepared = prepare_text(text="Local speech.")

    async def job(prepared, settings, out_dir, engine, **kwargs):
        assert engine.online is False
        assert kwargs["allow_online"] is False
        assert settings.voice == "af_heart"
        return out_dir / "fake-job"

    monkeypatch.setattr("lextide.jobs.synthesize_job", job)
    assert (
        main(
            [
                "synthesize",
                "--engine",
                "kokoro",
                "--text",
                prepared.tts_text,
                "--out-dir",
                str(tmp_path),
                "--confirm-text",
                prepared.sha256,
            ]
        )
        == 0
    )
    assert "微软在线服务" not in capsys.readouterr().out
