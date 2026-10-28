"""Pre-submission reading policy: no network, model, or actual TTS calls."""

import asyncio
import hashlib
from dataclasses import asdict

import pytest

from lextide.config import Settings
from lextide.desktop import service
from lextide.errors import InputError
from lextide.jobs import synthesize_job
from lextide.longform import tasks
from lextide.longform.document import prepare_document
from lextide.text import prepare_text

MIXED = [
    "A student observes a bird.\n学生观察一只鸟。",
    "A student observes a bird. 学生观察一只鸟。",
    "A student observes a bird.学生观察一只鸟。",
    "In 1921, Nora counted 1,500 birds.1921年，Nora数了1,500只鸟。",
    "Our guide calls the lake 明湖 in the morning.",
    "An ancient character is \U00020000.",
    "The Chinese year includes 〇.",
]
CONFIGS = [Settings(), Settings(engine="kokoro", voice="af_heart")]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("text", MIXED)
@pytest.mark.parametrize("settings", CONFIGS)
def test_service_rejects_mixed_before_engine_or_job(text, settings, tmp_path, monkeypatch):
    calls = []

    def engine(*args, **kwargs):
        calls.append("engine")
        raise AssertionError("Text reached the synthesis boundary")

    monkeypatch.setattr(service, "LongEngine", engine)
    request = {
        "command": "start",
        "settings": asdict(settings),
        "text": text,
        "approved_sha256": sha(text),
        "allow_online": True,
        "out_dir": str(tmp_path / "jobs"),
    }
    with pytest.raises(InputError, match="Chinese text remains"):
        asyncio.run(service.start(request, lambda *_: None))
    assert calls == []
    assert not (tmp_path / "jobs").exists()


@pytest.mark.parametrize("settings", CONFIGS)
def test_short_job_guard_precedes_media_and_engine(settings, tmp_path, monkeypatch):
    prepared = prepare_text(text=MIXED[0])
    calls = []

    class Engine:
        online = settings.engine == "edge-tts"

        async def synthesize(self, *args, **kwargs):
            calls.append("synthesize")

    monkeypatch.setattr(
        "lextide.jobs.resolve_media_tools",
        lambda *_: pytest.fail("media accessed before rejection"),
    )
    with pytest.raises(InputError, match="Chinese text remains"):
        asyncio.run(
            synthesize_job(
                prepared,
                settings,
                tmp_path / "jobs",
                Engine(),
                allow_online=True,
                confirmed_sha256=prepared.sha256,
            )
        )
    assert calls == [] and not (tmp_path / "jobs").exists()


