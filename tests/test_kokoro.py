"""Kokoro contract tests: fake inference only, no model download or real inference."""

import asyncio
import contextlib
import hashlib
import json
import os
import socket
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from lextide.config import Settings
from lextide.engines import kokoro as adapter
from lextide.engines.kokoro import KokoroEngine
from lextide.engines.sentence_split import split_sentences
from lextide.errors import StudioError

SETTINGS = Settings(voice="af_heart", rate="-4%", timeout=90, engine="kokoro")


@pytest.mark.parametrize(
    "text,parts",
    [
        ("Hello. World!", ["Hello. ", "World!"]),
        (" Dr. Smith paid 3.14 dollars.\nNext? ", [" Dr. Smith paid 3.14 dollars.\n", "Next? "]),
        ("A U.S. test, e.g. this one. Done.", ["A U.S. test, e.g. this one. ", "Done."]),
        ('"Use &amp; literally!" Then stop.', ['"Use &amp; literally!" ', "Then stop."]),
        ("Mr. A. Smith said no", ["Mr. A. Smith said no"]),
        ("One... Two?!", ["One... ", "Two?!"]),
    ],
)
def test_sentence_slices_preserve_exact_input(text, parts):
    assert split_sentences(text) == parts
    assert "".join(split_sentences(text)) == text


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    lease = {"active": False, "released": False}
    model = {
        "model_id": "kokoro-v1.0-int8",
        "version": "model-test-v1",
        "assets": {
            adapter._MODEL_FILE: {"path": "/private/model.onnx", "sha256": "a" * 64},
            adapter._VOICES_FILE: {"path": "/private/voices.bin", "sha256": "b" * 64},
        },
    }

    @contextlib.contextmanager
    def model_lease(root):
        lease["active"] = True
        try:
            yield model
        finally:
            lease["active"] = False
            lease["released"] = True

    monkeypatch.setattr(adapter, "model_lease", model_lease)
    monkeypatch.setattr(
        adapter, "resolve_media_tools", lambda *args: ("ffmpeg-test", "ffprobe-test")
    )
    engine = KokoroEngine(model_dir=tmp_path / "model", offline_python=sys.executable)
    return engine, lease


