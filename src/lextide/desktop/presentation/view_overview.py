"""The pre-Carbon Overview layout, scoped to a standalone, presentation-only page."""

from __future__ import annotations

from string import Template

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..localized import QLabel, QPushButton, QTableWidget
from ..theme import DARK, LIGHT, brand_symbol, icon, status_dot

# Only colors used by the G10.1.1 Overview. The rest of the app keeps its Carbon theme.
_COLORS = {
    "surface": ("#FFFFFF", "#182230"),
    "subtle": ("#F8FAFC", "#1D2939"),
    "tint": ("#F4F8FF", "#1B2B42"),
    "text": ("#192334", "#EDF3FA"),
    "muted": ("#5E6C80", "#A6B4C7"),
    "line": ("#E1E7EF", "#324257"),
    "control": ("#8492A6", "#778BA5"),
    "accent": ("#1762D8", "#8DBAFF"),
    "accent_hover": ("#1154C3", "#A4C8FF"),
    "on_accent": ("#FFFFFF", "#11223B"),
    "accent_soft": ("#EAF2FF", "#223B5F"),
    "hover": ("#EFF3F9", "#233249"),
}

_STYLE = Template("""
    QWidget { color: ${text}; font-size: 13px; }
    QWidget#OverviewContent, QScrollArea { background: ${surface}; }
    QFrame#Hero { background: ${tint}; border: 1px solid ${line}; border-radius: 17px; }
    QFrame#Card { background: ${surface}; border: 1px solid ${line}; border-radius: 13px; }
    QLabel { background: transparent; border: none; }
    QLabel#OverviewBrand { border: 1px solid ${line}; border-radius: 71px; }
    QLabel[role="title"] { font-size: 28px; font-weight: 700; }
    QLabel[role="heading"] { font-size: 15px; font-weight: 600; }
    QLabel[role="eyebrow"] { color: ${accent}; font-size: 10px; font-weight: 700; }
    QLabel[role="muted"] { color: ${muted}; font-size: 12px; }
    QPushButton {
        background: ${surface}; border: 1px solid ${control}; border-radius: 8px;
        min-height: 30px; padding: 3px 11px; font-weight: 500;
    }
    QPushButton:hover { background: ${hover}; }
    QPushButton:pressed { background: ${accent_soft}; }
    QPushButton[primary="true"] {
        background: ${accent}; color: ${on_accent}; border-color: ${accent}; font-weight: 600;
    }
    QPushButton[primary="true"]:hover { background: ${accent_hover}; }
    QPushButton[quiet="true"] { background: transparent; border-color: transparent; }
    QPushButton[quiet="true"]:hover { background: ${hover}; }
    QTableWidget {
        background: ${surface}; border: 1px solid ${line}; border-radius: 10px; outline: 0;
    }
    QTableWidget::item { padding: 8px; border-bottom: 1px solid ${line}; }
    QTableWidget::item:selected { background: ${accent_soft}; color: ${text}; }
    QTableWidget::item:hover { background: ${hover}; }
    QHeaderView::section {
        background: ${subtle}; color: ${muted}; border: none; padding: 10px 8px; font-size: 11px;
    }
    QPushButton:focus, QTableWidget:focus { border: 2px solid ${accent}; }
""")


def _label(source: str, role: str, *, wrap: bool = False) -> QLabel:
    label = QLabel(tr(source))
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


def _card(name: str, margin: int) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(12)
    return frame, layout


