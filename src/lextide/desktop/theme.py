"""Native Qt theme derived from the approved Lextide UI Design System 1.1.

The 1.1 HTML's semantic colors supersede the original 1.0 JSON resource.
No web view or bundled system fonts are needed.
"""

from __future__ import annotations

import re
from pathlib import Path
from string import Template

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ASSETS = Path(__file__).parent / "assets"

LIGHT = {
    "canvas": "#F7F8FA",
    "surface": "#FFFFFF",
    "sidebar": "#F5F7FA",
    "subtle": "#F8FAFC",
    "tint": "#F4F8FF",
    "text": "#192334",
    "soft": "#334158",
    "muted": "#5E6C80",
    "disabled": "#7F8A9A",
    "line": "#E1E7EF",
    "control": "#8492A6",
    "accent": "#1762D8",
    "accent_hover": "#1154C3",
    "on_accent": "#FFFFFF",
    "accent_soft": "#EAF2FF",
    "hover": "#EFF3F9",
    "success": "#22734E",
    "error": "#B12F41",
}
DARK = {
    "canvas": "#101722",
    "surface": "#182230",
    "sidebar": "#131D2A",
    "subtle": "#1D2939",
    "tint": "#1B2B42",
    "text": "#EDF3FA",
    "soft": "#D1DCEA",
    "muted": "#A6B4C7",
    "disabled": "#7F91A7",
    "line": "#324257",
    "control": "#778BA5",
    "accent": "#8DBAFF",
    "accent_hover": "#A4C8FF",
    "on_accent": "#11223B",
    "accent_soft": "#223B5F",
    "hover": "#233249",
    "success": "#85D6B3",
    "error": "#FFA5B2",
}


def resolved_theme(preference: str) -> str:
    if preference in {"light", "dark"}:
        return preference
    app = QApplication.instance()
    if app and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
        return "dark"
    return "light"


def icon(name: str, color: str = "#5E6C80", size: int = 20) -> QIcon:
    """Tint the approved line-icon resource, preserving its original geometry."""
    path = ASSETS / "icons" / f"{name}.svg"
    if not path.is_file():
        return QIcon()
    svg = re.sub(r'stroke="#[0-9A-Fa-f]{6}"', f'stroke="{color}"', path.read_text())
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def brand_symbol(size: int) -> QPixmap:
    """Render the approved transparent SVG without changing its colors or viewBox."""
    renderer = QSvgRenderer(str(ASSETS / "brand" / "lextide-symbol-editable.svg"))
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return pixmap


def apply_theme(preference: str = "system") -> dict[str, str]:
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("Create QApplication before applying a theme")
    c = DARK if resolved_theme(preference) == "dark" else LIGHT
    palette = QPalette()
    for role, key in (
        (QPalette.ColorRole.Window, "surface"),
        (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Base, "surface"),
        (QPalette.ColorRole.AlternateBase, "subtle"),
        (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.Button, "surface"),
        (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.Highlight, "accent"),
        (QPalette.ColorRole.HighlightedText, "on_accent"),
        (QPalette.ColorRole.ToolTipBase, "surface"),
        (QPalette.ColorRole.ToolTipText, "text"),
        (QPalette.ColorRole.PlaceholderText, "muted"),
    ):
        palette.setColor(role, QColor(c[key]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["disabled"]))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["disabled"])
    )
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(c))
    return c


