"""G5 external dependencies: persisted paths and offline first-launch guidance."""

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

from lextide.desktop import controller, service  # noqa: E402
from lextide.desktop.i18n import tr  # noqa: E402
from lextide.desktop.preferences import load_preferences, save_preferences  # noqa: E402
from lextide.desktop.window import MainWindow  # noqa: E402
from lextide.engines.base import EngineCapabilities  # noqa: E402


@pytest.fixture(scope="module")
def app():
    value = QApplication.instance() or QApplication([])
    value.setQuitOnLastWindowClosed(False)
    return value


@pytest.fixture
def desktop(app, monkeypatch, tmp_path):
    calls = []
    # History workers are unrelated to dependency discovery and need a real
    # WorkerClient.finished signal; this fixture captures payloads only.
    monkeypatch.setattr(controller.Controller, "refresh_history", lambda *_: None)
    monkeypatch.setattr(
        controller.Controller,
        "_request",
        lambda self, payload, callback=None, **kwargs: calls.append((payload, callback)),
    )
    w = MainWindow()
    c = controller.Controller(w, tmp_path)
    c.poll_timer.stop()
    app.processEvents()
    yield w, c, calls
    c.player.media.stop()
    w.close_allowed = True
    w.close()
    w.deleteLater()
    app.processEvents()


def approve(w, c):
    w.speech_edit.setPlainText("A local dependency check never sends this text.")
    preview = service.preview({"text": w.speech_edit.toPlainText()})
    c._prepared(preview, c.revision)
    w.approval_check.setChecked(True)


def test_idle_first_close_finishes_after_current_close_event(desktop, app):
    w, c, _ = desktop
    w.show()
    app.processEvents()
    assert w.isVisible() and not c.workers
    w.close()
    app.processEvents()
    assert c.closing and w.close_allowed
    assert not w.isVisible()


def test_new_and_old_preferences_leave_optional_paths_blank(tmp_path):
    defaults = load_preferences(tmp_path)
    assert defaults["ffmpeg"] == defaults["ffprobe"] == ""
    assert defaults["offline_python"] == defaults["model_dir"] == ""
    save_preferences(tmp_path, {"theme": "dark", "offline_python": "/existing/python"})
    loaded = load_preferences(tmp_path)
    assert loaded["theme"] == "dark"
    assert loaded["offline_python"] == "/existing/python"
    assert loaded["ffmpeg"] == loaded["ffprobe"] == ""
    assert loaded["out_dir"] == str(tmp_path / "projects")


def test_settings_save_and_reload_explicit_media_paths(desktop):
    w, c, calls = desktop
    w.ffmpeg_edit.setText("/local tools/ffmpeg")
    w.ffprobe_edit.setText("/local tools/ffprobe")
    c.save_settings()
    loaded = load_preferences(c.root)
    assert loaded["ffmpeg"] == "/local tools/ffmpeg"
    assert loaded["ffprobe"] == "/local tools/ffprobe"
    c.preferences = loaded
    w.ffmpeg_edit.clear()
    w.ffprobe_edit.clear()
    c._restore_preferences()
    assert c._runtime() == {"ffmpeg": "/local tools/ffmpeg", "ffprobe": "/local tools/ffprobe"}
    assert calls[-1][0]["runtime"] == c._runtime()
    saved = json.loads((c.root / "preferences.json").read_text())
    assert not {"text", "approved_sha256", "allow_online"} & saved.keys()


@pytest.mark.parametrize("key", ["ffmpeg", "ffprobe"])
def test_media_path_edit_invalidates_pending_click_and_stale_capabilities(
    desktop, monkeypatch, key
):
    w, c, calls = desktop
    c.capabilities = {"edge-tts": {"available": True}, "kokoro": {"available": True}}
    approve(w, c)
    assert w.generate_button.isEnabled()
    monkeypatch.setattr(c, "_start_synthesis", lambda *_: pytest.fail("stale runtime submitted"))
    w.generate_button.click()
    payload, callback = calls[-1]
    assert payload["command"] == "preview" and c.pending_submission is not None
    getattr(w, f"{key}_edit").setText(f"/different/{key}")
    assert c.preview is None
    assert not w.approval_check.isChecked()
    assert c.capabilities == {}
    callback(service.preview(payload))
    assert c.pending_submission is None
    assert w.generate_button.isEnabled()  # Only a fresh click can approve the new runtime.


def test_explicit_paths_propagate_to_synthesis_request(desktop, monkeypatch):
    w, c, calls = desktop
    for key in ("model_dir", "offline_python", "ffmpeg", "ffprobe"):
        getattr(w, f"{key}_edit").setText(f"/local/{key}")
    approve(w, c)
    monkeypatch.setattr(controller, "confirm_transmission", lambda *_: pytest.fail("extra dialog"))
    requests = []
    monkeypatch.setattr(c, "_start_synthesis", requests.append)
    w.generate_button.click()
    assert not requests
    payload, callback = calls[-1]
    assert payload["command"] == "preview"
    preview = service.preview(payload)
    callback(preview)
    assert len(requests) == 1
    assert requests[0]["text"] == preview["text"]
    assert requests[0]["approved_sha256"] == preview["sha256"]
    assert service._authorize(requests[0], preview["text"], "edge-tts") is True
    assert requests[0]["runtime"] == {
        "model_dir": "/local/model_dir",
        "offline_python": "/local/offline_python",
        "ffmpeg": "/local/ffmpeg",
        "ffprobe": "/local/ffprobe",
    }
    assert service._runtime(requests[0]) == {
        **requests[0]["runtime"],
        "model_dir": Path("/local/model_dir"),
    }


