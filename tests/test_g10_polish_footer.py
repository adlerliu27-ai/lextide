"""Idle footer collapses; actionable failures and the independent guard remain visible."""

import test_g8_generation as g8

from lextide.desktop.i18n import tr

desktop = g8.desktop


def test_idle_import_and_ready_have_no_footer_noise(desktop):
    w, c, _, starts, _ = desktop
    assert w.status_label.text() == "" and w.status_label.isHidden()
    c._imported({"text": "A clear sentence.", "source_name": "sample.txt"}, c.revision)
    assert w.status_label.text() == "" and w.status_label.isHidden()
    c._engines({"media_tools": {k: {"available": True} for k in ("ffmpeg", "ffprobe")}})
    assert w.status_label.text() == "" and w.status_label.isHidden()
    assert not starts


def test_error_and_progress_visible_guard_independent(desktop):
    w, c, requests, starts, errors = desktop
    c._error("OCR_UNAVAILABLE: Missing provider")
    assert errors and w.status_label.text() and not w.status_label.isHidden()
    w.status_label.clear()
    w.speech_edit.setPlainText("A sentence. 中文")
    assert not w.generate_button.isEnabled()
    assert w.studio_status_label.text() == tr(c._reading_issue())
    assert w.status_label.isHidden() and not starts
    w.speech_edit.setPlainText("A clear sentence.")
    c.generate()
    assert requests and not starts
    assert w.status_label.text() == tr("Preparing audio…")
    assert not w.status_label.isHidden()
