"""Qt control tests. No real TTS, network request or subjective audio claim."""

import json
import subprocess
import sys
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox  # noqa: E402

from lextide.desktop import controller, service  # noqa: E402
from lextide.desktop.player import clock_text, cue_at  # noqa: E402
from lextide.desktop.preferences import load_preferences, save_preferences  # noqa: E402
from lextide.desktop.process import WorkerClient  # noqa: E402
from lextide.desktop.window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    value = QApplication.instance() or QApplication([])
    value.setQuitOnLastWindowClosed(False)
    return value


@pytest.fixture
def desktop(app, monkeypatch, tmp_path):
    # Do not launch engine discovery during these deterministic UI gate tests.
    monkeypatch.setattr(controller.Controller, "refresh_history", lambda *_: None)
    monkeypatch.setattr(controller.Controller, "refresh_engines", lambda *_: None)
    w = MainWindow()
    c = controller.Controller(w, tmp_path)
    errors = []
    w.show_error = lambda title, message: errors.append((title, message))
    w.show()
    app.processEvents()
    yield w, c, errors
    c.poll_timer.stop()
    c.player.media.stop()
    w.close_allowed = True
    w.close()
    w.deleteLater()
    app.processEvents()


def reviewed(w, c):
    w.speech_edit.setPlainText("A quiet day begins with a clear sentence.")
    result = service.preview({"text": w.speech_edit.toPlainText(), "settings": c._settings()})
    c._prepared(result, c.revision)
    w.approval_check.setChecked(True)
    return result


@pytest.mark.parametrize("field", ["text", "voice", "rate", "filter", "pause", "runtime", "output"])
def test_review_invalidates_after_any_effective_edit(desktop, field):
    w, c, _ = desktop
    reviewed(w, c)
    assert w.generate_button.isEnabled()
    {
        "text": lambda: w.speech_edit.insertPlainText(" A new thought."),
        "voice": lambda: w.voice_combo.setCurrentText("en-US-JennyNeural"),
        "rate": lambda: w.rate_spin.setValue(5),
        "filter": lambda: w.filter_check.setChecked(False),
        "pause": lambda: w.pause_spin.setValue(120),
        "runtime": lambda: w.model_dir_edit.setText("/different/models"),
        "output": lambda: w.output_dir_edit.setText("/different/output"),
    }[field]()
    assert c.preview is None
    assert not w.approval_check.isChecked()
    assert not w.generate_button.isEnabled()


def test_stale_preview_never_overwrites_new_text(desktop):
    w, c, _ = desktop
    revision = c.revision
    result = service.preview({"text": "Old text."})
    w.speech_edit.setPlainText("New text.")
    c._prepared(result, revision)
    assert w.speech_edit.toPlainText() == "New text."
    assert c.preview is None


def test_cancelled_online_permission_does_not_start(desktop, monkeypatch):
    w, c, _ = desktop
    reviewed(w, c)
    monkeypatch.setattr(controller, "confirm_transmission", lambda *_: False)
    monkeypatch.setattr(c, "_start_synthesis", lambda *_: pytest.fail("started without consent"))
    c.generate()


def test_approved_snapshot_captured_not_later_editor(desktop, monkeypatch):
    w, c, _ = desktop
    preview = reviewed(w, c)
    monkeypatch.setattr(controller, "confirm_transmission", lambda *_: True)
    calls = []
    monkeypatch.setattr(c, "_start_synthesis", calls.append)
    c.generate()
    w.speech_edit.setPlainText("This is a later draft.")
    assert calls[0]["text"] == preview["text"]
    assert calls[0]["approved_sha256"] == preview["sha256"]
    assert calls[0]["allow_online"] is True
    assert "allow_online" not in load_preferences(c.root)


