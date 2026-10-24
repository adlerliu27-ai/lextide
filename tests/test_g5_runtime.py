"""Frozen process routing and external runtime isolation; no real inference."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lextide import runtime
from lextide.engines import kokoro
from lextide.errors import StudioError


@pytest.mark.parametrize("frozen_mode", [False, True])
def test_supervisor_routes_through_bundle_without_changing_lifetime(monkeypatch, frozen_mode):
    from lextide.longform import process

    monkeypatch.setattr(sys, "frozen", frozen_mode, raising=False)
    calls = []

    class Pipe:
        def close(self):
            calls.append("closed")

    class Child:
        stdin = Pipe()

        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return Child()

    monkeypatch.setattr(process.asyncio, "create_subprocess_exec", spawn)
    assert asyncio.run(process.run_supervised(["/usr/bin/true"])) == 0
    prefix = ["--supervise"] if frozen_mode else ["-B", "-m", "lextide.longform.process"]
    assert calls[0][0] == (sys.executable, *prefix, str(os.getpid()), "/usr/bin/true")
    assert calls[0][1]["stdin"] == asyncio.subprocess.PIPE
    assert calls[1] == "closed"


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    root = tmp_path / "Lextide.app" / "Contents" / "Frameworks"
    root.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(root), raising=False)
    monkeypatch.setattr(sys, "executable", str(root.parent / "MacOS" / "Lextide"))
    monkeypatch.delenv("LEXTIDE_OFFLINE_PYTHON", raising=False)
    return root


@pytest.mark.parametrize("frozen_mode", [False, True])
def test_worker_client_uses_bootstrap_only_when_frozen(monkeypatch, tmp_path, frozen_mode):
    pytest.importorskip("PySide6.QtCore")
    from lextide.desktop.process import WorkerClient

    monkeypatch.setattr(sys, "frozen", frozen_mode, raising=False)
    client = WorkerClient({"command": "preview", "text": "Hello."}, tmp_path, timeout_ms=0)
    started = []
    monkeypatch.setattr(client.process, "start", lambda: started.append(True))
    client.start()
    try:
        assert started == [True]
        assert client.process.program() == sys.executable
        prefix = ["--desktop-worker"] if frozen_mode else ["-m", "lextide.desktop.worker"]
        assert client.process.arguments() == [*prefix, str(client.path)]
        assert json.loads(client.path.read_text()) == client.request
        assert client.path.stat().st_mode & 0o077 == 0
    finally:
        if client.path:
            client.path.unlink()


def test_development_source_root_and_environment_are_preserved(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/external/lib")
    monkeypatch.setenv("PYTHONHOME", "/development/python")
    environment = kokoro._runtime_environment()
    assert runtime.bundled_source_root() == Path(kokoro.__file__).resolve().parents[2]
    assert environment["PYTHONPATH"] == str(runtime.bundled_source_root())
    assert environment["DYLD_LIBRARY_PATH"] == "/external/lib"
    assert environment["PYTHONHOME"] == "/development/python"


def test_frozen_source_path_and_environment_are_isolated(frozen, monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", "/unrelated/checkout")
    monkeypatch.setenv("PYTHONHOME", str(frozen))
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", str(frozen))
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", sys.executable)
    monkeypatch.setenv("_MEIPASS2", str(frozen))
    external = str(tmp_path / "external-library")
    bundle_paths = (str(frozen), str(frozen.parent / "Resources" / "library"))
    for name in ("DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "PATH"):
        monkeypatch.setenv(name, os.pathsep.join((*bundle_paths, external)))
    monkeypatch.setenv("DYLD_FALLBACK_LIBRARY_PATH", str(frozen))
    monkeypatch.setenv("PHONEMIZER_ESPEAK_LIBRARY", "/overridden/library")
    monkeypatch.setenv("ESPEAK_DATA_PATH", "/overridden/data")
    environment = kokoro._runtime_environment()
    assert environment["PYTHONPATH"] == str(frozen / "worker-source")
    assert Path(environment["PYTHONPATH"]).is_absolute()
    for name in ("DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "PATH"):
        assert environment[name] == external
    for name in (
        "PYTHONHOME",
        "_PYI_APPLICATION_HOME_DIR",
        "_PYI_ARCHIVE_FILE",
        "_MEIPASS2",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "PHONEMIZER_ESPEAK_LIBRARY",
        "ESPEAK_DATA_PATH",
    ):
        assert name not in environment
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        assert environment[name] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert os.environ["PYTHONHOME"] == str(frozen)
    assert os.environ["_PYI_ARCHIVE_FILE"] == sys.executable


def test_frozen_without_external_python_is_unavailable_without_subprocess(frozen, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("A missing external runtime must not spawn the frozen app")

    monkeypatch.setattr(kokoro.shutil, "which", unexpected)
    monkeypatch.setattr(kokoro.subprocess, "run", unexpected)
    engine = kokoro.KokoroEngine(model_dir=frozen / "missing-model")
    with pytest.raises(StudioError) as error:
        engine._python()
    assert error.value.code == "OFFLINE_RUNTIME_UNAVAILABLE"
    assert engine._runtime_status() == {"ready": False}
    assert engine.capabilities().available is False


def test_frozen_accepts_explicit_external_venv_without_resolving_symlink(
    frozen,
    monkeypatch,
    tmp_path,
):
    target = tmp_path / "real-python"
    target.write_text("external runtime")
    target.chmod(0o700)
    venv = tmp_path / "optional-runtime" / "bin" / "python"
    venv.parent.mkdir(parents=True)
    venv.symlink_to(target)
    assert kokoro.KokoroEngine(offline_python=str(venv))._python() == str(venv)
    monkeypatch.setenv("LEXTIDE_OFFLINE_PYTHON", str(venv))
    assert kokoro.KokoroEngine()._python() == str(venv)


def test_frozen_rejects_app_executable_as_external_python(frozen, tmp_path):
    executable = Path(sys.executable)
    executable.parent.mkdir()
    executable.write_text("frozen app")
    executable.chmod(0o700)
    alias = tmp_path / "python-alias"
    alias.symlink_to(executable)
    for candidate in (executable, alias):
        with pytest.raises(StudioError) as error:
            kokoro.KokoroEngine(offline_python=str(candidate))._python()
        assert error.value.code == "OFFLINE_RUNTIME_UNAVAILABLE"


def test_external_python_imports_bundled_worker_source_without_optional_runtime(
    monkeypatch,
    tmp_path,
):
    python = sys.executable
    root = tmp_path / "relocated app" / "Resources"
    source = root / "worker-source" / "lextide"
    (source / "engines").mkdir(parents=True)
    original = Path(kokoro.__file__).resolve().parents[1]
    for relative in ("__init__.py", "engines/__init__.py", "engines/kokoro_worker.py"):
        shutil.copyfile(original / relative, source / relative)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(root), raising=False)
    monkeypatch.setenv("PYTHONHOME", "/invalid/bundled/python")
    script = (
        "import json, pathlib, sys; import lextide.engines.kokoro_worker as worker; "
        "worker.deny_network(); status = worker.runtime_status(); "
        "assert not any(name in sys.modules for name in "
        "('numpy', 'onnxruntime', 'kokoro_onnx')); "
        "print(json.dumps({'source': str(pathlib.Path(worker.__file__).resolve()), "
        "'status': status}))"
    )
    result = subprocess.run(
        [python, "-B", "-c", script],
        cwd=tmp_path,
        env=kokoro._runtime_environment(),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    output = json.loads(result.stdout)
    assert output["source"] == str(source / "engines" / "kokoro_worker.py")
    assert isinstance(output["status"]["ready"], bool)
