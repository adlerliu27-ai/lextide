"""G9 submission and local preview safety; workers are intercepted, no network."""

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g8_generation as g8  # noqa: E402

complete = g8.complete
desktop = g8.desktop


@pytest.mark.parametrize("engine", ["edge-tts", "kokoro"])
def test_mixed_reading_text_blocked_before_preview(desktop, engine):
    w, c, requests, starts, _ = desktop
    w.engine_combo.setCurrentIndex(w.engine_combo.findData(engine))
    w.speech_edit.setPlainText("A small bird landed nearby.\n一只小鸟落在附近。")
    assert not w.generate_button.isEnabled()
    c.generate()  # Direct invocation must not bypass the disabled control.
    assert not requests and not starts


def test_last_edit_to_mixed_text_cannot_submit_old_preview(desktop):
    w, c, requests, starts, _ = desktop
    w.generate_button.click()
    pending = requests[0]
    w.speech_edit.insertPlainText(" 北京")
    complete(pending)
    assert not starts and not w.generate_button.isEnabled()
    assert c.pending_submission is None


def test_auto_online_decision_still_blocks_mixed_text(desktop):
    w, c, requests, starts, _ = desktop
    w.speech_edit.setPlainText("Read this sentence. 阅读这个句子。")
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("auto"))
    payload, callback, _ = requests.pop()
    assert payload["command"] == "auto_probe" and "text" not in payload
    callback(
        {
            "decision": {"engine": "edge-tts", "ready": True, "reason": "Will use Edge online"},
            "engines": [{"engine_id": "edge-tts", **c.capabilities["edge-tts"]}],
        }
    )
    assert not w.generate_button.isEnabled()
    c.generate()
    assert not requests and not starts
