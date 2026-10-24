import asyncio
import hashlib
import json

import pytest

from lextide.config import Settings
from lextide.engines.base import EngineResult, SentenceBoundary
from lextide.errors import StudioError, ValidationError
from lextide.jobs import synthesize_job, verify_job
from lextide.text import prepare_text


class FakeEngine:
    engine_id = "offline-contract-test"
    version = "1"
    online = True  # Test that the production consent gate applies even with an injected engine.

    def __init__(self, fail=False):
        self.called = False
        self.fail = fail

    async def synthesize(self, text, destination, settings, *, allow_online):
        self.called = True
        destination.write_bytes(b"test fixture, not playable audio")
        if self.fail:
            raise StudioError("SIMULATED_OFFLINE", "Offline test failure", 4)
        return EngineResult(
            [SentenceBoundary(0, 10000000, text)],
            True,
            destination.stat().st_size,
            hashlib.sha256(destination.read_bytes()).hexdigest(),
        )


@pytest.fixture
def fake_media(monkeypatch):
    monkeypatch.setattr("lextide.jobs.resolve_media_tools", lambda *a: ("fake", "fake"))
    monkeypatch.setattr("lextide.jobs.validate_audio", lambda *a, **kw: {"duration_ms": 1000})


def test_job_denies_consent_before_engine_or_output(tmp_path):
    prepared = prepare_text(text="Hello.")
    engine = FakeEngine()
    with pytest.raises(StudioError):
        asyncio.run(
            synthesize_job(
                prepared,
                Settings(),
                tmp_path / "new",
                engine,
                allow_online=False,
                confirmed_sha256=prepared.sha256,
            )
        )
    assert not engine.called
    assert not (tmp_path / "new").exists()


def test_failed_job_cleans_partial_and_preserves_existing(tmp_path, fake_media):
    existing = tmp_path / "old.mp3"
    existing.write_bytes(b"untouched")
    prepared = prepare_text(text="Hello.")
    with pytest.raises(StudioError):
        asyncio.run(
            synthesize_job(
                prepared,
                Settings(),
                tmp_path,
                FakeEngine(fail=True),
                allow_online=True,
                confirmed_sha256=prepared.sha256,
            )
        )
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_bytes() == b"untouched"


def test_atomic_bundle_and_trusted_hash_revalidation(tmp_path, fake_media):
    prepared = prepare_text(text="Hello.")
    output = asyncio.run(
        synthesize_job(
            prepared,
            Settings(),
            tmp_path,
            FakeEngine(),
            allow_online=True,
            confirmed_sha256=prepared.sha256,
        )
    )
    assert list(tmp_path.iterdir()) == [output]
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert (output / "original.txt").read_bytes() == prepared.original_bytes
    assert len(manifest["artifacts"]) == 8
    assert verify_job(output)["hashes_match"] is True
    audio = output / "audio.mp3"
    audio.write_bytes(audio.read_bytes()[:-1])
    with pytest.raises(ValidationError, match="manifest"):
        verify_job(output)
