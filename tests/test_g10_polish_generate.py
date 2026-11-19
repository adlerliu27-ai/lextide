"""Generic labels never replace per-transmission online consent."""

import hashlib

import pytest
import test_g8_generation as g8

from lextide.desktop import controller
from lextide.desktop.i18n import tr

desktop = g8.desktop


@pytest.mark.parametrize(
    "outcome", ["decline", "edit", "rate", "voice", "engine", "close", "allow"]
)
def test_modal_authorization_rechecks_exact_snapshot(desktop, monkeypatch, outcome):
    w, c, requests, starts, _ = desktop

    def consent(parent, text, settings, sha):
        assert not starts and c.confirming and not w.generate_button.isEnabled()
        assert text == w.speech_edit.toPlainText()
        assert sha == hashlib.sha256(text.encode()).hexdigest()
        c.generate()  # A duplicate click inside the modal cannot create another request.
        if outcome == "edit":
            w.speech_edit.insertPlainText(" Changed.")
        elif outcome == "rate":
            w.rate_spin.setValue(4)
        elif outcome == "voice":
            w.gender_combo.setCurrentIndex(w.gender_combo.findData("female"))
        elif outcome == "engine":
            w.engine_combo.setCurrentIndex(w.engine_combo.findData("kokoro"))
        elif outcome == "close":
            c.closing = True
        return outcome != "decline"

    monkeypatch.setattr(controller, "confirm_transmission", consent)
    c.generate()
    g8.complete(requests[0])
    assert len(requests) == 1
    assert bool(starts) is (outcome == "allow")
    g8.complete(requests[0])
    assert len(starts) <= 1


def test_regenerate_requires_matching_verified_output(desktop, tmp_path, monkeypatch):
    w, c, _, starts, _ = desktop
    assert w.generate_button.text() == tr("Generate Audio")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"verified-fixture")
    signature = c._signature()
    monkeypatch.setattr(c.player, "load", lambda *_: None)
    monkeypatch.setattr(c.bilingual, "load", lambda *_: None)
    c._verified({"audio_path": str(audio)}, "Synthetic session", signature)
    assert w.generate_button.text() == tr("Regenerate Audio")
    for control, change, restore in [
        (
            w.speech_edit,
            lambda: w.speech_edit.insertPlainText(" Extra."),
            lambda: w.speech_edit.setPlainText('The teacher said, "Practice every day."'),
        ),
        (w.rate_spin, lambda: w.rate_spin.setValue(5), lambda: w.rate_spin.setValue(0)),
        (
            w.gender_combo,
            lambda: w.gender_combo.setCurrentIndex(1),
            lambda: w.gender_combo.setCurrentIndex(0),
        ),
    ]:
        old = w.rate_spin.value()
        change()
        assert w.generate_button.text() == tr("Generate Audio")
        restore()
        if control is w.rate_spin:
            w.rate_spin.setValue(old)
        assert w.generate_button.text() == tr("Regenerate Audio")
    audio.unlink()
    c._gate()
    assert w.generate_button.text() == tr("Generate Audio") and not starts


@pytest.mark.parametrize("engine", ["edge-tts", "kokoro"])
@pytest.mark.parametrize("gender", ["male", "female"])
def test_generic_label_keeps_online_disclosure(desktop, engine, gender):
    w, c, _, _, _ = desktop
    w.engine_combo.setCurrentIndex(w.engine_combo.findData(engine))
    w.gender_combo.setCurrentIndex(w.gender_combo.findData(gender))
    assert w.generate_button.text() == tr("Generate Audio")
    assert w.generate_button.icon().isNull()
    assert (not w.online_notice.isHidden()) is (engine == "edge-tts")
    assert w.engine_decision_status.text() == tr(
        "Edge online · {voice}" if engine == "edge-tts" else "Kokoro offline · {voice}",
        voice=c._settings()["voice"],
    )
