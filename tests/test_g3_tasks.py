"""Checkpoint tests use synthetic PCM and fake synthesis, never real TTS."""

import asyncio
import errno
import shutil
import wave

import pytest

from lextide.cli import build_parser, main
from lextide.config import Settings
from lextide.errors import InputError, StudioError, ValidationError
from lextide.jobs import verify_job
from lextide.longform import tasks
from lextide.longform.document import TEXT_RULE_VERSION, prepare_document
from lextide.longform.storage import atomic_json, exclusive_lock, read_json

TEXT = (
    "First, a small bird follows the quiet river.\n\n"
    "Second, a patient walker studies the bridge.\n\n"
    "Third, a new boat arrives beside the garden.\n\n"
    "Finally, the neighbors share a simple meal."
)


@pytest.fixture
def harness(tmp_path):
    class FakeEngine:
        calls = []
        fail_at = None
        interrupt_at = None
        identity_revision = 1

        def __init__(self, settings, **kwargs):
            self.settings = settings

        def identity(self):
            return {
                "engine_id": self.settings.engine,
                "engine_version": "test-1",
                "online": self.settings.engine == "edge-tts",
                "voice": self.settings.voice,
                "rate": self.settings.rate,
                "model": (
                    {
                        "id": "test-model",
                        "version": "1",
                        "sha256": "a" * 64,
                        "voices_sha256": "b" * 64,
                    }
                    if self.settings.engine == "kokoro"
                    else None
                ),
                "revision": self.identity_revision,
            }

        async def synthesize(self, text, destination, *, allow_online):
            if self.settings.engine == "edge-tts":
                assert allow_online
            self.calls.append(text)
            if len(self.calls) == self.fail_at:
                raise StudioError("FAKE_NETWORK_FAILURE", "synthetic injected failure", 4)
            if len(self.calls) == self.interrupt_at:
                raise asyncio.CancelledError
            samples = 5001 + len(self.calls) * 37  # Deliberately nondeterministic by request.
            with wave.open(str(destination), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes(
                    b"\xd0\x07\x30\xf8" * (samples // 2) + b"\xd0\x07" * (samples % 2)
                )
            return {
                "samples": samples,
                "sample_rate": 24000,
                "channels": 1,
                "boundaries": [
                    {
                        "offset_ticks": 0,
                        "duration_ticks": samples * 10_000_000 // 24000,
                        "text": text,
                    }
                ],
                "timing_source": "synthetic PCM sample counts",
                "model": self.identity()["model"],
                "effective_settings": {"samples": samples, "sample_rate": 24000},
            }

    settings = Settings(engine="kokoro", voice="af_heart")

    def create(*, config=settings, **kwargs):
        return tasks.create_task(
            prepare_document(text=TEXT),
            config,
            tmp_path / "tasks",
            tmp_path / "cache",
            engine=FakeEngine(config),
            chunk_bytes=64,
            **kwargs,
        )

    def run(task, **kwargs):
        return asyncio.run(tasks.run_task(task, engine_factory=FakeEngine, **kwargs))

    return FakeEngine, create, run


def test_failed_chunk_resume_skips_saved_samples_and_exports_real_pcm(harness):
    engine, create, run = harness
    task = create(pause_ms=25)
    engine.fail_at = 2
    with pytest.raises(StudioError, match="synthetic injected"):
        run(task)
    failed = read_json(task / "task.json")
    assert failed["status"] == "failed"
    first = failed["chunks"][0].copy()
    assert [c["status"] for c in failed["chunks"]] == ["completed", "failed", "pending", "pending"]
    output = run(task)
    finished = read_json(task / "task.json")
    assert len(engine.calls) == 5
    assert [c["attempts"] for c in finished["chunks"]] == [1, 2, 1, 1]
    assert finished["chunks"][0]["metadata"] == first["metadata"]
    assert finished["chunks"][0]["artifact_sha256"] == first["artifact_sha256"]
    report = verify_job(output)
    assert report["status"] == "valid" and report["text_coverage"]["verified"]
    expected_samples = sum(c["metadata"]["samples"] for c in finished["chunks"]) + 3 * 600
    assert report["audio"]["decoded_samples_per_channel"] == expected_samples
    assert (output / "subtitle.txt").read_text() == TEXT
    assert (output / "original.txt").read_text() == TEXT
    assert (output / "plan.json").read_bytes() == (task / "plan.json").read_bytes()
    assert read_json(output / "manifest.json")["effective_settings"]["audio_postprocessing"][
        "enabled"
    ]


def test_cache_identity_is_not_job_or_audio_hash_and_filter_only_changes_export(harness):
    engine, create, run = harness
    a, b = create(), create(audio_filter="off")
    am, bm = read_json(a / "task.json"), read_json(b / "task.json")
    assert am["job_id"] != bm["job_id"] and am["export_key"] != bm["export_key"]
    assert [c["cache_key"] for c in am["chunks"]] == [c["cache_key"] for c in bm["chunks"]]
    first, second = run(a), run(b)
    assert len(engine.calls) == 4
    assert all(c["cache_hit"] and c["attempts"] == 0 for c in read_json(b / "task.json")["chunks"])
    assert not read_json(second / "manifest.json")["effective_settings"]["audio_postprocessing"][
        "enabled"
    ]
    assert verify_job(first)["hashes_match"] and verify_job(second)["hashes_match"]
    request = read_json(next((a.parent.parent / "cache").glob("*/manifest.json")))["request"]
    assert request["text_rules_version"] == TEXT_RULE_VERSION
    for config in (
        Settings(engine="kokoro", voice="bf_emma"),
        Settings(engine="kokoro", voice="af_heart", rate="+5%"),
    ):
        changed = read_json(create(config=config) / "task.json")
        assert changed["chunks"][0]["cache_key"] != am["chunks"][0]["cache_key"]


def test_only_chunk_with_both_bad_copies_is_regenerated(harness):
    engine, create, run = harness
    task = create()
    previous_export = run(task)
    before = read_json(task / "task.json")
    bad = before["chunks"][1]
    (task / bad["path"]).write_bytes(b"truncated")
    (task.parent.parent / "cache" / bad["cache_key"] / "audio.wav").write_bytes(b"truncated")
    current_export = run(task)
    after = read_json(task / "task.json")
    assert len(engine.calls) == 5
    assert [c["attempts"] for c in after["chunks"]] == [1, 2, 1, 1]
    assert after["chunks"][1]["artifact_sha256"] != bad["artifact_sha256"]
    assert after["chunks"][0]["metadata"] == before["chunks"][0]["metadata"]
    assert current_export != previous_export
    assert verify_job(previous_export)["status"] == "valid"
    assert verify_job(current_export)["status"] == "valid"


def test_deleted_cache_recovers_from_local_checkpoints_without_synthesis(harness):
    engine, create, run = harness
    task = create()
    run(task)
    before = read_json(task / "task.json")
    for chunk in before["chunks"]:
        shutil.rmtree(task.parent.parent / "cache" / chunk["cache_key"])
    output = run(task)
    assert len(engine.calls) == 4
    assert all(c["reused_from"] == "task" for c in read_json(task / "task.json")["chunks"])
    assert verify_job(output)["status"] == "valid"


def test_bad_local_copy_recovers_from_cache(harness):
    engine, create, run = harness
    task = create()
    run(task)
    saved = read_json(task / "task.json")["chunks"][0]
    (task / saved["path"]).write_bytes(b"bad")
    run(task)
    assert len(engine.calls) == 4
    assert read_json(task / "task.json")["chunks"][0]["artifact_sha256"] == saved["artifact_sha256"]


def test_stale_active_checkpoint_and_partial_work_are_reconciled(harness):
    engine, create, run = harness
    task = create()
    engine.fail_at = 2
    with pytest.raises(StudioError):
        run(task)
    state = read_json(task / "task.json")
    state["status"] = "synthesizing"
    atomic_json(task / "task.json", state)
    (task / "work" / "partial.wav").write_bytes(b"partial")
    output = run(task)
    assert not (task / "work" / "partial.wav").exists()
    assert len(engine.calls) == 5 and verify_job(output)["status"] == "valid"


def test_failed_assembly_retries_no_inference(harness, monkeypatch):
    engine, create, run = harness
    task = create()
    original = tasks.assemble

    async def failed(*args, **kwargs):
        raise StudioError("INJECTED_ASSEMBLY", "injected", 5)

    monkeypatch.setattr(tasks, "assemble", failed)
    with pytest.raises(StudioError):
        run(task)
    assert read_json(task / "task.json")["status"] == "failed"
    assert not list((task / "exports").iterdir())
    monkeypatch.setattr(tasks, "assemble", original)
    run(task)
    assert len(engine.calls) == 4


def test_atomic_checkpoint_failure_after_cache_publication_reuses_cache(harness, monkeypatch):
    engine, create, run = harness
    task = create()
    original = tasks._save
    failed = False

    def fail_once(path, manifest):
        nonlocal failed
        if not failed and manifest["chunks"][0]["status"] == "completed":
            failed = True
            raise OSError(errno.ENOSPC, "injected disk full")
        return original(path, manifest)

    monkeypatch.setattr(tasks, "_save", fail_once)
    with pytest.raises(StudioError):
        run(task)
    run(task)
    assert len(engine.calls) == 4


def test_cancelled_async_task_pauses_and_resumes(harness):
    engine, create, run = harness
    task = create()
    engine.interrupt_at = 2
    with pytest.raises(asyncio.CancelledError):
        run(task)
    assert read_json(task / "task.json")["status"] == "paused"
    run(task)
    assert len(engine.calls) == 5


def test_safe_cancel_between_chunks_then_explicit_resume(harness):
    engine, create, run = harness
    task = create()

    def cancel_after_first(report):
        if report["current_index"] == 0:
            tasks.cancel_task(task)

    assert run(task, progress=cancel_after_first) is None
    assert read_json(task / "task.json")["status"] == "cancelled"
    assert len(engine.calls) == 1
    run(task)
    assert len(engine.calls) == 4


def test_cancel_during_verification_does_not_publish(harness, monkeypatch):
    engine, create, run = harness
    task = create()
    original = tasks.validate_export_audio

    def cancel_then_validate(*args, **kwargs):
        tasks.cancel_task(task)
        return original(*args, **kwargs)

    monkeypatch.setattr(tasks, "validate_export_audio", cancel_then_validate)
    assert run(task) is None
    assert read_json(task / "task.json")["status"] == "cancelled"
    assert not list((task / "exports").iterdir())


def test_online_resume_requires_fresh_permission_before_engine(harness):
    engine, create, run = harness
    task = create(config=Settings())
    with pytest.raises(StudioError) as error:
        run(task)
    assert error.value.code == "ONLINE_NOT_AUTHORIZED" and not engine.calls
    run(task, allow_online=True)
    with pytest.raises(StudioError) as error:
        run(task)
    assert error.value.code == "ONLINE_NOT_AUTHORIZED" and len(engine.calls) == 4


@pytest.mark.parametrize("mutation", ["input", "parameter", "engine", "snapshot"])
def test_refuse_incompatible_resume_before_synthesis(harness, mutation):
    engine, create, run = harness
    task = create()
    options = {}
    if mutation == "input":
        options["expected_input"] = prepare_document(text="Other input.")
    elif mutation == "parameter":
        options["expected_options"] = {"rate": "+1%"}
    elif mutation == "engine":
        engine.identity_revision += 1
    else:
        (task / "tts.txt").write_text("Changed!")
    with pytest.raises((InputError, ValidationError)):
        run(task, **options)
    assert not engine.calls


@pytest.mark.parametrize("lock", ["task", "cache"])
def test_repeated_launch_fails_busy_without_writing(harness, lock):
    engine, create, run = harness
    task = create()
    if lock == "task":
        path = task / ".task.lock"
    else:
        cache = task.parent.parent / "cache"
        cache.mkdir()
        path = cache / ".runner.lock"
    with exclusive_lock(path), pytest.raises(StudioError) as error:
        run(task)
    assert error.value.code == "TASK_BUSY" and not engine.calls


def test_size_preflight_refuses_unreadable_plan(harness, monkeypatch):
    _, create, _ = harness
    monkeypatch.setattr(tasks, "MAX_JSON_BYTES", 200)
    with pytest.raises(InputError, match="16 MiB"):
        create()


def test_cli_local_preview_and_old_commands(capsys):
    parser = build_parser()
    args = parser.parse_args(["long", "--text", TEXT, "--engine", "kokoro", "--preview"])
    assert args.kokoro_audio_filter == "on"
    assert main(["long", "--text", TEXT, "--engine", "kokoro", "--preview"]) == 0
    assert "SHA256" in capsys.readouterr().out
    assert main(["long", "--text", TEXT, "--out-dir", "unused"]) == 3
    assert main(["preview", "--text", "A short preview."]) == 0
    assert parser.parse_args(["tasks", "resume", "/tmp/task"]).allow_online is False


def test_schema3_manifest_tampering_is_rejected(harness):
    _, create, run = harness
    task = create()
    output = run(task)
    manifest = read_json(output / "manifest.json")
    manifest["stream"]["audio_bytes"] += 1
    atomic_json(output / "manifest.json", manifest)
    with pytest.raises(ValidationError):
        verify_job(output)


def test_resume_retains_actual_output_when_shared_cache_was_regenerated(harness):
    engine, create, run = harness
    original_task = create()
    run(original_task)
    original = read_json(original_task / "task.json")["chunks"][0]
    shutil.rmtree(original_task.parent.parent / "cache" / original["cache_key"])
    other_task = create()
    run(other_task)
    newer = read_json(other_task / "task.json")["chunks"][0]
    assert newer["artifact_sha256"] != original["artifact_sha256"]
    run(original_task)
    retained = read_json(original_task / "task.json")["chunks"][0]
    assert retained["artifact_sha256"] == original["artifact_sha256"]
    assert retained["metadata"] == original["metadata"]
    assert len(engine.calls) == 5


def test_changed_input_during_synthesis_never_publishes(harness):
    _, create, run = harness
    task = create()

    def mutate_original(report):
        (task / "original.txt").write_text("Changed original during execution.")

    with pytest.raises(ValidationError):
        run(task, progress=mutate_original)
    assert not list((task / "exports").iterdir())


def test_duration_limit_stops_before_remaining_requests(harness, monkeypatch):
    engine, create, run = harness
    task = create()
    monkeypatch.setattr(tasks, "MAX_TASK_SAMPLES", 6000)
    with pytest.raises(ValidationError, match="两小时"):
        run(task)
    assert len(engine.calls) == 2
    assert not list((task / "exports").iterdir())


@pytest.mark.parametrize("field", ["model", "export_key", "filter", "chunk_model"])
def test_export_identity_cannot_silently_diverge(harness, field):
    _, create, run = harness
    output = run(create())
    manifest = read_json(output / "manifest.json")
    if field == "model":
        manifest["model"]["sha256"] = "broken"
    elif field == "export_key":
        manifest["export_key"] = "a" * 64
    elif field == "filter":
        manifest["effective_settings"]["audio_postprocessing"]["enabled"] = False
    else:
        manifest["chunks"][0]["metadata"]["model"]["voices_sha256"] = "c" * 64
    atomic_json(output / "manifest.json", manifest)
    with pytest.raises(ValidationError):
        verify_job(output)
