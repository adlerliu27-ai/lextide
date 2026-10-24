"""G4 application boundaries; synthesis is mocked and no online service is used."""

import asyncio
import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

from lextide.desktop import service, worker
from lextide.engines.base import EngineCapabilities
from lextide.errors import InputError, StudioError, ValidationError
from lextide.longform import tasks
from lextide.longform.storage import read_json


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def payload(tmp_path):
    return {
        "command": "start",
        "text": "The first sentence. The second sentence.",
        "settings": {"engine": "kokoro", "voice": "af_heart", "rate": "+0%"},
        "out_dir": str(tmp_path / "outputs"),
        "cache_dir": str(tmp_path / "cache"),
    }


@pytest.fixture
def mocked_runner(monkeypatch):
    """Task creation and persisted validation are real; TTS/run is explicitly mocked."""
    calls = []

    async def fake_run(task, **kwargs):
        calls.append((task, kwargs))
        return None

    monkeypatch.setattr(service, "run_task", fake_run)
    return calls


def test_preview_matches_exact_g3_cleanup_and_plan(payload):
    payload["text"] = "\ufeff  First\tline.\r\n\r\nSecond line.  "
    result = service.preview(payload)
    assert result["text"] == "First line.\n\nSecond line."
    assert result["sha256"] == sha(result["text"])
    assert result["sentence_count"] == 2
    assert result["chunk_count"] == 1
    assert result["changes"]
    assert result["audio_filter"] == "on" and result["pause_ms"] == 0


@pytest.mark.parametrize("text", ["", "你好", "A" * (1024 * 1024 + 1)])
def test_preview_preserves_core_input_limits(payload, text):
    payload["text"] = text
    with pytest.raises(InputError):
        service.preview(payload)


@pytest.mark.parametrize("engine", ["edge-tts", "kokoro"])
def test_missing_or_stale_confirmation_never_creates_task(payload, engine, monkeypatch):
    payload["settings"] = {"engine": engine}
    payload["approved_sha256"] = sha("Previously reviewed text.")
    payload["allow_online"] = True
    monkeypatch.setattr(service, "LongEngine", lambda *_a, **_k: pytest.fail("engine constructed"))
    with pytest.raises(StudioError, match="Review the current text"):
        asyncio.run(service.dispatch(payload))
    assert not Path(payload["out_dir"]).exists()


@pytest.mark.parametrize("consent", [False, None, "true", 1])
def test_edge_requires_explicit_current_boolean_consent(payload, consent, monkeypatch):
    payload.update(settings={"engine": "edge-tts"}, approved_sha256=sha(payload["text"]))
    payload["allow_online"] = consent
    monkeypatch.setattr(service, "LongEngine", lambda *_a, **_k: pytest.fail("engine constructed"))
    with pytest.raises(StudioError) as caught:
        asyncio.run(service.dispatch(payload))
    assert caught.value.code == "ONLINE_NOT_AUTHORIZED"


def test_start_preserves_import_and_review_provenance(payload, mocked_runner):
    original = "The original source sentence."
    payload.update(
        approved_sha256=sha(payload["text"]),
        provenance={
            "text": original,
            "source_name": "article.md",
            "source_sha256": "a" * 64,
            "format": "md",
            "changes": ["Markdown headings converted to text"],
        },
    )
    events = []
    result = asyncio.run(service.dispatch(payload, events.append))
    task = Path(result["task_path"])
    assert (task / "source-extracted.txt").read_text() == original
    assert (task / "original.txt").read_text() == payload["text"]
    review = read_json(task / "document-review.json")
    assert review["source"]["source_sha256"] == "a" * 64
    assert review["title"] == "article.md"
    assert review["extracted_sha256"] == sha(original)
    assert review["approved_tts_sha256"] == sha(payload["text"])
    edit = review["review_edits"][0]
    left, right = edit["original_span"]
    assert original[:left] + edit["after"] + original[right:] == payload["text"]
    assert events[0] == {"event": "task_created", "task_path": str(task)}
    assert mocked_runner[0][1]["allow_online"] is False
    assert tasks.task_status(task)["status"] == "created"


