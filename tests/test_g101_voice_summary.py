"""Studio's pinned summary reflects the settings sent by the existing Generate path."""

import pytest

pytest.importorskip("PySide6.QtWidgets")
import test_g8_generation as g8  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from test_g10_voice_gui import auto_result  # noqa: E402

from lextide.desktop.i18n import tr  # noqa: E402

desktop = g8.desktop


@pytest.mark.parametrize(
    ("engine", "gender", "provider_id", "name", "rate", "theme"),
    [
        ("edge-tts", "male", "en-US-GuyNeural", "Guy", 0, "light"),
        ("edge-tts", "female", "en-US-AriaNeural", "Aria", 11, "dark"),
        ("kokoro", "male", "am_michael", "Michael", -4, "dark"),
        ("kokoro", "female", "af_heart", "Heart", 5, "light"),
    ],
)
def test_footer_matches_generation_payload(desktop, engine, gender, provider_id, name, rate, theme):
    w, _, requests, starts, _ = desktop
    w.set_theme(theme)
    w.resize(w.minimumSize())
    w.show_page("Studio")
    w.show()
    w.engine_combo.setCurrentIndex(w.engine_combo.findData(engine))
    w.gender_combo.setCurrentIndex(w.gender_combo.findData(gender))
    w.rate_spin.setValue(rate)
    QApplication.processEvents()
    summary = w.generation_summary
    expected_rate = f"{rate:+d}%"
    assert summary.text() == tr(
        "Edge online · {voice} · Rate {rate}"
        if engine == "edge-tts"
        else "Kokoro offline · {voice} · Rate {rate}",
        voice=name,
        rate=expected_rate,
    )
    assert provider_id in summary.toolTip()
    assert summary.isVisible() and summary.parentWidget() is w.generate_button.parentWidget()
    assert summary.geometry().bottom() < w.generate_button.geometry().top()
    assert summary.contentsRect().width() >= summary.fontMetrics().horizontalAdvance(summary.text())
    assert summary.height() >= summary.heightForWidth(summary.width())
    assert w.generate_button.width() >= w.generate_button.sizeHint().width()
    assert w.online_notice.isVisible() is (engine == "edge-tts")
    w.generate_button.click()
    g8.complete(requests[0])
    assert len(starts) == 1
    settings = starts[0]["settings"]
    assert (settings["engine"], settings["voice"], settings["rate"]) == (
        engine,
        provider_id,
        expected_rate,
    )


@pytest.mark.parametrize("reachable", [True, False])
def test_auto_replaces_pending_summary_with_effective_voice(desktop, reachable):
    w, c, requests, starts, _ = desktop
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("auto"))
    assert w.generation_summary.text() == tr("Checking engine…")
    assert not w.generate_button.isEnabled()
    _, resolve_probe, _ = requests.pop()
    resolve_probe(auto_result(c, reachable))
    assert w.generation_summary.text() == tr(
        "Edge online · {voice} · Rate {rate}"
        if reachable
        else "Kokoro offline · {voice} · Rate {rate}",
        voice="Guy" if reachable else "Michael",
        rate=c._settings()["rate"],
    )
    assert not requests and not starts


def test_incompatible_pin_clears_previous_voice_and_rate(desktop):
    w, c, requests, starts, _ = desktop
    w.voice_combo.setCurrentIndex(1)
    assert c.pinned_voice == "en-US-GuyNeural"
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("kokoro"))
    assert w.generation_summary.text() == tr("Check speech settings")
    assert not w.generate_button.isEnabled()
    assert not requests and not starts
