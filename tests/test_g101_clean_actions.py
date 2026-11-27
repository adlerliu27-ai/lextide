"""The editable local preview has one optional adoption step and never grants consent."""

import hashlib

import pytest
import test_g8_generation as g8
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPushButton
from test_g9_clean_preview import ENGLISH, SOURCE, finish_clean, import_source

from lextide.desktop import controller
from lextide.desktop.i18n import current_language, tr

desktop = g8.desktop


def preview(w, c, requests):
    import_source(c)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    finish_clean(requests.pop())
    assert w.speech_edit.toPlainText() == ENGLISH
    assert w.reading_heading.text() == tr("English preview")
    assert not w.discard_preview_button.isHidden()
    assert (
        "Generate Audio" in w.clean_summary_label.text()
        or "生成音频" in w.clean_summary_label.text()
    )


def test_apply_is_local_and_preserves_visible_edits_after_reentry_and_language_change(desktop):
    w, c, requests, starts, errors = desktop
    preview(w, c, requests)
    edited = ENGLISH + " I checked it."
    w.speech_edit.setPlainText(edited)
    w.show_page("Library")
    w.show_page("Studio")
    c.refresh_localized_text("zh_CN" if current_language() == "en" else "en")
    assert w.reading_heading.text() == tr("English preview")
    w.clean_button.click()
    assert w.reading_heading.text() == tr("Reading text")
    assert w.clean_button.isHidden() and w.discard_preview_button.isHidden()
    assert w.source_edit.toPlainText() == SOURCE and w.speech_edit.toPlainText() == edited
    assert not requests and not starts and not errors
    w.speech_edit.insertPlainText("Fresh edit. ")
    assert not w.clean_button.isHidden() and w.clean_button.text() == tr("Re-apply English Only")
    w.speech_edit.undo()
    assert w.speech_edit.toPlainText() == edited and w.clean_button.isHidden()


def test_discard_preview_restores_exact_draft_and_english_guard(desktop):
    w, c, requests, starts, _ = desktop
    preview(w, c, requests)
    w.discard_preview_button.click()
    assert w.source_edit.toPlainText() == SOURCE == w.speech_edit.toPlainText()
    assert w.discard_preview_button.isHidden() and w.clean_button.text() == tr("Preview English")
    assert c.cleaning is None and not w.generate_button.isEnabled()
    c.generate()
    assert not requests and not starts


def test_switching_mode_can_keep_edited_preview_and_reapply_can_be_declined(desktop, monkeypatch):
    w, c, requests, starts, _ = desktop
    preview(w, c, requests)
    keep = ENGLISH + " My edit."
    w.speech_edit.setPlainText(keep)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("original"))
    assert w.clean_mode_combo.currentData() == "english" and w.speech_edit.toPlainText() == keep
    w.clean_button.click()
    keep += " More."
    w.speech_edit.setPlainText(keep)
    w.clean_button.click()
    assert w.speech_edit.toPlainText() == keep and not requests
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    w.clean_button.click()
    finish_clean(requests.pop())
    assert w.speech_edit.toPlainText() == ENGLISH and w.clean_button.isHidden()
    assert w.source_edit.toPlainText() == SOURCE and not starts


@pytest.mark.parametrize("allow", [False, True])
def test_generate_uses_preview_after_per_job_review_only(desktop, monkeypatch, allow):
    w, c, requests, starts, _ = desktop
    preview(w, c, requests)
    edited = ENGLISH + " I added a phrase."
    w.speech_edit.setPlainText(edited)
    observations = []

    def consent(_parent, text, settings, digest):
        observations.append((text, settings["voice"], digest))
        return allow

    monkeypatch.setattr(controller, "confirm_transmission", consent)
    w.generate_button.click()
    g8.complete(requests.pop())
    digest = hashlib.sha256(edited.encode()).hexdigest()
    assert observations == [(edited, c._settings()["voice"], digest)]
    assert w.speech_edit.toPlainText() == edited
    assert w.discard_preview_button.isHidden() is allow
    if allow:
        assert starts[0]["text"] == edited and starts[0]["approved_sha256"] == digest
        assert w.clean_button.isHidden()
    else:
        assert not starts


def test_studio_import_button_and_open_shortcut_use_one_file_picker(desktop, monkeypatch):
    w, _, requests, starts, _ = desktop
    w.show_page("Studio")
    w.show()
    buttons = [
        button
        for button in w.findChildren(QPushButton)
        if button.isVisible() and button.text() == tr("Import file")
    ]
    assert buttons == [w.import_button]
    assert w.paste_source_button.isVisible()
    picked = []

    def cancel_picker(*_args, **_kwargs):
        picked.append(True)
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", cancel_picker)
    w.import_button.click()
    [open_action] = [
        action
        for action in w.findChildren(QAction)
        if action.shortcut().matches(QKeySequence(QKeySequence.StandardKey.Open))
        == QKeySequence.SequenceMatch.ExactMatch
    ]
    open_action.trigger()
    assert len(picked) == 2
    assert not requests and not starts
