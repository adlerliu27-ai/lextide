"""Studio must approve the edited preview after dropping the translated speaker line."""

import hashlib

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g9_clean_preview as preview  # noqa: E402

desktop = preview.desktop


def test_goodwin_preview_edit_and_send_use_the_same_final_hash(desktop):
    w, c, requests, starts, errors = desktop
    original = "We interview the doctor.\nGoodwin 博士：我是 Fred Goodwin 博士。"
    preview.import_source(c, original)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    preview.finish_clean(requests[0])
    assert w.speech_edit.toPlainText() == "We interview the doctor."
    assert w.source_edit.toPlainText() == original
    assert not starts
    edited = "We interviewed the doctor today."
    w.speech_edit.setPlainText(edited)
    w.generate_button.click()
    preview.g8.complete(requests[-1])
    assert len(starts) == 1
    assert starts[0]["text"] == edited
    assert starts[0]["approved_sha256"] == hashlib.sha256(edited.encode("utf-8")).hexdigest()
    assert starts[0]["provenance"]["text"] == original
    assert not errors


def test_names_preview_edited_back_to_chinese_cannot_start(desktop):
    w, c, requests, starts, errors = desktop
    original = "We interview the doctor.\nGoodwin 博士：我是 Fred Goodwin 博士。"
    preview.import_source(c, original)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    preview.finish_clean(requests[0])
    assert w.speech_edit.toPlainText() == "We interview the doctor."
    w.speech_edit.setPlainText("I studied at 北京大学 in 2025.")
    assert not w.generate_button.isEnabled()
    c.generate()
    assert len(requests) == 1 and not starts
    assert w.source_edit.toPlainText() == original