@pytest.mark.parametrize("key", ["model_dir", "offline_python", "ffmpeg", "ffprobe"])
def test_browse_selects_existing_path_only(desktop, monkeypatch, key):
    w, c, _ = desktop
    calls = []

    def choose(*args):
        calls.append(args)
        return "/selected/path" if key == "model_dir" else ("/selected/path", "")

    method = "getExistingDirectory" if key == "model_dir" else "getOpenFileName"
    monkeypatch.setattr(controller.QFileDialog, method, choose)
    getattr(w, f"{key}_button").click()
    assert getattr(w, f"{key}_edit").text() == "/selected/path"
    assert len(calls) == 1


def test_discovery_is_delegated_and_stale_response_is_ignored(desktop, monkeypatch):
    w, c, calls = desktop
    monkeypatch.setattr(service, "engines", lambda *_: pytest.fail("checked on GUI thread"))
    c.refresh_engines()
    request, callback = calls[-1]
    assert request == {"command": "engines", "runtime": {}}
    w.ffmpeg_edit.setText("/changed/ffmpeg")
    callback({"engines": [{"engine_id": "kokoro", "available": True}]})
    assert c.capabilities == {}
    assert "Check FFmpeg" in w.media_status_label.text()


@pytest.mark.parametrize("missing", ["ffmpeg", "ffprobe"])
def test_missing_media_tool_is_individual_and_disables_engine_availability(
    monkeypatch, tmp_path, missing
):
    runtime = {}
    for name in ("ffmpeg", "ffprobe"):
        path = tmp_path / name
        if name != missing:
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o700)
        runtime[name] = str(path)
    monkeypatch.setattr(
        service.KokoroEngine,
        "capabilities",
        lambda self: EngineCapabilities(
            "kokoro",
            "test",
            False,
            True,
            True,
            ("af_heart",),
            notes=("runtime_metadata_ready=true",),
        ),
    )
    monkeypatch.setattr(service, "model_status", lambda *_a, **_k: {"ready": True})
    result = service.engines({"runtime": runtime})
    assert result["media_tools"][missing] == {
        "available": False,
        "path": None,
        "error": "MEDIA_TOOL_UNAVAILABLE",
    }
    other = "ffprobe" if missing == "ffmpeg" else "ffmpeg"
    assert result["media_tools"][other] == {"available": True, "path": runtime[other]}
    assert result["offline_runtime"]["ready"] is True
    assert all(engine["available"] is False for engine in result["engines"])


def test_missing_optional_runtime_and_models_return_guidance_without_traceback(desktop, tmp_path):
    w, c, _ = desktop
    result = service.engines(
        {
            "runtime": {
                "model_dir": str(tmp_path / "missing-models"),
                "offline_python": str(tmp_path / "missing-python"),
                "ffmpeg": str(tmp_path / "missing-ffmpeg"),
                "ffprobe": str(tmp_path / "missing-ffprobe"),
            }
        }
    )
    assert result["offline_runtime"]["ready"] is False
    assert result["model"]["ready"] is False
    c._engines(result)
    text = w.media_status_label.text() + w.model_status_label.text()
    assert "ffmpeg · Not found" in text and "ffprobe · Not found" in text
    assert "Model missing" in text and "Runtime unavailable" in text
    assert "never trigger downloads or online fallback" in text
    assert "Traceback" not in text
    assert not (tmp_path / "missing-models").exists()
    approve(w, c)
    assert not w.generate_button.isEnabled()


def test_library_disabled_action_explains_selection(desktop):
    w, _, _ = desktop
    assert not w.library_load_button.isEnabled()
    assert "Select a session" in w.library_load_button.toolTip()
    assert "Python 3.13" in w.offline_python_edit.placeholderText()
    assert "/Users/" not in w.offline_python_edit.placeholderText()


def test_successful_recheck_clears_missing_media_guidance(desktop):
    w, c, _ = desktop
    c._engines({"engines": [{"engine_id": "edge-tts", "available": False}]})
    assert "Media tools unavailable" in w.status_label.text()
    c._engines(
        {
            "engines": [{"engine_id": "edge-tts", "available": True}],
            "media_tools": {
                name: {"available": True, "path": f"/local/{name}"}
                for name in ("ffmpeg", "ffprobe")
            },
        }
    )
    assert w.status_label.text() == tr("Ready")
    for name in ("ffmpeg", "ffprobe"):
        assert tr("{name} · Found: {path}", name=name, path=f"/local/{name}") in (
            w.media_status_label.text()
        )
    assert "Media tools unavailable" not in w.status_label.text()
    approve(w, c)
    assert w.generate_button.isEnabled()