@pytest.mark.parametrize("engine", ["edge-tts", "kokoro"])
def test_cleaning_snapshot_and_manual_edit_survive_task_creation(payload, mocked_runner, engine):
    original = "**Practice** every day.\r\n每天练习。\r\n"
    cleaned = asyncio.run(
        service.dispatch({"command": "clean", "mode": "english", "text": original})
    )
    reviewed = cleaned["cleaned_text"] + " This sentence is a manual edit."
    payload.update(
        text=reviewed,
        settings={"engine": engine},
        approved_sha256=sha(reviewed),
        allow_online=engine == "edge-tts",
        provenance={
            "text": original,
            "cleaning": {**cleaned, "mode": "english", "original_sha256": sha(original)},
        },
    )
    result = asyncio.run(service.dispatch(payload))
    task = Path(result["task_path"])
    assert (task / "source-extracted.txt").read_bytes() == original.encode()
    assert (task / "tts.txt").read_bytes() == reviewed.encode()
    record = read_json(task / "document-review.json")
    assert record["text_preparation"]["cleaned_text"] == cleaned["cleaned_text"]
    assert record["text_preparation"]["cleaned_sha256"] == sha(cleaned["cleaned_text"])
    assert record["reviewed_sha256"] == record["approved_tts_sha256"] == sha(reviewed)
    assert mocked_runner[0][1]["allow_online"] is (engine == "edge-tts")


def test_user_session_title_is_bounded_and_persisted_for_history(payload, mocked_runner):
    payload.update(
        approved_sha256=sha(payload["text"]),
        provenance={
            "text": payload["text"],
            "source_name": "original.txt",
            "title": "Session" * 50,
        },
    )
    result = asyncio.run(service.dispatch(payload))
    task = Path(result["task_path"])
    review = read_json(task / "document-review.json")
    assert review["title"] == ("Session" * 50)[:256]
    assert review["source"]["source_name"] == "original.txt"
    item = service.history({"paths": [str(task)]})["items"][0]
    assert item["title"] == review["title"]


def test_resume_immutable_settings_and_fresh_authorization(payload, mocked_runner):
    payload["approved_sha256"] = sha(payload["text"])
    initial = asyncio.run(service.dispatch(payload))
    path = Path(initial["task_path"])
    before = (path / "task.json").read_bytes()
    resume = {"command": "resume", "task_path": str(path)}
    current = service.status(resume)
    assert current["review_text"] == payload["text"]
    assert current["settings"]["rate"] == "+0%"
    with pytest.raises(StudioError):
        asyncio.run(service.dispatch(resume))
    resume.update(approved_sha256=current["sha256"], settings={"rate": "+10%"})
    with pytest.raises(InputError, match="saved task settings"):
        asyncio.run(service.dispatch(resume))
    assert (path / "task.json").read_bytes() == before
    resume.pop("settings")
    asyncio.run(service.dispatch(resume))
    assert len(mocked_runner) == 2
    assert mocked_runner[-1][1]["expected_options"]["rate"] == "+0%"
    assert mocked_runner[-1][1]["expected_input"].tts_text == payload["text"]


def test_edge_resume_requires_current_consent(payload, mocked_runner):
    payload.update(
        settings={"engine": "edge-tts"},
        allow_online=True,
        approved_sha256=sha(payload["text"]),
    )
    result = asyncio.run(service.dispatch(payload))
    resume = {
        "command": "resume",
        "task_path": result["task_path"],
        "approved_sha256": payload["approved_sha256"],
        "allow_online": False,
    }
    with pytest.raises(StudioError) as caught:
        asyncio.run(service.dispatch(resume))
    assert caught.value.code == "ONLINE_NOT_AUTHORIZED"
    assert len(mocked_runner) == 1


def test_cancel_uses_g3_persistent_marker(payload, mocked_runner):
    payload["approved_sha256"] = sha(payload["text"])
    result = asyncio.run(service.dispatch(payload))
    task = Path(result["task_path"])
    cancelled = asyncio.run(service.dispatch({"command": "cancel", "task_path": str(task)}))
    assert cancelled["status"] == "cancel_requested"
    assert tasks.task_status(task)["status"] == "cancelled"
    assert (task / "cancel.request").is_file()


def test_history_lists_schema_1_2_3_and_unfinished_tasks_without_verifying(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "verify_job", lambda *_a, **_k: pytest.fail("eager verification"))
    for schema in (1, 2, 3):
        job = tmp_path / f"job-{schema}"
        job.mkdir()
        (job / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": schema,
                    "status": "completed",
                    "engine": {"id": "edge-tts"},
                    "created_at": f"2026-10-0{schema}",
                }
            )
        )
        (job / "validation.json").write_text(json.dumps({"audio": {"duration_ms": 1234}}))
    task = tmp_path / "task-paused"
    task.mkdir()
    (task / "task.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "kind": "longform-task",
                "status": "paused",
                "config": {"settings": {"engine": "kokoro"}},
                "chunks": [{"status": "completed"}, {"status": "pending"}],
            }
        )
    )
    (tmp_path / "symlink-job").symlink_to(tmp_path / "job-1", target_is_directory=True)
    results = service.history({"roots": [str(tmp_path)]})
    assert len(results["items"]) == 4
    assert {item["schema_version"] for item in results["items"]} == {1, 2, 3}
    assert all(item["verified"] is False for item in results["items"])
    assert results["items"][0]["duration_seconds"] == 1.234
    assert results["items"][-1]["status"] == "paused"
    assert results["items"][-1]["completed_chunks"] == 1
    assert results["items"][-1]["total_chunks"] == 2


