"""Filter snapshot compatibility; fake TTS with real short PCM/MP3 only."""

import copy

import pytest
from test_g3_tasks import harness as _task_harness

from lextide.engines.kokoro import KokoroEngine
from lextide.errors import ValidationError
from lextide.jobs import verify_job
from lextide.longform import media, tasks
from lextide.longform.storage import read_json

harness = _task_harness


@pytest.mark.parametrize("mode", ["on", "off"])
def test_new_task_and_old_resume_use_their_own_filter_profile(harness, monkeypatch, mode):
    engine, create, run = harness
    current = tasks.postprocessing_settings
    profiles = ["kokoro-hf-v1", "kokoro-hf-v2", "kokoro-hf-v3"]
    old_tasks = []
    for profile in profiles[:2]:
        saved = KokoroEngine(audio_filter=mode)._audio_postprocessing(24000, profile=profile)
        monkeypatch.setattr(
            tasks, "postprocessing_settings", lambda *a, _saved=saved, **kw: copy.deepcopy(_saved)
        )
        old_tasks.append(create(audio_filter=mode))
    monkeypatch.setattr(tasks, "postprocessing_settings", current)
    all_tasks = [*old_tasks, create(audio_filter=mode)]
    manifests = [read_json(task / "task.json") for task in all_tasks]
    assert [m["config"]["audio_postprocessing"]["profile"] for m in manifests] == profiles
    assert len({m["export_key"] for m in manifests}) == 3
    assert len({tuple(c["cache_key"] for c in m["chunks"]) for m in manifests}) == 1
    for task, manifest in zip(all_tasks, manifests, strict=True):
        output = run(task)
        post = read_json(output / "manifest.json")["effective_settings"]["audio_postprocessing"]
        assert post == manifest["config"]["audio_postprocessing"]
        assert verify_job(output)["status"] == "valid"
    assert len(engine.calls) == 4  # Reuse original PCM; no inference for profile changes.
    for task, manifest in zip(old_tasks, manifests[:2], strict=True):
        again = run(task)
        assert (
            read_json(again / "manifest.json")["effective_settings"]["audio_postprocessing"]
            == manifest["config"]["audio_postprocessing"]
        )
    assert len(engine.calls) == 4


@pytest.mark.parametrize("profile", ["kokoro-hf-v1", "kokoro-hf-v2", "kokoro-hf-v3"])
@pytest.mark.parametrize("mode", ["on", "off"])
def test_saved_processing_requires_exact_known_profile(profile, mode):
    saved = KokoroEngine(audio_filter=mode)._audio_postprocessing(24000, profile=profile)
    assert media.postprocessing_settings("kokoro", mode, saved=saved) == saved
    altered = copy.deepcopy(saved)
    altered["ffmpeg_filter"] = "volume=0"
    with pytest.raises(ValidationError):
        media.postprocessing_settings("kokoro", mode, saved=altered)


def test_edge_processing_stays_disabled_for_every_switch():
    assert media.postprocessing_settings("edge-tts", "on") == media.postprocessing_settings(
        "edge-tts", "off"
    )
    assert not media.postprocessing_settings("edge-tts", "on")["enabled"]


def test_old_schema3_export_without_roundtrip_record_still_valid(harness):
    from lextide.longform.storage import atomic_json

    _, create, run = harness
    output = run(create())
    manifest = read_json(output / "manifest.json")
    manifest["effective_settings"].pop("sample_roundtrip", None)
    atomic_json(output / "manifest.json", manifest)
    assert verify_job(output)["status"] == "valid"
