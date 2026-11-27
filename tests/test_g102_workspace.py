"""Carbon secondary pages retain geometry, history identity and explicit save."""

import hashlib
import json

import pytest

from lextide.desktop.service import library_script
from lextide.errors import ValidationError


def test_saved_job_script_is_local_and_hash_checked(tmp_path):
    script = tmp_path / "tts.txt"
    script.write_text("Read this locally.", encoding="utf-8")
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps({"text_sha256": digest}))
    assert library_script({"path": str(tmp_path)})["source_sha256"] == digest
    script.write_text("Changed", encoding="utf-8")
    with pytest.raises(ValidationError):
        library_script({"path": str(tmp_path)})


@pytest.fixture
def desktop(request):
    pytest.importorskip("PySide6.QtWidgets")
    return request.getfixturevalue("workspace_ui")


@pytest.fixture
def workspace_ui(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    from lextide.desktop.controller import Controller
    from lextide.desktop.window import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Controller, "refresh_history", lambda *_: None)
    monkeypatch.setattr(Controller, "refresh_engines", lambda *_: None)
    window = MainWindow()
    control = Controller(window, tmp_path)
    window.show()
    app.processEvents()
    yield app, window, control
    control.poll_timer.stop()
    window.close_allowed = True
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_library_selection_and_folder_length_leave_table_fixed(desktop):
    app, window, control = desktop
    control._history_loaded(
        {
            "items": [
                {
                    "path": str(control.root / ("folder" * n)),
                    "title": str(n),
                    "status": "completed",
                    "created_at": "2026-01-01",
                    "kind": "job",
                }
                for n in (1, 70)
            ]
        }
    )
    window.show_page("Library")
    table = window.library_table
    assert table.columnCount() == 5
    assert table.horizontalHeaderItem(4).text() == "Created"
    for width in (1180, 980):
        window.resize(width, 800)
        app.processEvents()
        assert table.isColumnHidden(4) is (width < 1040)
        geometries = []
        for index in (0, 1):
            table.selectRow(index)
            app.processEvents()
            geometries.append(table.geometry())
            assert table.item(index, 1).text() == "completed"
            assert not table.item(index, 1).icon().isNull()
        assert geometries[0] == geometries[1]


def test_reset_unapplied_settings_preserves_studio_and_disk(desktop):
    _, w, c = desktop
    w.speech_edit.setPlainText("Edited draft")
    old = w.settings_output_dir_edit.text()
    prefs = c.root / "preferences.json"
    before = prefs.read_bytes() if prefs.exists() else None
    w.settings_output_dir_edit.setText(old + "-changed")
    assert w.settings_save_button.isEnabled()
    w.settings_reset_button.click()
    assert w.settings_output_dir_edit.text() == old
    assert not w.settings_save_button.isEnabled()
    assert w.speech_edit.toPlainText() == "Edited draft"
    assert (prefs.read_bytes() if prefs.exists() else None) == before
