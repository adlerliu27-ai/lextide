"""BUG-G5-1 service integration: MOCKED Edge speech, real local task/media validation."""

import asyncio
import hashlib
import subprocess
import wave
from pathlib import Path

import pytest

from lextide.audio import resolve_media_tools
from lextide.desktop import service
from lextide.engines.base import EngineResult, SentenceBoundary
from lextide.engines.edge import EdgeEngine
from lextide.errors import StudioError, ValidationError
from lextide.longform.storage import read_json


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def synthetic_mp3(tmp_path_factory):
    """Encode a local waveform once; this is not synthesized or intelligible speech."""
    try:
        ffmpeg, _ = resolve_media_tools()
    except StudioError:
        pytest.skip("Local ffmpeg/ffprobe required for the synthetic Edge integration")
    directory = tmp_path_factory.mktemp("g5-synthetic-edge")
    source = directory / "source.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes((b"\xd0\x07" * 30 + b"\x30\xf8" * 30) * 800)
    target = directory / "source.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(target),
        ],
        check=True,
        timeout=20,
    )
    return target.read_bytes()


@pytest.fixture
def edge_response(monkeypatch, synthetic_mp3):
    """Replace only the online engine; LongEngine, tasks, export and verify stay real."""
    calls = []

    def install(boundaries):
        async def synthesize(self, text, destination, settings, *, allow_online):
            calls.append({"text": text, "allow_online": allow_online})
            assert allow_online is True
            destination.write_bytes(synthetic_mp3)
            return EngineResult(
                boundaries,
                True,
                len(synthetic_mp3),
                hashlib.sha256(synthetic_mp3).hexdigest(),
            )

        monkeypatch.setattr(EdgeEngine, "synthesize", synthesize)
        return calls

    return install


def _request(tmp_path, text):
    request = {
        "text": text,
        "settings": {"engine": "edge-tts", "voice": "en-US-GuyNeural", "rate": "+0%"},
        "out_dir": str(tmp_path / "outputs"),
        "cache_dir": str(tmp_path / "cache"),
        "allow_online": True,
    }
    preview = service.preview(request)
    assert preview["text"] == text  # Formatting markers are not rewritten before review.
    request["approved_sha256"] = preview["sha256"]
    return request


def _assert_review_and_transmission(request, calls, result):
    """The reviewer, mocked transport and persisted artifacts see identical source text."""
    text = request["text"]
    assert calls == [{"text": text, "allow_online": True}]
    assert result["status"] == "completed"
    assert result["completed_chunks"] == result["total_chunks"] == 1
    task = Path(result["task_path"])
    export = Path(result["export_path"])
    for directory in (task, export):
        assert (directory / "tts.txt").read_text(encoding="utf-8") == text
    review = read_json(task / "document-review.json")
    manifest = read_json(export / "manifest.json")
    assert (
        request["approved_sha256"]
        == review["reviewed_sha256"]
        == review["approved_tts_sha256"]
        == _sha(calls[0]["text"])
        == manifest["consent"]["confirmed_text_sha256"]
        == manifest["artifacts"]["tts.txt"]["sha256"]
        == _sha((task / "tts.txt").read_text(encoding="utf-8"))
    )
    checked = service.verify({"path": str(task)})
    assert checked["report"]["status"] == "valid"
    assert checked["report"]["network_used"] is False
    assert len(calls) == 1  # Local verification cannot send the source again.
    return checked


@pytest.mark.parametrize(
    "text,native",
    [
        ("**First** part, then text.", "First** part, then text."),
        ("*Italic* lead then text.", "Italic* lead then text."),
        ("- Bullet item one.", "Bullet item one."),
        ("• Bullet with dot.", "Bullet with dot."),
        ("> Quoted line here.", "Quoted line here."),
        ('"Quoted" start here.', 'Quoted" start here.'),
        ("`code` at start here.", "code` at start here."),
        ("— Dash leads this.", "Dash leads this."),
        ("# Heading line.", "# Heading line."),
        ("1. Numbered item one.", "1. Numbered item one."),
        ("🙂 Emoji leads this.", "🙂 Emoji leads this."),
        ("A sentence with **bold** in it.", "A sentence with **bold** in it."),
    ],
    ids=[
        "bold",
        "italic",
        "hyphen",
        "bullet",
        "quote-block",
        "double-quote",
        "code",
        "em-dash",
        "heading-retained",
        "number-retained",
        "emoji-retained",
        "inline-bold-retained",
    ],
)
def test_start_accepts_reported_edge_prefix_behavior(tmp_path, edge_response, text, native):
    request = _request(tmp_path, text)
    calls = edge_response([SentenceBoundary(1_230_000, 14_770_000, native)])
    result = asyncio.run(service.start(request, lambda _: None))
    checked = _assert_review_and_transmission(request, calls, result)
    assert checked["cues"] == [{"index": 1, "start_ms": 123, "end_ms": 1600, "text": native}]