def test_create_task_guard_precedes_any_output(tmp_path):
    prepared = prepare_document(text=MIXED[0])

    class Engine:
        def identity(self):
            pytest.fail("engine identity must not be accessed")

    with pytest.raises(InputError, match="Chinese text remains"):
        tasks.create_task(
            prepared, Settings(), tmp_path / "jobs", tmp_path / "cache", engine=Engine()
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("settings", CONFIGS)
@pytest.mark.parametrize(
    "text",
    [
        "Don't stop. Python 3.13, C++, API and SHA-256 remain useful.",
        'The teacher said, "Practice every day."',
        "Nora counted 1,500 birds in 1921.",
        "A café serves crème brûlée.",
    ],
)
def test_english_text_is_accepted_without_mutation(settings, text):
    from lextide.reading import validate_reading_text

    before = text.encode()
    validate_reading_text(text, settings)
    assert text.encode() == before


@pytest.mark.parametrize("text", ["", " \n\t", None])
def test_empty_reading_text_is_rejected(text):
    from lextide.reading import validate_reading_text

    with pytest.raises(InputError, match="Reading text is empty"):
        validate_reading_text(text, Settings())


@pytest.mark.parametrize("voice", ["zh-CN-XiaoxiaoNeural", "ja-JP-NanamiNeural"])
def test_non_english_voice_is_not_subject_to_global_han_ban(voice):
    from lextide.reading import validate_reading_text

    # The existing Settings.validate English-voice restriction is intentionally
    # unchanged. This isolates policy scope for future engine locale support.
    settings = Settings(voice=voice)
    validate_reading_text(MIXED[0], settings)
    with pytest.raises(InputError, match="Chinese text remains"):
        validate_reading_text(MIXED[0], settings, english_only=True)


@pytest.mark.parametrize("flag", ["false", "true", 1, None])
def test_service_rejects_non_boolean_english_only(flag, tmp_path, monkeypatch):
    text = "Read this sentence."
    monkeypatch.setattr(service, "LongEngine", lambda *_a, **_k: pytest.fail("engine constructed"))
    with pytest.raises(InputError, match="English Only must be a boolean"):
        asyncio.run(
            service.start(
                {
                    "text": text,
                    "english_only": flag,
                    "approved_sha256": sha(text),
                    "allow_online": True,
                    "out_dir": str(tmp_path / "jobs"),
                },
                lambda *_: None,
            )
        )
    assert not list(tmp_path.iterdir())


def test_service_requires_review_of_exact_normalized_text(tmp_path, monkeypatch):
    from lextide.errors import StudioError

    text = "  Read this sentence.  "
    monkeypatch.setattr(service, "LongEngine", lambda *_a, **_k: pytest.fail("engine constructed"))
    with pytest.raises(StudioError) as error:
        asyncio.run(
            service.start(
                {
                    "text": text,
                    "approved_sha256": sha(text.strip()),
                    "allow_online": True,
                    "out_dir": str(tmp_path / "jobs"),
                },
                lambda *_: None,
            )
        )
    assert error.value.code == "TEXT_REVIEW_REQUIRED"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("settings", CONFIGS)
def test_service_passes_exact_approved_english_bytes(settings, tmp_path, monkeypatch):
    text = 'The guide said, "Observe carefully."'
    received = []
    task = tmp_path / "task"
    task.mkdir()
    monkeypatch.setattr(service, "LongEngine", lambda *_a, **_k: object())

    def create(prepared, *_args, **_kwargs):
        received.append(prepared.tts_text)
        return task

    async def run(_task, request, allowed, _emit, *, expected_input, expected_options):
        assert allowed is (settings.engine == "edge-tts")
        assert expected_input.tts_text == request["text"] == text
        assert request["approved_sha256"] == sha(text)
        return {"status": "mocked-boundary"}

    monkeypatch.setattr(service, "create_task", create)
    monkeypatch.setattr(service, "_run", run)
    result = asyncio.run(
        service.start(
            {
                "text": text,
                "settings": asdict(settings),
                "approved_sha256": sha(text),
                "allow_online": settings.engine == "edge-tts",
                "english_only": True,
                "out_dir": str(tmp_path / "jobs"),
            },
            lambda *_: None,
        )
    )
    assert received == [text]
    assert result["status"] == "mocked-boundary"


def test_historical_mixed_task_is_rejected_without_manifest_rewrite(tmp_path, monkeypatch):
    """Create a valid old manifest with the guard disabled, then restore the guard."""
    from lextide.reading import validate_reading_text

    class Engine:
        def identity(self):
            return {"engine_id": "edge-tts", "engine_version": "offline-test"}

    monkeypatch.setattr(tasks, "resolve_media_tools", lambda *_: ("test-ffmpeg", "test-ffprobe"))
    monkeypatch.setattr(tasks, "_decoder_version", lambda *_: "offline-test")
    monkeypatch.setattr(tasks, "validate_reading_text", lambda *_a, **_k: None)
    task = tasks.create_task(
        prepare_document(text=MIXED[0]),
        Settings(),
        tmp_path / "jobs",
        tmp_path / "cache",
        engine=Engine(),
    )
    protected = {name: (task / name).read_bytes() for name in ("task.json", "tts.txt", "plan.json")}
    monkeypatch.setattr(tasks, "validate_reading_text", validate_reading_text)
    calls = []

    def engine_factory(*_args, **_kwargs):
        calls.append("engine")
        raise AssertionError("Unsafe resume reached the engine")

    with pytest.raises(InputError, match="Chinese text remains"):
        asyncio.run(tasks.run_task(task, allow_online=True, engine_factory=engine_factory))
    monkeypatch.setattr(service, "_run", engine_factory)
    with pytest.raises(InputError, match="Chinese text remains"):
        asyncio.run(
            service.resume(
                {
                    "task_path": str(task),
                    "approved_sha256": sha(MIXED[0]),
                    "allow_online": True,
                },
                lambda *_: None,
            )
        )
    assert calls == []
    assert all((task / name).read_bytes() == content for name, content in protected.items())


def test_error_does_not_include_private_text(tmp_path, monkeypatch):
    text = "PRIVATE-DOCUMENT-CANARY. 中文。"
    monkeypatch.setattr(service, "LongEngine", lambda *_a, **_k: pytest.fail("engine constructed"))
    with pytest.raises(InputError) as error:
        asyncio.run(
            service.start(
                {
                    "text": text,
                    "approved_sha256": sha(text),
                    "allow_online": True,
                    "out_dir": str(tmp_path / "jobs"),
                },
                lambda *_: None,
            )
        )
    assert "PRIVATE-DOCUMENT-CANARY" not in str(error.value)
