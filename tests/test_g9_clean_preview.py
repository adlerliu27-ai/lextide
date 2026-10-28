"""G9 local preparation and consent races, using self-authored text only."""

import asyncio
import hashlib

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g8_generation as g8  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from lextide.desktop import service  # noqa: E402
from lextide.desktop.i18n import tr  # noqa: E402

desktop = g8.desktop

SOURCE = (
    "Maya recorded 1,500 birds.玛雅记录了一千五百只鸟。\n\n"
    "Small observations matter.\n小小的观察很重要。"
)
ENGLISH = "Maya recorded 1,500 birds.\n\nSmall observations matter."


def import_source(c, text=SOURCE):
    c._imported({"text": text, "source_name": "Local test", "format": "text"}, c.revision)


def finish_clean(request):
    payload, callback, _ = request
    assert payload["command"] == "clean"
    assert payload.get("allow_online") is not True
    data = asyncio.run(service.dispatch(payload))
    callback(data)
    return data


def test_selection_prepares_enhanced_preview_without_extra_button(desktop):
    w, c, requests, starts, errors = desktop
    import_source(c)
    assert not requests and not starts
    assert not hasattr(w, "cleaning_strength_combo")
    assert w.clean_mode_combo.currentData() == "original"
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    assert len(requests) == 1
    assert requests[0][0]["cleaning_mode"] == "remove_chinese_translations"
    assert c.pending_clean is not None
    assert not w.generate_button.isEnabled()
    c.generate()
    assert len(requests) == 1 and not starts
    data = finish_clean(requests[0])
    assert w.source_edit.toPlainText() == SOURCE
    assert w.speech_edit.toPlainText() == ENGLISH
    assert data["removed_chinese_segments"] == 2
    assert c.cleaning["version"] == "english-translations-v1"
    assert c.pending_clean is None and w.generate_button.isEnabled()
    assert w.clean_button.text() == tr("Apply")
    assert not starts and not errors


@pytest.mark.parametrize("engine", ["edge-tts", "kokoro"])
def test_manual_final_edit_is_the_only_authorized_text(desktop, engine):
    w, c, requests, starts, errors = desktop
    import_source(c)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    finish_clean(requests[0])
    final = ENGLISH + "\nThis is my final note."
    w.speech_edit.setPlainText(final)
    w.engine_combo.setCurrentIndex(w.engine_combo.findData(engine))
    assert len(requests) == 1 and not starts
    w.generate_button.click()
    assert requests[-1][0]["command"] == "preview"
    g8.complete(requests[-1])
    assert len(starts) == 1
    payload = starts[0]
    assert payload["text"] == final
    assert payload["approved_sha256"] == hashlib.sha256(final.encode()).hexdigest()
    assert payload["allow_online"] is (engine == "edge-tts")
    original, provenance = service._provenance(payload, final)
    assert original == SOURCE
    assert provenance["text_preparation"]["cleaned_text"] == ENGLISH
    assert provenance["text_preparation"]["reviewed_sha256"] == payload["approved_sha256"]
    assert not errors


def test_edited_draft_can_decline_replacement_before_automatic_clean(desktop, monkeypatch):
    w, c, requests, starts, _ = desktop
    import_source(c)
    final = "My carefully edited English draft."
    w.speech_edit.setPlainText(final)
    questions = []

    def decline(*args, **kwargs):
        questions.append(args)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", decline)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    assert len(questions) == 1
    assert w.speech_edit.toPlainText() == final
    assert w.source_edit.toPlainText() == SOURCE
    assert c.pending_clean is None
    assert not requests and not starts


def test_edit_while_cleaning_cannot_be_overwritten_by_late_result(desktop):
    w, c, requests, starts, _ = desktop
    import_source(c)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    final = "I edited this sentence while local preparation was running."
    w.speech_edit.setPlainText(final)
    finish_clean(requests[0])
    assert w.speech_edit.toPlainText() == final
    assert w.source_edit.toPlainText() == SOURCE
    assert c.pending_clean is None and not starts


def test_duplicate_clean_callback_cannot_overwrite_a_new_edit(desktop):
    w, c, requests, starts, _ = desktop
    import_source(c)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    finish_clean(requests[0])
    final = ENGLISH + " My later edit remains."
    w.speech_edit.setPlainText(final)
    finish_clean(requests[0])
    assert w.speech_edit.toPlainText() == final
    assert not starts


def test_new_import_in_english_mode_ignores_older_clean_callback(desktop):
    w, c, requests, starts, errors = desktop
    import_source(c)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    first = requests[0]
    new_source = "Each page offers a new idea.\n每一页都带来新的想法。"
    import_source(c, new_source)
    assert len(requests) == 2
    second = requests[1]
    pending = c.pending_clean
    finish_clean(first)
    assert c.pending_clean is pending
    assert w.source_edit.toPlainText() == new_source
    assert w.speech_edit.toPlainText() == new_source
    assert not w.generate_button.isEnabled()
    finish_clean(second)
    assert w.speech_edit.toPlainText() == "Each page offers a new idea."
    assert c.provenance["text"] == new_source
    assert not starts and not errors


def test_mode_change_invalidates_pending_clean(desktop):
    w, c, requests, starts, _ = desktop
    import_source(c)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    first = requests[0]
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("original"))
    finish_clean(first)
    assert w.speech_edit.toPlainText() == SOURCE
    assert w.source_edit.toPlainText() == SOURCE
    assert not starts


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("学习需要练习。", ""),
        (
            "The word 北京 appears in this English example.",
            "The word 北京 appears in this English example.",
        ),
    ],
)
def test_empty_or_uncertain_result_is_reviewable_but_cannot_generate(desktop, source, expected):
    w, c, requests, starts, _ = desktop
    import_source(c, source)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    data = finish_clean(requests[0])
    assert w.speech_edit.toPlainText() == expected
    assert w.source_edit.toPlainText() == source
    assert data["warnings"]
    assert not w.generate_button.isEnabled()
    c.generate()
    assert len(requests) == 1 and not starts


def test_cleaning_worker_failure_keeps_source_and_draft(desktop):
    w, c, requests, starts, errors = desktop
    import_source(c)
    w.clean_mode_combo.setCurrentIndex(w.clean_mode_combo.findData("english"))
    requests[0][2]["on_error"]("Local text preparation failed.")
    assert w.source_edit.toPlainText() == SOURCE
    assert w.speech_edit.toPlainText() == SOURCE
    assert c.pending_clean is None
    assert not w.generate_button.isEnabled()
    assert errors and not starts