def test_full_model_hash_failure_overrides_static_available_capability(monkeypatch):
    monkeypatch.setattr(
        service.KokoroEngine,
        "capabilities",
        lambda self: EngineCapabilities("kokoro", "test", False, True, True, ("af_heart",)),
    )
    monkeypatch.setattr(
        service,
        "model_status",
        lambda *_args, **_kwargs: {
            "installed": True,
            "ready": False,
            "valid": False,
            "error": "MODEL_INVALID",
        },
    )
    result = service.engines({})
    assert result["engines"][1]["installed"] is True
    assert result["engines"][1]["available"] is False


def test_history_depth_and_explicit_open_are_bounded(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "manifest.json").write_text(json.dumps({"schema_version": 1, "status": "completed"}))
    assert service.history({"roots": [str(tmp_path)]})["items"] == []
    assert len(service.history({"paths": [str(deep)]})["items"]) == 1


def test_srt_keeps_real_gap_and_millisecond_precision():
    cues = service.parse_srt(
        "1\n00:00:00,123 --> 00:00:01,222\nFirst.\n\n2\n00:00:03,456 --> 00:00:04,789\nSecond.\n"
    )
    assert cues[0]["start_ms"] == 123
    assert cues[0]["end_ms"] == 1222
    assert cues[1]["start_ms"] == 3456
    assert cues[1]["end_ms"] == 4789


@pytest.mark.parametrize(
    "srt",
    [
        "",
        "1\n00:00:00,000 --> 00:00:00,000\nBad.",
        "2\n00:00:00,000 --> 00:00:01,000\nBad.",
        "1\n00:60:00,000 --> 00:60:01,000\nBad.",
        "1\n00:00:00,000 --> 00:00:02,000\nFirst.\n\n2\n00:00:01,000 --> 00:00:03,000\nOverlap.",
    ],
)
def test_srt_rejects_invalid_timing(srt):
    with pytest.raises(ValidationError):
        service.parse_srt(srt)


def test_verify_never_offers_incomplete_task(payload, mocked_runner, monkeypatch):
    payload["approved_sha256"] = sha(payload["text"])
    created = asyncio.run(service.dispatch(payload))
    monkeypatch.setattr(service, "verify_job", lambda *_a, **_k: pytest.fail("incomplete played"))
    with pytest.raises(ValidationError, match="no completed output"):
        service.verify({"path": created["task_path"]})


def test_worker_rejects_nonprivate_oversize_and_symlink_request(tmp_path):
    path = tmp_path / "payload.json"
    path.write_text("{}")
    path.chmod(0o644)
    with pytest.raises(InputError, match="private"):
        worker.read_request(path)
    path.chmod(0o600)
    assert worker.read_request(path) == {}
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        worker.read_request(link)
    with path.open("wb") as stream:
        stream.truncate(worker.MAX_REQUEST_BYTES + 1)
    with pytest.raises(InputError, match="4 MiB"):
        worker.read_request(path)


def test_worker_unknown_failure_never_dumps_private_text(tmp_path, monkeypatch, capsys):
    path = tmp_path / "payload.json"
    path.write_text('{"command":"preview"}')
    path.chmod(0o600)

    async def failed(*_args):
        raise RuntimeError("PRIVATE DOCUMENT TEXT AND ACCESS TOKEN")

    monkeypatch.setattr(worker, "dispatch", failed)
    assert worker.main([str(path)]) == 1
    result = capsys.readouterr()
    assert "PRIVATE" not in result.out + result.err
    assert json.loads(result.out)["code"] == "DESKTOP_WORKER_ERROR"