class OverviewView(QScrollArea):
    """The host binds the buttons, recent table and summary to its existing actions/data."""

    def __init__(self, *, dark: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dark = bool(dark)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("OverviewContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(16)
        self.setWidget(content)

        layout.addWidget(_label("Your listening workspace", "title"))
        layout.addWidget(
            _label(
                "Turn written content into natural speech, synchronized subtitles, "
                "and focused listening.",
                "muted",
                wrap=True,
            )
        )
        hero, hero_layout = _card("Hero", 25)
        hero_row = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(10)
        copy.addWidget(_label("WELCOME TO LEXTIDE", "eyebrow"))
        copy.addWidget(_label("Make every word\nworth listening to.", "title"))
        copy.addWidget(
            _label("A quiet space to turn your reading into natural audio.", "muted", wrap=True)
        )
        self.overview_new_button = QPushButton(tr("Create a session"))
        self.overview_new_button.setProperty("primary", True)
        self.overview_library_button = QPushButton(tr("Open library"))
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.overview_new_button)
        actions.addWidget(self.overview_library_button)
        actions.addStretch()
        copy.addLayout(actions)
        hero_row.addLayout(copy, 1)
        mark = QLabel()
        mark.setObjectName("OverviewBrand")
        mark.setPixmap(brand_symbol(116))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(142, 142)
        hero_row.addWidget(mark)
        hero_layout.addLayout(hero_row)
        layout.addWidget(hero)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        start, start_layout = _card("Card", 18)
        start_layout.addWidget(_label("START WITH YOUR WORDS", "eyebrow"))
        start_layout.addWidget(_label("Import a document", "heading"))
        start_layout.addWidget(
            _label(
                "TXT, Markdown, DOCX, text-based PDF or a PNG/JPEG image.\n"
                "Text is extracted locally for your review.",
                "muted",
                wrap=True,
            )
        )
        self.overview_import_button = QPushButton(tr("Choose a file"))
        start_layout.addWidget(self.overview_import_button, alignment=Qt.AlignmentFlag.AlignLeft)
        cards.addWidget(start, 1)
        summary, summary_layout = _card("Card", 18)
        summary_layout.addWidget(_label("ON THIS DEVICE", "eyebrow"))
        summary_layout.addWidget(_label("Your local sessions", "heading"))
        self.overview_summary_label = _label(
            "No sessions loaded. Create a session or open an existing output folder in Library.",
            "muted",
            wrap=True,
        )
        summary_layout.addWidget(self.overview_summary_label)
        summary_layout.addStretch()
        cards.addWidget(summary, 1)
        layout.addLayout(cards)

        recent_header = QHBoxLayout()
        recent_header.addWidget(_label("Recent sessions", "heading"))
        recent_header.addStretch()
        self.view_library_button = QPushButton(tr("View library  →"))
        self.view_library_button.setProperty("quiet", True)
        recent_header.addWidget(self.view_library_button)
        layout.addLayout(recent_header)
        self.recent_table = QTableWidget(0, 3)
        self.recent_table.setHorizontalHeaderLabels([tr("Session"), tr("Status"), tr("Engine")])
        self.recent_table.verticalHeader().hide()
        self.recent_table.verticalHeader().setDefaultSectionSize(51)
        self.recent_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.recent_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.recent_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.recent_table.setShowGrid(False)
        self.recent_table.setWordWrap(False)
        header = self.recent_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.recent_table)
        self.recent_empty_label = _label(
            "Your first session will appear here after it has been saved.", "muted"
        )
        layout.addWidget(self.recent_empty_label)
        layout.addStretch()

        sequence = (
            self.overview_new_button,
            self.overview_library_button,
            self.overview_import_button,
            self.view_library_button,
            self.recent_table,
        )
        for previous, following in zip(sequence, sequence[1:]):
            QWidget.setTabOrder(previous, following)
        for button in sequence[:-1]:
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        for button in (self.overview_new_button, self.overview_import_button):
            button.setIconSize(QSize(17, 17))

        model = self.recent_table.model()
        model.rowsInserted.connect(self._fit_recent_rows)
        model.rowsRemoved.connect(self._fit_recent_rows)
        model.modelReset.connect(self._fit_recent_rows)
        self.set_dark(dark)

    def set_dark(self, dark: bool) -> None:
        """Apply the historic Overview colors to this subtree only."""
        self._dark = bool(dark)
        colors = {key: pair[int(self._dark)] for key, pair in _COLORS.items()}
        self.setStyleSheet(_STYLE.substitute(colors))
        # Align QPalette.Base with theme.status_dot's DARK["surface"] check while
        # keeping the historic #182230 / #FFFFFF QSS background on Overview only.
        table_palette = self.recent_table.palette()
        table_palette.setColor(
            QPalette.ColorRole.Base, QColor(DARK["surface"] if self._dark else LIGHT["surface"])
        )
        self.recent_table.setPalette(table_palette)
        for row_index in range(self.recent_table.rowCount()):
            status_item = self.recent_table.item(row_index, 1)
            if status_item is None or status_item.icon().isNull():
                continue
            record = status_item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(record, dict) and self.recent_table.item(row_index, 0) is not None:
                record = self.recent_table.item(row_index, 0).data(Qt.ItemDataRole.UserRole)
            if isinstance(record, dict) and "status" in record:
                status_item.setIcon(status_dot(str(record.get("status", "")), table_palette))
        self.overview_new_button.setIcon(icon("plus", colors["on_accent"]))
        self.overview_import_button.setIcon(icon("upload", colors["muted"]))
        self._fit_recent_rows()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._fit_recent_rows()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in {QEvent.Type.FontChange, QEvent.Type.LanguageChange}:
            self._fit_recent_rows()

    def _fit_recent_rows(self) -> None:
        table = self.recent_table
        count = table.rowCount()
        height = table.horizontalHeader().sizeHint().height() + table.frameWidth() * 2
        height += count * table.verticalHeader().defaultSectionSize()
        table.setFixedHeight(min(height, 207))
        table.setVisible(bool(count))
        self.recent_empty_label.setVisible(not count)