def test_resume_rechecks_edge_permission(desktop, monkeypatch):
    _, c, _ = desktop
    monkeypatch.setattr(controller, "confirm_transmission", lambda *_: False)
    monkeypatch.setattr(c, "_start_synthesis", lambda *_: pytest.fail("resumed without consent"))
    c._resume_ready(
        "/old/task",
        {
            "settings": {"engine": "edge-tts", "voice": "voice", "rate": "-4%"},
            "review_text": "Saved words.",
            "sha256": "a" * 64,
        },
    )


def test_offline_generation_uses_no_online_permission(desktop, monkeypatch):
    w, c, _ = desktop
    c.capabilities["kokoro"] = {"available": True}
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("kokoro"))
    reviewed(w, c)
    monkeypatch.setattr(controller, "confirm_transmission", lambda *_: pytest.fail("online dialog"))
    calls = []
    monkeypatch.setattr(c, "_start_synthesis", calls.append)
    c.generate()
    assert calls[0]["allow_online"] is False
    assert calls[0]["audio_filter"] == "on"


def test_missing_offline_runtime_cannot_be_claimed_ready(desktop):
    w, c, _ = desktop
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("kokoro"))
    reviewed(w, c)
    assert not w.generate_button.isEnabled()


def test_permission_dialog_is_unchecked_and_escape_cancels(desktop):
    w, _, _ = desktop
    observed = []

    def inspect():
        dialog = next(
            x for x in QApplication.topLevelWidgets() if isinstance(x, QDialog) and x.isVisible()
        )
        check = dialog.findChild(QCheckBox)
        buttons = dialog.findChild(QDialogButtonBox)
        observed.append(
            not check.isChecked()
            and not buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        )
        dialog.reject()

    QTimer.singleShot(20, inspect)
    assert not controller.confirm_transmission(
        w, "Exact outgoing text.", {"voice": "Guy", "rate": "-4%"}, "a" * 64
    )
    assert observed == [True]


def test_navigation_and_theme_preserve_draft(desktop):
    w, c, _ = desktop
    w.speech_edit.setPlainText("Keep this draft.")
    for theme in ("light", "dark", "system"):
        w.set_theme(theme)
        for page in w.PAGES:
            w.show_page(page)
            assert w.speech_edit.toPlainText() == "Keep this draft."
    assert c.preview is None


def test_only_real_history_rows_and_empty_search(desktop):
    w, c, _ = desktop
    assert w.library_table.rowCount() == 0
    c._history_loaded(
        {
            "items": [
                {
                    "path": "/real/task",
                    "kind": "task",
                    "status": "paused",
                    "title": "Real session",
                    "engine": "kokoro",
                }
            ]
        }
    )
    assert w.tasks_table.rowCount() == w.library_table.rowCount() == 1
    w.library_search.setText("not present")
    assert w.library_table.rowCount() == 0
    assert w.recent_table.rowCount() == 1


def test_subtitle_gaps_and_boundaries_remain_unshifted():
    cues = [{"start_ms": 100, "end_ms": 900}, {"start_ms": 1200, "end_ms": 2000}]
    starts = [100, 1200]
    assert [cue_at(cues, starts, p) for p in (0, 100, 899, 900, 1100, 1200, 2000)] == [
        None,
        0,
        0,
        None,
        None,
        1,
        None,
    ]
    assert clock_text(3_661_000) == "1:01:01"


def test_preferences_reject_corrupt_state_without_erasing_jobs(tmp_path):
    tmp_path.joinpath("preferences.json").write_text("broken")
    original = load_preferences(tmp_path)
    save_preferences(tmp_path, original)
    assert load_preferences(tmp_path) == original
    assert tmp_path.joinpath("preferences.json").stat().st_mode & 0o077 == 0


def test_real_qprocess_preview_protocol_and_private_cleanup(app, tmp_path):
    results, errors, done = [], [], []
    worker = WorkerClient({"command": "preview", "text": "Real worker preview."}, tmp_path)
    worker.result_received.connect(results.append)
    worker.error_received.connect(errors.append)
    worker.finished.connect(lambda: done.append(True))
    worker.start()
    deadline = time.monotonic() + 10
    while not done and time.monotonic() < deadline:
        QTest.qWait(20)
    assert done and not errors
    assert results[0]["text"] == "Real worker preview."
    assert list(tmp_path.glob("request-*.json")) == []


