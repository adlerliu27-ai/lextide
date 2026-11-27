"""Carbon presentation tokens and native Qt widget styles for Lextide."""

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
    "canvas": "#F6F7F9",
    "surface": "#FFFFFF",
    "subtle": "#F8FAFC",
    "tint": "#F1F5F9",
    "hover": "#EEF2F7",
    "pressed": "#E2E8F0",
    "line": "#E2E8F0",
    "control": "#8492A6",
    "ink": "#0F172A",
    "secondary": "#334155",
    "muted": "#526071",
    "disabled": "#94A3B8",
    "accent": "#2563EB",
    "accent_hover": "#1D4ED8",
    "accent_pressed": "#1E40AF",
    "accent_soft": "#E9F2FF",
    "transcript_active_bg": "#F0F5FC",
    "focus_ring": "#3B82F6",
    "on_accent": "#FFFFFF",
    "success": "#15803D",
    "success_soft": "#ECFDF3",
    "warning": "#B45309",
    "warning_soft": "#FFFBEB",
    "warning_line": "#FCD34D",
    "error": "#B42318",
    "error_soft": "#FEF3F2",
    "error_line": "#FDA29B",
    "code_bg": "#F1F5F9",
    "sidebar": "#F6F7F9",
    "text": "#0F172A",
    "soft": "#334155",
}
DARK = {
    "canvas": "#0F1621",
    "surface": "#151F2E",
    "subtle": "#1A2638",
    "tint": "#202E44",
    "hover": "#24344D",
    "pressed": "#2C3F5E",
    "line": "#29384E",
    "control": "#6F839D",
    "ink": "#F1F5F9",
    "secondary": "#CBD5E1",
    "muted": "#94A3B8",
    "disabled": "#64748B",
    "accent": "#4F8CFF",
    "accent_hover": "#6EA1FF",
    "accent_pressed": "#3B76F6",
    "accent_soft": "#1E3354",
    "transcript_active_bg": "#1C2D46",
    "focus_ring": "#60A5FA",
    "on_accent": "#0B1220",
    "success": "#4ADE80",
    "success_soft": "#132E23",
    "warning": "#FBBF24",
    "warning_soft": "#2E2414",
    "warning_line": "#92400E",
    "error": "#F87171",
    "error_soft": "#311B1E",
    "error_line": "#991B1B",
    "code_bg": "#1B283B",
    "sidebar": "#0F1621",
    "text": "#F1F5F9",
    "soft": "#CBD5E1",
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


def status_dot(status: str, palette: QPalette) -> QIcon:
    colors = (
        DARK
        if palette.color(QPalette.ColorRole.Base).name().lower() == DARK["surface"].lower()
        else LIGHT
    )
    key = {"completed": "success", "failed": "error", "running": "accent", "paused": "warning"}.get(
        status, "muted"
    )
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colors[key]))
    painter.drawEllipse(1, 1, 14, 14)
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
    check = (
        ASSETS
        / "icons"
        / (
            "checkbox-check-dark.svg"
            if c["surface"] == DARK["surface"]
            else "checkbox-check-light.svg"
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
            border-radius: 12px;
            }
        QFrame#Hero { background: ${tint}; border: 1px solid ${line}; border-radius: 12px; }
        QFrame#Review { background: ${tint}; border: 1px solid ${line}; border-radius: 12px; }
        QFrame#Approval, QFrame#SettingsFooter {
            background: ${surface}; border-top: 1px solid ${line};
        }
        QLabel { background: transparent; border: none; }
        QLabel#OverviewBrand { border: none; background: transparent; }
        QLabel[role="muted"] { color: ${muted}; font-size: 12px; }
        QLabel[role="micro"] { color: ${muted}; font-size: 10px; }
        QLabel[role="eyebrow"] { color: ${accent}; font-size: 10px; font-weight: 700; }
        QLabel[role="title"] { font-size: 22px; font-weight: 700; }
        QLabel[role="heading"] { font-size: 14px; font-weight: 600; }
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
            min-height: 36px; padding: 3px 12px; border-radius: 8px;
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
            background: ${accent};
            border: 1px solid ${accent};
            border-radius: 18px;
            min-height: 0;
            padding: 0;
        }
        QPushButton#PlayButton:hover { background: ${accent_hover}; }
        QPushButton#PlayButton:focus { border: 2px solid ${text}; }
        QPushButton#PlayButton:pressed { background: ${accent}; }
        QPushButton#PlayButton:disabled { background: ${tint}; border: 1px solid ${line}; }
        QLineEdit, QComboBox, QSpinBox {
            background: ${surface}; border: 1px solid ${control}; border-radius: 7px;
            min-height: 29px; padding: 3px 8px; selection-background-color: ${accent};
        }
        QComboBox { padding-right: 30px; }
        QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right;
            border: none; width: 24px; }
        QComboBox::down-arrow { image: url("${transcript_arrow}"); width: 12px; height: 12px; }
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
        QCheckBox::indicator { width: 16px; height: 16px; border: 1.5px solid ${control};
            border-radius: 4px; background: ${surface}; }
        QCheckBox::indicator:checked { image: url("${check}"); background: ${accent};
            border-color: ${accent}; }
        QCheckBox::indicator:focus { border: 2px solid ${focus_ring}; }
        QCheckBox::indicator:disabled { border-color: ${disabled}; background: ${subtle}; }
        QCheckBox::indicator:checked:disabled { background: ${disabled}; }
        QCheckBox:disabled { color: ${disabled}; }
        QFrame#Player QComboBox { padding-right: 2px; }
        QFrame#Approval QComboBox { padding: 0 2px 0 6px; min-height: 30px; }
        QFrame#Approval QSpinBox { padding: 0 2px; min-height: 30px; }
        QFrame#Approval QPushButton { padding: 2px 8px; }
        QFrame#Approval QToolButton#Disclosure { padding: 1px 2px; }
        QFrame#StudioOptions, QFrame#LibraryInspector { border-top: 1px solid ${line}; }
        QFrame#StudioPreparation, QWidget#OcrProgress, QFrame#OcrGate {
            background: ${tint}; border: 1px solid ${line}; border-radius: 8px; }
        QLabel#OcrWarning, QFrame#OcrWarning { background: ${warning_soft}; color: ${warning};
            border: 1px solid ${warning_line}; border-radius: 8px; padding: 5px 8px; }
        QLabel#PreviewBadge { background: ${warning_soft}; color: ${warning};
            border-radius: 6px; padding: 2px 6px; }
        QLabel[role="pageHeading"] { font-size: 22px; font-weight: 700; }
        QTabWidget#StudioTextTabs QTabBar::tab { min-height: 22px; padding: 2px 10px;
            border-radius: 8px; }
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
    ).substitute({**c, "transcript_arrow": transcript_arrow.as_posix(), "check": check.as_posix()})
