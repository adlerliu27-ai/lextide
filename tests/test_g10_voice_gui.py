"""Voice choices, cached Auto evidence and the explicit generation boundary."""

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g8_generation as g8  # noqa: E402

from lextide.desktop.preferences import load_preferences  # noqa: E402

desktop = g8.desktop


def auto_result(c, reachable, *, edge_ids=None):
    edge = dict(c.capabilities["edge-tts"])
    if edge_ids is not None:
        edge["voices"] = edge_ids
    return {
        "edge_probe": {"reachable": reachable},
        "model": {"ready": True},
        "offline_runtime": {"ready": True},
        "engines": [
            {"engine_id": "kokoro", **c.capabilities["kokoro"]},
            {"engine_id": "edge-tts", **edge},
        ],
    }


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_gender_is_independent_and_pin_requires_visible_replacement(desktop, theme):
    w, c, requests, starts, _ = desktop
    w.set_theme(theme)
    assert c._settings()["voice"] == "en-US-GuyNeural"
    w.gender_combo.setCurrentIndex(w.gender_combo.findData("female"))
    assert load_preferences(c.root)["voice_gender"] == "female"
    for engine, voice in (("kokoro", "af_heart"), ("edge-tts", "en-US-AriaNeural")):
        w.engine_combo.setCurrentIndex(w.engine_combo.findData(engine))
        assert c._settings()["voice"] == voice
        assert voice in w.engine_decision_status.text()
        assert w.generate_button.isEnabled()
        assert not requests and not starts
    w.voice_combo.setCurrentIndex(1)
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("kokoro"))
    assert c.pinned_voice == "en-US-AriaNeural"
    assert w.voice_combo.currentIndex() == -1
    assert not w.generate_button.isEnabled()
    assert "en-US-AriaNeural" in w.engine_decision_status.text()
    w.voice_combo.setCurrentIndex(0)
    assert c.pinned_voice is None and c._settings()["voice"] == "af_heart"
    assert w.generate_button.isEnabled() and not requests


@pytest.mark.parametrize("reachable", [True, False])
def test_auto_uses_only_cached_catalogue_for_gender_changes(desktop, reachable):
    w, c, requests, starts, _ = desktop
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("auto"))
    payload, callback, _ = requests.pop()
    assert payload == {"command": "auto_probe", "runtime": c._runtime()}
    callback(auto_result(c, reachable, edge_ids=["en-US-GuyNeural"]))
    expected = "en-US-GuyNeural" if reachable else "am_michael"
    assert c._settings()["voice"] == expected
    w.gender_combo.setCurrentIndex(w.gender_combo.findData("female"))
    assert c._effective_engine() == "kokoro"
    assert c._settings()["voice"] == "af_heart"
    assert "af_heart" in w.engine_decision_status.text()
    assert not w.online_notice.isVisible() and not requests and not starts
    w.gender_combo.setCurrentIndex(w.gender_combo.findData("male"))
    assert c._settings()["voice"] == expected
    assert not requests


def test_disappearing_or_invalid_voice_blocks_generation(desktop):
    w, c, requests, starts, _ = desktop
    w.voice_combo.setCurrentIndex(1)
    w.generate_button.click()
    pending = requests.pop()
    c._engines({"engines": [{"engine_id": "edge-tts", "available": True, "voices": []}]})
    g8.complete(pending)
    assert not starts and not w.generate_button.isEnabled()
    assert c._settings()["voice"] == ""
    # Even a forged combo row must not reach the preview/start boundary.
    w.voice_combo.addItem("Unknown", "en-US-ImpostorNeural")
    c.generate()
    assert not starts and not requests