def write_pcm_result(directory, counts=(24001, 12001), rate=24000):
    with wave.open(str(directory / "audio.wav"), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(b"\x01\x00" * sum(counts))
    (directory / "result.json").write_text(
        json.dumps(
            {
                "ok": True,
                "sample_rate": rate,
                "sample_counts": list(counts),
                "total_samples": sum(counts),
            }
        )
    )


def test_worker_contract_actual_sample_timing_and_private_cleanup(
    fake_runtime,
    tmp_path,
    monkeypatch,
):
    engine, lease = fake_runtime
    calls = []
    source = 'Use &amp; literally. "Next!"'

    async def run(arguments, *, env=None):
        assert lease["active"]
        calls.append(arguments)
        if "lextide.engines.kokoro_worker" in arguments:
            path = Path(arguments[-1])
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
            request = json.loads(path.read_text())
            assert request["sentences"] == ["Use &amp; literally. ", '"Next!"']
            assert request["voice"] == "af_heart"
            assert request["speed"] == 0.96
            assert request["lang"] == "en-us"
            assert env["HF_HUB_OFFLINE"] == "1"
            write_pcm_result(path.parent)
        else:
            assert arguments[arguments.index("-c:a") + 1] == "libmp3lame"
            assert arguments[arguments.index("-write_xing") + 1] == "1"
            assert arguments[-2] == "mp3"
            Path(arguments[-1]).write_bytes(b"fake-mp3")
        return 0

    monkeypatch.setattr(engine, "_run_process", run)
    destination = tmp_path / "audio.mp3"
    result = asyncio.run(engine.synthesize(source, destination, SETTINGS, allow_online=False))
    assert len(calls) == 2  # One worker and exactly one encoder, regardless of sentence count.
    assert lease["released"] and not lease["active"]
    assert "".join(boundary.text for boundary in result.boundaries) == source
    first, second = result.boundaries
    assert first.offset_ticks == 0
    assert first.duration_ticks == 24001 * 10_000_000 // 24000
    assert second.offset_ticks == first.duration_ticks
    assert second.offset_ticks + second.duration_ticks == 36002 * 10_000_000 // 24000
    assert result.timing_source == "kokoro sentence PCM sample counts"
    assert result.effective_settings["samples"] == 36002
    assert result.effective_settings["sample_rate"] == 24000
    assert result.model == {
        "id": "kokoro-v1.0-int8",
        "version": "model-test-v1",
        "sha256": "a" * 64,
        "voices_sha256": "b" * 64,
    }
    assert "/private" not in json.dumps(result.model)
    assert result.audio_sha256 == hashlib.sha256(b"fake-mp3").hexdigest()
    assert not list(tmp_path.glob(".kokoro-*"))


@pytest.mark.parametrize(
    "settings",
    [
        Settings(voice="en-US-GuyNeural", engine="kokoro"),
        Settings(),
    ],
)
def test_wrong_engine_or_voice_rejected_before_worker(settings, fake_runtime, tmp_path):
    engine, lease = fake_runtime
    with pytest.raises(StudioError) as caught:
        asyncio.run(engine.synthesize("Hello.", tmp_path / "a.mp3", settings, allow_online=False))
    assert caught.value.exit_code == 2
    assert not lease["active"] and not lease["released"]


def test_unavailable_model_voice_is_not_substituted(fake_runtime, tmp_path, monkeypatch):
    engine, lease = fake_runtime

    async def run(arguments, **kwargs):
        result = Path(arguments[-1]).parent / "result.json"
        result.write_text('{"ok":false,"code":"OFFLINE_VOICE_UNAVAILABLE"}')
        return 1

    monkeypatch.setattr(engine, "_run_process", run)
    with pytest.raises(StudioError) as caught:
        asyncio.run(engine.synthesize("Hello.", tmp_path / "a.mp3", SETTINGS, allow_online=False))
    assert caught.value.exit_code == 2
    assert lease["released"]
    assert not list(tmp_path.glob(".kokoro-*"))


@pytest.mark.parametrize("counts,rate", [([0], 24000), ([1], 0), ([600 * 24000 + 1], 24000)])
def test_invalid_or_overlong_sample_metadata_rejected(counts, rate, tmp_path):
    (tmp_path / "result.json").write_text(
        json.dumps(
            {
                "ok": True,
                "sample_rate": rate,
                "sample_counts": counts,
                "total_samples": sum(counts),
            }
        )
    )
    with pytest.raises(StudioError) as caught:
        KokoroEngine._read_result(tmp_path, 1)
    assert caught.value.exit_code == 5


def test_pcm_length_must_match_sample_metadata(tmp_path):
    write_pcm_result(tmp_path, counts=(100,))
    with (tmp_path / "audio.wav").open("ab") as output:
        output.write(b"unexpected bytes")
    with pytest.raises(StudioError, match="样本计数"):
        KokoroEngine._read_result(tmp_path, 1)


def test_runtime_missing_is_safe_and_does_not_create_output(tmp_path):
    engine = KokoroEngine(offline_python=str(tmp_path / "PRIVATE-missing-runtime"))
    with pytest.raises(StudioError) as caught:
        asyncio.run(engine.synthesize("Hello.", tmp_path / "a.mp3", SETTINGS, allow_online=True))
    assert caught.value.exit_code == 7
    assert "PRIVATE" not in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_default_runtime_and_override_are_explicit(monkeypatch):
    seen = []

    def which(value):
        seen.append(value)
        return sys.executable

    monkeypatch.setattr(adapter.shutil, "which", which)
    monkeypatch.delenv("LEXTIDE_OFFLINE_PYTHON", raising=False)
    KokoroEngine()._python()
    assert seen[-1].endswith("TTS-Studio/offline-runtime/.venv/bin/python")
    monkeypatch.setenv("LEXTIDE_OFFLINE_PYTHON", "/runtime/from-environment")
    KokoroEngine()._python()
    assert seen[-1] == "/runtime/from-environment"
    KokoroEngine(offline_python="/explicit/python")._python()
    assert seen[-1] == "/explicit/python"


def test_capabilities_static_readiness_does_not_load_model(monkeypatch):
    monkeypatch.setattr(KokoroEngine, "_runtime_status", lambda self: {"ready": True})
    monkeypatch.setattr(
        adapter,
        "model_status",
        lambda root: {
            "installed": True,
            "model_id": "test",
            "version": "1",
            "assets": [{"present": True, "actual_size": 10, "expected_size": 10}],
        },
    )
    capabilities = KokoroEngine().capabilities()
    assert capabilities.installed and capabilities.available and not capabilities.online
    assert capabilities.voices == ("af_heart",)
    assert any("未验证推理" in note for note in capabilities.notes)


def test_adapter_import_and_capabilities_do_not_import_optional_runtime(tmp_path):
    script = (
        "import sys; from lextide.engines.kokoro import KokoroEngine; "
        f"e=KokoroEngine(model_dir=__import__('pathlib').Path({str(tmp_path)!r}), "
        f"offline_python={sys.executable!r}); c=e.capabilities(); "
        "assert not any(x in sys.modules for x in ('numpy','onnxruntime','kokoro_onnx')); "
        "assert not c.installed; assert not c.available"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=adapter._runtime_environment(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_worker_socket_guard_denies_ipv4_ipv6_and_dns(monkeypatch):
    from lextide.engines.kokoro_worker import deny_network

    with monkeypatch.context() as context:
        # Register restorations before the worker mutates process-global socket functions.
        for name in (
            "socket",
            "create_connection",
            "getaddrinfo",
            "gethostbyname",
            "gethostbyname_ex",
            "gethostbyaddr",
            "getnameinfo",
        ):
            context.setattr(socket, name, getattr(socket, name))
        for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
            context.setenv(name, os.environ.get(name, ""))
        deny_network()
        for family in (socket.AF_INET, socket.AF_INET6):
            with pytest.raises(PermissionError):
                socket.socket(family)
        with pytest.raises(PermissionError):
            socket.getaddrinfo("example.com", 443)


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_or_cancel_kills_worker_and_cleans_private_files(
    fake_runtime,
    tmp_path,
    monkeypatch,
    cancel,
):
    engine, lease = fake_runtime
    state = {"killed": False, "waits": 0}

    class Process:
        returncode = None

        async def wait(self):
            state["waits"] += 1
            if self.returncode is None:
                await asyncio.Event().wait()
            return self.returncode

        def kill(self):
            state["killed"] = True
            self.returncode = -9

    async def launch(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)

    async def exercise():
        task = asyncio.create_task(
            engine.synthesize(
                "Hello.",
                tmp_path / "a.mp3",
                Settings(voice="af_heart", engine="kokoro", timeout=1),
                allow_online=False,
            )
        )
        if cancel:
            await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(StudioError) as caught:
                await task
            assert caught.value.code == "OFFLINE_TIMEOUT"

    asyncio.run(exercise())
    assert state["killed"] and state["waits"] == 2
    assert lease["released"]
    assert not (tmp_path / "a.mp3").exists()
    assert not list(tmp_path.glob(".kokoro-*"))


@pytest.fixture
def worker_optional_modules(monkeypatch):
    """Tiny numeric stand-in: exercise the API contract without installing numpy/ORT."""
    import math
    import struct
    from types import SimpleNamespace

    class Samples:
        ndim = 1
        dtype = float

        def __init__(self, values):
            self.values = values
            self.size = len(values)

        def __mul__(self, factor):
            return Samples([value * factor for value in self.values])

        def astype(self, dtype):
            assert dtype == "<i2"
            return SimpleNamespace(
                tobytes=lambda: struct.pack(
                    "<" + "h" * self.size,
                    *(int(value) for value in self.values),
                )
            )

    fake_numpy = SimpleNamespace(
        asarray=lambda values: Samples(values),
        floating=float,
        issubdtype=lambda dtype, expected: dtype is expected,
        isfinite=lambda samples: SimpleNamespace(
            all=lambda: all(math.isfinite(value) for value in samples.values)
        ),
        clip=lambda samples, low, high: Samples(
            [min(high, max(low, value)) for value in samples.values]
        ),
    )
    state = {"calls": [], "outputs": [([0.1, 0.2, -0.1], 24000), ([0.3, 0.1], 24000)]}

    class FakeKokoro:
        def __init__(self, model_path, voices_path):
            assert model_path == "model.onnx" and voices_path == "voices.bin"

        def get_voices(self):
            return ["af_heart"]

        def create(self, text, **kwargs):
            state["calls"].append((text, kwargs))
            return state["outputs"].pop(0)

    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", SimpleNamespace(Kokoro=FakeKokoro))
    request = {
        "sentences": ["Literal &amp;. ", "Next!"],
        "speed": 0.96,
        "voice": "af_heart",
        "lang": "en-us",
        "model_path": "model.onnx",
        "voices_path": "voices.bin",
    }
    return request, state


def test_worker_uses_public_create_and_counts_each_sentence(worker_optional_modules, tmp_path):
    from lextide.engines.kokoro_worker import _synthesize

    request, state = worker_optional_modules
    result = _synthesize(request, tmp_path)
    assert result == {"ok": True, "sample_rate": 24000, "sample_counts": [3, 2], "total_samples": 5}
    assert [call[0] for call in state["calls"]] == request["sentences"]
    assert state["calls"][0][1] == {
        "voice": "af_heart",
        "speed": 0.96,
        "lang": "en-us",
        "trim": True,
        "sentence_pause": 0.25,
        "clause_pause": 0.1,
        "continuous": False,
    }
    with wave.open(str(tmp_path / "audio.wav"), "rb") as audio:
        assert audio.getnframes() == 5
        assert audio.getframerate() == 24000
        assert audio.getsampwidth() == 2


@pytest.mark.parametrize(
    "outputs",
    [
        [([], 24000)],
        [([float("nan")], 24000)],
        [([float("inf")], 24000)],
        [([0.1], 0)],
        [([0.1], 24000), ([0.1], 22050)],
    ],
)
def test_worker_rejects_empty_nonfinite_or_changing_rate(
    outputs,
    worker_optional_modules,
    tmp_path,
):
    from lextide.engines.kokoro_worker import _synthesize

    request, state = worker_optional_modules
    state["outputs"] = outputs
    assert _synthesize(request, tmp_path) == {"ok": False, "code": "OFFLINE_AUDIO_INVALID"}


def test_runtime_check_rejects_any_pinned_dependency_drift(monkeypatch):
    from lextide.engines import kokoro_worker as worker

    monkeypatch.setattr(worker.sys, "version_info", (3, 13, 15))
    monkeypatch.setattr(worker.util, "find_spec", lambda module: object())
    expected = {name: value[1] for name, value in worker._RUNTIME_PACKAGES.items()}
    monkeypatch.setattr(worker.metadata, "version", expected.__getitem__)
    assert worker.runtime_status()["ready"]
    expected["onnxruntime"] = "0.0.0"
    assert worker.runtime_status() == {"ready": False, "reason": "runtime_version"}


def test_model_load_failure_has_distinct_safe_status(
    worker_optional_modules, tmp_path, monkeypatch
):
    from lextide.engines import kokoro_worker as worker

    request, _ = worker_optional_modules

    def fail(*args, **kwargs):
        raise RuntimeError("sensitive model path")

    monkeypatch.setattr(sys.modules["kokoro_onnx"], "Kokoro", fail)
    result = worker._synthesize(request, tmp_path)
    assert result == {"ok": False, "code": "OFFLINE_MODEL_LOAD_FAILED"}
    (tmp_path / "result.json").write_text(json.dumps(result))
    with pytest.raises(StudioError) as caught:
        KokoroEngine._read_result(tmp_path, 2)
    assert caught.value.code == "OFFLINE_MODEL_LOAD_FAILED"
    assert caught.value.exit_code == 7
    assert "sensitive" not in str(caught.value)