def test_cli_help_does_not_initialize_qt():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from lextide.cli import build_parser; build_parser().format_help(); "
            "assert not any(k.startswith('PySide6') for k in sys.modules); print('no Qt')",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "no Qt"


def test_runtime_preferences_do_not_store_approval(desktop):
    w, c, _ = desktop
    reviewed(w, c)
    c.save_settings()
    data = json.loads((c.root / "preferences.json").read_text())
    assert not ({"text", "approved_sha256", "allow_online"} & set(data))


def test_import_warnings_survive_review_and_cleanup_is_undoable(desktop):
    w, c, _ = desktop
    c.provenance = {"warnings": ["One PDF page has no text."]}
    w.speech_edit.setPlainText("  Keep source spacing.  ")
    result = service.preview({"text": w.speech_edit.toPlainText()})
    c._prepared(result, c.revision)
    assert "One PDF page" in w.changes_edit.toPlainText()
    assert w.speech_edit.toPlainText() == "Keep source spacing."
    w.speech_edit.undo()
    assert w.speech_edit.toPlainText() == "  Keep source spacing.  "
    assert c.preview is None


def test_online_dialog_nested_edit_rejects_stale_generation(desktop, monkeypatch):
    w, c, _ = desktop
    reviewed(w, c)

    def consent(*_):
        w.speech_edit.setPlainText("A concurrent edit while the dialog is open.")
        return True

    monkeypatch.setattr(controller, "confirm_transmission", consent)
    monkeypatch.setattr(c, "_start_synthesis", lambda *_: pytest.fail("stale start"))
    c.generate()


def test_cancel_targets_selected_task_not_different_running_task(desktop, monkeypatch):
    w, c, _ = desktop
    w.set_tasks([{"path": "/task/B", "title": "Task B", "status": "paused", "kind": "task"}])
    w.tasks_table.selectRow(0)
    c.synthesis = object()
    c.active_task = "/task/A"
    calls = []
    monkeypatch.setattr(
        controller.QMessageBox, "question", lambda *_: controller.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(c, "_request", lambda request, *_args, **_kw: calls.append(request))
    c.cancel()
    assert calls == [{"command": "cancel", "task_path": "/task/B"}]
    c.synthesis = None


def test_completed_tasks_cannot_resume_or_cancel(desktop):
    w, c, _ = desktop
    w.set_tasks([{"path": "/task/done", "status": "completed", "kind": "task"}])
    w.tasks_table.selectRow(0)
    c._task_selection()
    assert not w.task_resume_button.isEnabled()
    assert not w.task_cancel_button.isEnabled()


def test_closing_completion_does_not_launch_media_verification(desktop, monkeypatch):
    _, c, _ = desktop
    c.closing = True
    monkeypatch.setattr(c, "_verify", lambda *_: pytest.fail("new worker while closing"))
    c._synthesized({"status": "completed", "export_path": "/finished/job"})


def test_reset_remains_undoable(desktop, monkeypatch):
    w, c, _ = desktop
    w.source_edit.setPlainText("Original words.")
    w.speech_edit.setPlainText("Important edited words.")
    monkeypatch.setattr(c, "_confirm_replacement", lambda: True)
    c.reset_text()
    assert w.speech_edit.toPlainText() == "Original words."
    w.speech_edit.undo()
    assert w.speech_edit.toPlainText() == "Important edited words."


def test_settings_io_failure_cannot_report_success(desktop, monkeypatch):
    w, c, errors = desktop

    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr(controller, "save_preferences", fail)
    c.save_settings()
    assert errors
    assert "Could not save local settings" in w.status_label.text()
    assert "settings saved" not in w.status_label.text()