def test_start_preserves_full_markers_when_edge_returns_them(tmp_path, edge_response):
    text = '**First** part has *italic*, `code`, and "quotes".'
    request = _request(tmp_path, text)
    calls = edge_response([SentenceBoundary(0, 16_000_000, text)])
    result = asyncio.run(service.start(request, lambda _: None))
    checked = _assert_review_and_transmission(request, calls, result)
    assert checked["cues"][0]["text"] == text


def test_start_accepts_multiple_marked_sentences_without_changing_gaps(tmp_path, edge_response):
    text = "**First** part, then text. • Item two follows. A **bold** word stays."
    native = [
        SentenceBoundary(0, 5_000_000, "First** part, then text."),
        SentenceBoundary(7_000_000, 5_000_000, "Item two follows."),
        SentenceBoundary(14_000_000, 5_000_000, "A **bold** word stays."),
    ]
    request = _request(tmp_path, text)
    calls = edge_response(native)
    result = asyncio.run(service.start(request, lambda _: None))
    checked = _assert_review_and_transmission(request, calls, result)
    assert [(cue["start_ms"], cue["end_ms"]) for cue in checked["cues"]] == [
        (0, 500),
        (700, 1200),
        (1400, 1900),
    ]
    assert [cue["text"] for cue in checked["cues"]] == [boundary.text for boundary in native]


@pytest.mark.parametrize(
    "native",
    [
        ["First** part, then.", "Item two follows."],
        ["First** part, then text."],
        ["First** part then text.", "Item two follows."],
    ],
    ids=["missing-word", "missing-sentence", "missing-body-comma"],
)
def test_start_still_fails_when_edge_omits_real_content(tmp_path, edge_response, native):
    text = "**First** part, then text. • Item two follows."
    request = _request(tmp_path, text)
    calls = edge_response(
        [
            SentenceBoundary(index * 8_000_000, 7_000_000, sentence)
            for index, sentence in enumerate(native)
        ]
    )
    events = []
    with pytest.raises(ValidationError) as caught:
        asyncio.run(service.start(request, events.append))
    assert caught.value.code == "VALIDATION_FAILED"
    assert calls == [{"text": text, "allow_online": True}]
    task = Path(next(event["task_path"] for event in events if event["event"] == "task_created"))
    status = service.status({"task_path": str(task)})
    assert status["status"] == "failed"
    assert status["completed_chunks"] == 0
    assert status["last_error"]["code"] == "VALIDATION_FAILED"
    assert not list((task / "exports").iterdir())
    assert (task / "tts.txt").read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    "gate,error",
    [
        ("not-reviewed", "TEXT_REVIEW_REQUIRED"),
        ("online-declined", "ONLINE_NOT_AUTHORIZED"),
        ("edited-after-review", "TEXT_REVIEW_REQUIRED"),
    ],
)
def test_marker_text_without_current_authorization_never_reaches_edge(
    tmp_path, monkeypatch, gate, error
):
    request = _request(tmp_path, "**First** part, then text.")
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("Edge must not be called before current review and online consent")

    monkeypatch.setattr(EdgeEngine, "synthesize", forbidden)
    if gate == "not-reviewed":
        request.pop("approved_sha256")
    elif gate == "online-declined":
        request["allow_online"] = False
    else:
        request["text"] = "**First** part, then changed text."
    with pytest.raises(StudioError) as caught:
        asyncio.run(service.start(request, lambda _: None))
    assert caught.value.code == error
    assert calls == []
    assert not Path(request["out_dir"]).exists()
    assert not Path(request["cache_dir"]).exists()