def test_real_worker_process_preview_protocol(tmp_path):
    path = tmp_path / "payload.json"
    path.write_text(json.dumps({"command": "preview", "text": "A real worker preview."}))
    path.chmod(0o600)
    result = subprocess.run(
        [sys.executable, "-m", "lextide.desktop.worker", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["event"] == "result" and response["command"] == "preview"
    assert response["data"]["sha256"] == sha("A real worker preview.")
    assert not result.stderr


@pytest.mark.parametrize("contents,successful", [("English input.\n", True), ("", False)])
def test_real_import_worker_limits_and_safe_parser_errors(tmp_path, contents, successful):
    document = tmp_path / "document.txt"
    document.write_text(contents)
    path = tmp_path / "import.json"
    path.write_text(json.dumps({"command": "import", "path": str(document)}))
    path.chmod(0o600)
    result = subprocess.run(
        [sys.executable, "-m", "lextide.desktop.worker", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    response = json.loads(result.stdout)
    if successful:
        assert result.returncode == 0, result.stderr
        assert response["data"]["text"] == contents
    else:
        assert result.returncode == 2
        assert response["code"] == "DOCUMENT_IMPORT_FAILED"
        assert response["message"] != "The operation failed."


def test_g3_pcm_export_and_service_verification_are_real(payload, monkeypatch):
    """REAL G3/FFmpeg/manifest/SRT path; TTS alone emits explicitly synthetic PCM."""

    class SyntheticPCMEngine(service.LongEngine):
        async def synthesize(self, text, destination, *, allow_online):
            assert not allow_online
            samples = 24000
            with wave.open(str(destination), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes((b"\xd0\x07" * 30 + b"\x30\xf8" * 30) * 400)
            return {
                "samples": samples,
                "sample_rate": 24000,
                "channels": 1,
                "boundaries": [{"offset_ticks": 0, "duration_ticks": 10_000_000, "text": text}],
                "timing_source": "MOCKED synthetic PCM sample counts",
                "model": self.identity()["model"],
                "effective_settings": {"samples": samples, "sample_rate": 24000},
            }

    async def mocked_run(task, **kwargs):
        return await tasks.run_task(task, engine_factory=SyntheticPCMEngine, **kwargs)

    monkeypatch.setattr(service, "LongEngine", SyntheticPCMEngine)
    monkeypatch.setattr(service, "run_task", mocked_run)
    payload["approved_sha256"] = sha(payload["text"])
    events = []
    result = asyncio.run(service.dispatch(payload, events.append))
    assert result["status"] == "completed"
    assert result["completed_chunks"] == result["total_chunks"] == 1
    checked = service.verify({"path": result["task_path"]})
    assert checked["report"]["status"] == "valid"
    assert checked["report"]["network_used"] is False
    assert (checked["engine"], checked["voice"], checked["rate"]) == ("kokoro", "af_heart", "+0%")
    assert checked["cues"][0]["text"] == payload["text"]
    assert Path(checked["audio_path"]).is_file()
    assert checked["duration_seconds"] > 0.9
    assert any(event.get("completed_chunks") == 1 for event in events)
    # Integrity verification is real: a changed SRT is refused, never offered to the player.
    with Path(checked["srt_path"]).open("a") as stream:
        stream.write("tampered")
    with pytest.raises(ValidationError):
        service.verify({"path": result["task_path"]})


def test_owned_worker_sigint_persists_paused_g3_checkpoint(payload, tmp_path):
    """REAL subprocess/signal/checkpoint; engine synthesis explicitly MOCKED to wait."""
    payload["approved_sha256"] = sha(payload["text"])
    path = tmp_path / "pause-request.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    child_code = """
import asyncio
from lextide.desktop import service, worker
from lextide.longform import tasks
class WaitingMockEngine(service.LongEngine):
    async def synthesize(self, *args, **kwargs):
        await asyncio.sleep(60)
service.LongEngine = WaitingMockEngine
async def mocked_run(task, **kwargs):
    return await tasks.run_task(task, engine_factory=WaitingMockEngine, **kwargs)
service.run_task = mocked_run
raise SystemExit(worker.main())
"""
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    events = []
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if selector.select(timeout=0.2):
                line = process.stdout.readline()
                if line:
                    event = json.loads(line)
                    events.append(event)
                    if event.get("status") == "synthesizing":
                        break
                elif process.poll() is not None:
                    break
        assert any(event.get("status") == "synthesizing" for event in events), events
        process.send_signal(signal.SIGINT)
        output, error = process.communicate(timeout=10)
        events.extend(json.loads(line) for line in output.splitlines())
        assert process.returncode == 0, error
        result = next(event["data"] for event in events if event["event"] == "result")
        assert result["status"] == "paused"
        assert tasks.task_status(Path(result["task_path"]))["status"] == "paused"
        assert result["completed_chunks"] == 0
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
