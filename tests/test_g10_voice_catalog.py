"""Discovery must not invent a missing American voice or disclose a raw catalogue."""

import asyncio
from zipfile import ZipFile

from lextide.desktop import engine_policy
from lextide.voices import kokoro_voice_ids


def test_installed_voice_directory_filters_catalog_without_loading_arrays(tmp_path):
    path = tmp_path / "voices.bin"
    assert kokoro_voice_ids(path) == ()
    path.write_bytes(b"not a voice archive")
    assert kokoro_voice_ids(path) == ()
    with ZipFile(path, "w") as voices:
        voices.writestr("af_heart.npy", b"directory inspection only")
        voices.writestr("am_unlisted.npy", b"not curated")
    assert kokoro_voice_ids(path) == ("af_heart",)


def test_probe_reports_only_supported_matching_provider_classifications(monkeypatch):
    async def catalog(**_):
        return [
            {"ShortName": "en-US-AriaNeural", "Locale": "en-US", "Gender": "Female"},
            {"ShortName": "en-US-GuyNeural", "Locale": "en-GB", "Gender": "Male"},
        ]

    monkeypatch.setattr(engine_policy.edge_tts, "list_voices", catalog)
    result = asyncio.run(engine_policy.probe_edge_reachability())
    assert result["reachable"]
    assert result["american_voice_ids"] == ["en-US-AriaNeural"]
