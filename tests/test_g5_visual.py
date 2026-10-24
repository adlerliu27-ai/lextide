"""Rendered playback controls and brand transparency; no TTS or network access."""

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QStyle, QStyleOptionSlider  # noqa: E402

from lextide.desktop.theme import DARK, LIGHT  # noqa: E402
from lextide.desktop.window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    value = QApplication.instance() or QApplication([])
    value.setQuitOnLastWindowClosed(False)
    return value


@pytest.fixture
def window(app):
    value = MainWindow()
    value.show()
    app.processEvents()
    yield value
    value.close_allowed = True
    value.close()
    value.deleteLater()
    app.processEvents()


def render(widget):
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint())
    painter.end()
    return image


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [1180, 980])
@pytest.mark.parametrize("enabled", [False, True])
def test_player_slider_render_has_no_background_slab(window, app, theme, width, enabled):
    window.set_theme(theme)
    window.resize(width, 800)
    window.seek_slider.setEnabled(enabled)
    window.seek_slider.setRange(0, 120000 if enabled else 0)
    window.seek_slider.setValue(45000 if enabled else 0)
    app.processEvents()
    colors = DARK if theme == "dark" else LIGHT
    for slider in (window.seek_slider, window.volume_slider):
        image = render(slider)
        # The 24 px hit area must stay transparent outside the thin track/handle.
        for y in (2, image.height() - 3):
            for x in (2, image.width() // 2, image.width() - 3):
                assert image.pixelColor(x, y).alpha() == 0
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        groove = slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, slider
        )
        handle = slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, slider
        )
        assert 4 <= groove.height() <= 6
        assert handle.height() <= 14
        assert image.pixelColor(handle.center()) == QColor(
            colors["accent"] if slider.isEnabled() else colors["disabled"]
        )


def test_gui_brand_marks_have_transparent_corners_and_colored_symbols(window):
    for name in ("SidebarBrand", "OverviewBrand"):
        mark = window.findChild(QLabel, name)
        assert mark is not None
        image = mark.pixmap().toImage()
        assert image.hasAlphaChannel()
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(image.width() - 1, image.height() - 1).alpha() == 0
        assert any(
            image.pixelColor(x, y).alpha() > 0
            for y in range(image.height())
            for x in range(image.width())
        )


def test_styled_sliders_retain_native_drag_and_keyboard(window, app):
    slider = window.seek_slider
    slider.setEnabled(True)
    slider.setRange(0, 120000)
    slider.setSingleStep(1000)
    slider.setPageStep(5000)
    slider.setValue(30000)
    app.processEvents()
    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    handle = slider.style().subControlRect(
        QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, slider
    )
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=handle.center())
    assert slider.isSliderDown()
    target = QPoint(slider.width() * 3 // 4, handle.center().y())
    QTest.mouseMove(slider, target)
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=target)
    assert not slider.isSliderDown()
    assert slider.value() > 60000
    value = slider.value()
    QTest.keyClick(slider, Qt.Key.Key_Right)
    assert slider.value() == value + 1000
    QTest.keyClick(slider, Qt.Key.Key_PageUp)
    assert slider.value() == value + 6000
    window.volume_slider.setValue(80)
    QTest.keyClick(window.volume_slider, Qt.Key.Key_Left)
    assert window.volume_slider.value() == 79
