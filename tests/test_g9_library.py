"""Library localization regression. Worker/media I/O is mocked; no network."""

import ast
import inspect
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g7_gui as g7  # noqa: E402

from lextide.desktop.i18n import current_language, tr  # noqa: E402

desktop = g7.desktop


def history_rows(root):
    return [
        {
            "path": str(root / "imported-job"),
            "title": "Imported sample",
            "kind": "job",
            "status": "completed",
            "created_at": "2026-10-28T08:30:00Z",
            "source_path": str(root / "reading {draft}.txt"),
        },
        {
            "path": str(root / "pasted-job"),
            "title": "Pasted sample",
            "kind": "job",
            "status": "completed",
        },
    ]


def assert_details(w, row):
    chinese = current_language() == "zh_CN"
    source = Path(row["source_path"]).name if row.get("source_path") else tr("Pasted text")
    created = row.get("created_at") or tr("Unknown")
    prefix = (
        f"创建时间：{created} · 来源：{source}"
        if chinese
        else (f"Created: {created} · Source: {source}")
    )
    assert w.library_root_label.text() == f"{prefix}\n{row['path']}"
    full_source = row.get("source_path") or tr("Pasted text")
    tooltip = f"来源：{full_source}" if chinese else f"Source: {full_source}"
    assert w.library_root_label.toolTip() == f"{tooltip}\n{row['path']}"


def test_multiple_history_rows_show_created_source_and_tooltip(desktop):
    _, w, c = desktop
    rows = history_rows(c.root)
    c._history_loaded({"items": rows})
    w.show_page("Library")
    assert w.library_table.rowCount() == 2
    for index, row in enumerate(rows):
        w.library_table.selectRow(index)
        c._library_details()  # A direct call also propagates errors hidden by Qt signals.
        assert_details(w, row)
        assert w.library_load_button.isEnabled()


def test_empty_and_filtered_library_do_not_keep_stale_details(desktop):
    _, w, c = desktop
    rows = history_rows(c.root)
    c._history_loaded({"items": rows})
    w.library_table.selectRow(0)
    c._library_details()
    w.library_search.setText("no matching session")
    c._library_details()
    assert w.library_table.rowCount() == 0
    assert not w.library_load_button.isEnabled()
    assert "2026-10-28" not in w.library_root_label.text()
    w.library_search.clear()
    c._history_loaded({"items": []})
    c._library_details()
    assert w.library_empty_label.isVisible() is w.pages["Library"].isVisible()
    assert w.library_root_label.text() == tr(
        "{count} local folders · Select a session to see its source and creation time.",
        count=len(c.preferences["roots"]),
    )


def test_live_language_switch_and_reenter_library(desktop):
    _, w, c = desktop
    rows = history_rows(c.root)
    c._history_loaded({"items": rows})
    w.library_table.selectRow(1)
    for language in ("zh_CN", "en", "zh_CN"):
        w.show_page("Settings")
        w.language_combo.setCurrentIndex(w.language_combo.findData(language))
        c.save_settings()
        w.show_page("Library")
        c._library_details()
        assert_details(w, rows[1])


def test_selected_library_session_opens_listen_without_changing_artifacts(desktop, monkeypatch):
    _, w, c = desktop
    data = g7.load_session(w, c)
    job = Path(data["job_path"])
    before = g7.fingerprints(job)
    row = history_rows(c.root)[0] | {"path": str(job)}
    c._history_loaded({"items": [row]})
    w.show_page("Library")
    w.library_table.selectRow(0)
    requests = []

    def verify_fixture(payload, callback, **_):
        requests.append(payload)
        assert payload["command"] == "verify" and payload["path"] == str(job)
        callback(data)

    monkeypatch.setattr(c, "_request", verify_fixture)
    w.library_load_button.click()
    assert len(requests) == 1
    assert w.pages["Listen"].isVisible()
    assert w.transcript_list.count() == 2
    assert c.last_verified == data
    assert g7.fingerprints(job) == before


def test_ui_translation_keywords_cannot_collide_with_tr_parameters():
    """Check every UI call so another reserved format keyword fails in the test suite."""
    reserved = {
        name
        for name, parameter in inspect.signature(tr).parameters.items()
        if parameter.kind != inspect.Parameter.VAR_KEYWORD
    }
    desktop_dir = Path(inspect.getfile(tr)).parent
    collisions = []
    for path in desktop_dir.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
            ):
                collisions.extend(
                    (path.name, node.lineno, keyword.arg)
                    for keyword in node.keywords
                    if node.args and keyword.arg in reserved
                )
    assert not collisions