def stylesheet(c: dict[str, str]) -> str:
    transcript_arrow = (
        ASSETS
        / "icons"
        / (
            "font-size-chevron-dark.svg"
            if c["surface"] == DARK["surface"]
            else "font-size-chevron-light.svg"
        )
    )
    return Template(
        """
        QWidget { color: ${text}; font-size: 13px; }
        QMainWindow,
        QWidget#MainContent,
        QWidget#Page,
        QScrollArea,
        QScrollArea > QWidget > QWidget {
            background: ${surface};
        }
        QFrame#Sidebar { background: ${sidebar}; border-right: 1px solid ${line}; }
        QFrame#Toolbar { background: ${surface}; border-bottom: 1px solid ${line}; }
        QFrame#Player { background: ${surface}; border-top: 1px solid ${line}; }
        QFrame#Card,
        QFrame#TextPanel {
            background: ${surface};
            border: 1px solid ${line};
            border-radius: 13px;
            }
        QFrame#Hero { background: ${tint}; border: 1px solid ${line}; border-radius: 17px; }
        QFrame#Review { background: ${tint}; border: 1px solid ${line}; border-radius: 12px; }
        QFrame#Approval, QFrame#SettingsFooter {
            background: ${surface}; border-top: 1px solid ${line};
        }
        QLabel { background: transparent; border: none; }
        QLabel#OverviewBrand { border: 1px solid ${line}; border-radius: 71px; }
        QLabel[role="muted"] { color: ${muted}; font-size: 12px; }
        QLabel[role="micro"] { color: ${muted}; font-size: 10px; }
        QLabel[role="eyebrow"] { color: ${accent}; font-size: 10px; font-weight: 700; }
        QLabel[role="title"] { font-size: 28px; font-weight: 700; }
        QLabel[role="heading"] { font-size: 15px; font-weight: 600; }
        QLabel[role="brand"] { font-size: 23px; font-weight: 700; }
        QLabel[role="success"] { color: ${success}; }
        QLabel[role="error"] { color: ${error}; }
        QLabel#Badge {
            background: ${accent_soft};
            color: ${accent};
            border-radius: 7px;
            padding: 5px 8px;
            font-size: 11px;
            }
        QPushButton, QToolButton {
            background: ${surface}; border: 1px solid ${control}; border-radius: 8px;
            min-height: 30px; padding: 3px 11px; font-weight: 500;
        }
        QPushButton:hover, QToolButton:hover { background: ${hover}; }
        QPushButton:pressed, QToolButton:pressed { background: ${accent_soft}; }
        QPushButton:focus, QToolButton:focus, QLineEdit:focus, QTextEdit:focus,
        QPlainTextEdit:focus,
        QComboBox:focus,
        QSpinBox:focus,
        QListWidget:focus,
        QTableWidget:focus {
            border: 2px solid ${accent};
        }
        QPushButton[primary="true"] {
            background: ${accent}; color: ${on_accent}; border-color: ${accent}; font-weight: 600;
        }
        QPushButton[primary="true"]:hover {
            background: ${accent_hover};
            border-color: ${accent_hover};
            }
        QPushButton[primary="true"]:pressed { background: ${accent}; }
        QPushButton[quiet="true"] { background: transparent; border-color: transparent; }
        QPushButton[quiet="true"]:hover { background: ${hover}; }
        QToolButton#Disclosure {
            background: transparent;
            border-color: transparent;
            text-align: left;
            padding: 2px 4px;
            font-weight: 600;
        }
        QToolButton#Disclosure:hover { background: ${hover}; }
        QToolButton#Disclosure:focus { border-color: ${accent}; }
        QPushButton#NavButton {
            background: transparent;
            color: ${muted};
            text-align: left;
            border: 1px solid transparent;
            min-height: 36px; padding: 3px 12px; border-radius: 9px;
        }
        QPushButton#NavButton:hover { background: ${hover}; }
        QPushButton#NavButton:checked {
            background: ${accent_soft};
            color: ${accent};
            font-weight: 600;
            }
        QPushButton#NavButton:focus { border: 2px solid ${accent}; }
        QPushButton:disabled, QToolButton:disabled {
            background: ${subtle}; color: ${disabled}; border-color: ${line};
        }
        QPushButton#PlayButton {
            border-radius: 19px;
            min-width: 28px;
            min-height: 30px;
            padding: 3px;
            }
        QLineEdit, QComboBox, QSpinBox {
            background: ${surface}; border: 1px solid ${control}; border-radius: 7px;
            min-height: 29px; padding: 3px 8px; selection-background-color: ${accent};
        }
        QComboBox { padding-right: 24px; }
        QComboBox::drop-down { border: none; width: 22px; }
        QComboBox#TranscriptFontSize { padding-right: 28px; }
        QComboBox#TranscriptFontSize::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            border: none;
            width: 24px;
        }
        QComboBox#TranscriptFontSize::down-arrow {
            image: url("${transcript_arrow}");
            width: 12px;
            height: 8px;
        }
        QSpinBox::up-button, QSpinBox::down-button { border: none; width: 17px; }
        QComboBox QAbstractItemView {
            background: ${surface};
            selection-background-color: ${accent_soft};
            selection-color: ${text};
            }
        QLineEdit:disabled,
        QComboBox:disabled,
        QSpinBox:disabled {
            color: ${disabled};
            background: ${subtle};
            border-color: ${line};
            }
        QPlainTextEdit, QTextEdit {
            background: ${surface}; color: ${soft}; border: 1px solid ${line};
            border-radius: 8px; padding: 10px; selection-background-color: ${accent};
        }
        QPlainTextEdit#OriginalText { background: ${subtle}; }
        QPlainTextEdit#ChangesText { background: ${tint}; font-size: 12px; }
        QCheckBox { spacing: 8px; min-height: 25px; }
        QCheckBox:disabled { color: ${disabled}; }
        QTabWidget::pane { border: none; }
        QTabBar::tab {
            color: ${muted};
            padding: 8px 11px;
            border: 1px solid ${line};
            background: ${subtle};
            }
        QTabBar::tab:selected { color: ${accent}; background: ${accent_soft}; }
        QTableWidget, QListWidget {
            background: ${surface}; alternate-background-color: ${subtle};
            border: 1px solid ${line}; border-radius: 10px; outline: 0; gridline-color: ${line};
        }
        QTableWidget::item { padding: 8px; border-bottom: 1px solid ${line}; }
        QTableWidget::item:selected,
        QListWidget::item:selected {
            background: ${accent_soft};
            color: ${text};
            }
        QTableWidget::item:hover, QListWidget::item:hover { background: ${hover}; }
        QHeaderView::section {
            background: ${subtle};
            color: ${muted};
            border: none;
            padding: 10px 8px;
            font-size: 11px;
            }
        QListWidget#Transcript::item {
            padding: 15px 12px;
            border-left: 3px solid transparent;
            border-radius: 7px;
            margin: 3px 8px;
            }
        QListWidget#Transcript::item:selected { border-left: 3px solid ${accent}; }
        QProgressBar {
            border: none;
            border-radius: 4px;
            background: ${line};
            height: 7px;
            color: ${muted};
            }
        QProgressBar::chunk { background: ${accent}; border-radius: 4px; }
        QSlider#PlaybackSeek, QSlider#PlaybackVolume {
            background: transparent; border: none; min-height: 24px; max-height: 24px;
        }
        QSlider#PlaybackSeek::groove:horizontal, QSlider#PlaybackVolume::groove:horizontal {
            height: 4px;
            border: none;
            border-radius: 2px;
            background: ${line};
            }
        QSlider#PlaybackSeek::sub-page:horizontal, QSlider#PlaybackVolume::sub-page:horizontal {
            background: ${accent}; border: none; border-radius: 2px;
        }
        QSlider#PlaybackSeek::add-page:horizontal, QSlider#PlaybackVolume::add-page:horizontal {
            background: ${line}; border: none; border-radius: 2px;
        }
        QSlider#PlaybackSeek::handle:horizontal, QSlider#PlaybackVolume::handle:horizontal {
            width: 12px;
            margin: -4px 0;
            border-radius: 6px;
            background: ${accent};
            }
        /* Keep pseudo-states after the subcontrol: the inverse order paints a slab on Cocoa. */
        QSlider#PlaybackSeek::handle:horizontal:disabled,
        QSlider#PlaybackVolume::handle:horizontal:disabled { background: ${disabled}; }
        QScrollBar:vertical { border: none; background: transparent; width: 9px; margin: 2px; }
        QScrollBar::handle:vertical {
            background: ${control};
            min-height: 30px;
            border-radius: 3px;
            }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        QSplitter::handle { background: transparent; }
        QSplitter::handle:hover { background: ${accent_soft}; }
        QToolTip {
            color: ${text};
            background: ${surface};
            border: 1px solid ${line};
            padding: 6px;
            }
        QStatusBar { background: ${surface}; color: ${muted}; border-top: 1px solid ${line}; }
    """
    ).substitute({**c, "transcript_arrow": transcript_arrow.as_posix()})
