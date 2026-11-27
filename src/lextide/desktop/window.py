"""Qt Widgets presentation for Lextide, without service or playback side effects."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QCloseEvent, QFont, QFontDatabase, QIcon, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .bilingual_ui import CHINESE_ROLE, TranscriptDelegate
from .i18n import current_language, tr
from .localized import (
    QAction,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
)
from .presentation.view_overview import OverviewView
from .theme import ASSETS, apply_theme, brand_symbol, icon, resolved_theme, status_dot


def label(text: str, role: str = "", *, wrap: bool = False) -> QLabel:
    widget = QLabel(tr(text))
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


class ElidedPlayerLabel(QLabel):
    """Keep the full localized label accessible while painting one fitted line."""

    def paintEvent(self, event):
        if self.fontMetrics().horizontalAdvance(self.text()) <= self.contentsRect().width():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, self.alignment(), text)


class OptionalStatusLabel(QLabel):
    """Actionable status occupies space only while there is something to show."""

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))

    def clear(self):
        self.setText("")


def button(text: str, *, primary: bool = False, quiet: bool = False) -> QPushButton:
    widget = QPushButton(tr(text))
    widget.setProperty("primary", primary)
    widget.setProperty("quiet", quiet)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def box(name: str = "Card", margins: int = 18) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(12)
    return frame, layout


def row(*widgets: QWidget, stretch: int | None = None) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(8)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget, 1 if index == stretch else 0)
    return layout


def disclosure(text: str, content: QWidget) -> QToolButton:
    """A keyboard-accessible heading that keeps secondary content out of the way."""
    control = QToolButton()
    control.setText(tr(text))
    control.setObjectName("Disclosure")
    control.setCheckable(True)
    control.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    control.setArrowType(Qt.ArrowType.RightArrow)
    content.hide()

    def toggle(expanded: bool) -> None:
        content.setVisible(expanded)
        control.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    control.toggled.connect(toggle)
    return control


def table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels([tr(header) for header in headers])
    widget.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    widget.verticalHeader().hide()
    widget.verticalHeader().setDefaultSectionSize(51)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setShowGrid(False)
    widget.setAlternatingRowColors(False)
    widget.setWordWrap(False)
    widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for col in range(1, len(headers)):
        widget.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
    widget.setMinimumHeight(155)
    return widget


class MainWindow(QMainWindow):
    """The controller owns all data and actions; controls are explicit attributes."""

    closing = Signal()
    page_changed = Signal(str)
    PAGES = ("Overview", "Studio", "Library", "Listen", "Tasks", "Settings")

    def __init__(self) -> None:
        super().__init__()
        self.close_allowed = False
        self._theme_preference = "system"
        self._playing = False
        self._icons: list[tuple[QPushButton, str]] = []
        self._history: list[dict[str, Any]] = []
        self._cues: list[Any] = []
        self.setWindowTitle(tr("Lextide — A Text-to-Speech and Listening Studio"))
        self.setWindowIcon(QIcon(str(ASSETS / "brand" / "lextide-app-icon-1024.png")))
        self.resize(1180, 800)
        self.setMinimumSize(980, 700)
        root = QWidget()
        main = QHBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self.setCentralWidget(root)
        self.sidebar = self._sidebar()
        main.addWidget(self.sidebar)
        content = QWidget()
        content.setObjectName("MainContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        main.addWidget(content, 1)
        self.toolbar = self._toolbar()
        content_layout.addWidget(self.toolbar)
        self.page_stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        for name in self.PAGES:
            page = getattr(self, f"_{name.lower()}_page")()
            self.pages[name] = page
            self.page_stack.addWidget(page)
        content_layout.addWidget(self.page_stack, 1)
        self.player_bar = self._player()
        content_layout.addWidget(self.player_bar)
        self.set_player_loaded(False)
        self.status_label = OptionalStatusLabel()
        self.status_label.setProperty("role", "micro")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setText("")
        self.status_label.setContentsMargins(16, 3, 16, 3)
        content_layout.addWidget(self.status_label)
        self._shortcuts()
        for sequence in (
            (
                self.clean_mode_combo,
                self.clean_button,
                self.discard_preview_button,
                self.review_toggle,
                self.paste_source_button,
                self.source_edit,
                self.speech_edit,
                self.engine_combo,
                self.gender_combo,
                self.voice_combo,
                self.rate_spin,
                self.generate_button,
            ),
            (
                self.focus_check,
                self.follow_check,
                self.subtitle_mode_combo,
                self.font_combo,
                self.transcript_list,
                self.session_details_toggle,
            ),
        ):
            for previous, following in zip(sequence, sequence[1:]):
                QWidget.setTabOrder(previous, following)
        self.library_search.textChanged.connect(self._filter_history)
        self.library_table.itemSelectionChanged.connect(self._library_selection)
        self.theme_combo.currentIndexChanged.connect(
            lambda: self.set_theme(self.theme_combo.currentData())
        )
        self.focus_check.toggled.connect(self._set_focus_mode)
        self.font_combo.currentIndexChanged.connect(
            lambda: self._set_reader_font(self.font_combo.currentData())
        )
        self.transcript_list.verticalScrollBar().sliderPressed.connect(
            lambda: self.follow_check.setChecked(False)
        )
        self.engine_combo.currentIndexChanged.connect(self._engine_ui)
        self.speech_edit.textChanged.connect(self._update_character_count)
        QApplication.instance().styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        self.set_theme("system")
        self.set_history([])
        self.show_page("Overview")
        self._engine_ui()

    def _add_icon(self, control: QPushButton, name: str) -> QPushButton:
        self._icons.append((control, name))
        control.setIconSize(QSize(17, 17))
        return control

    def _sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(216)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(15, 26, 15, 19)
        layout.setSpacing(6)
        brand = QHBoxLayout()
        brand.setSpacing(11)
        mark = QLabel()
        mark.setObjectName("SidebarBrand")
        mark.setPixmap(brand_symbol(36))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(36, 36)
        brand.addWidget(mark, alignment=Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(1)
        words.addWidget(label("Lextide", "brand"))
        words.addWidget(label("LISTENING STUDIO", "micro"))
        brand.addLayout(words)
        brand.addStretch()
        layout.addLayout(brand)
        layout.addSpacing(29)
        layout.addWidget(label("WORKSPACE", "micro"))
        self.nav_buttons = {}
        self.nav_group = QButtonGroup(sidebar)
        self.nav_group.setExclusive(True)
        icons = ("home", "file-text", "library", "headphones", "list-check", "settings")
        for name, resource in zip(self.PAGES, icons):
            if name == "Settings":
                layout.addSpacing(21)
                layout.addWidget(label("PREFERENCES", "micro"))
            control = self._add_icon(button(name), resource)
            control.setObjectName("NavButton")
            control.setCheckable(True)
            control.clicked.connect(lambda checked=False, page=name: self.show_page(page))
            self.nav_group.addButton(control)
            self.nav_buttons[name] = control
            layout.addWidget(control)
        layout.addStretch()
        return sidebar

    def _toolbar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Toolbar")
        frame.setFixedHeight(48)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(22, 4, 22, 4)
        layout.setSpacing(10)
        layout.addWidget(label("Workspace   /", "muted"))
        self.breadcrumb_label = label("Overview")
        layout.addWidget(self.breadcrumb_label)
        layout.addStretch()
        self.import_button = self._add_icon(button("Import file"), "upload")
        self.import_button.setToolTip(tr("Import a document or image (⌘O)."))
        self.new_project_button = self._add_icon(button("New session"), "plus")
        layout.addWidget(self.import_button)
        layout.addWidget(self.new_project_button)
        return frame

    def _page(
        self, title: str, subtitle: str, *, scroll: bool = True
    ) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("Page")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        container = QWidget()
        container.setObjectName("Page")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(16)
        if title:
            layout.addWidget(label(title, "title"))
        if subtitle:
            layout.addWidget(label(subtitle, "muted", wrap=True))
        if scroll:
            scroller = QScrollArea()
            scroller.setWidgetResizable(True)
            scroller.setFrameShape(QFrame.Shape.NoFrame)
            scroller.setWidget(container)
            outer.addWidget(scroller)
        else:
            outer.addWidget(container)
        return page, layout

    def _overview_page(self) -> QWidget:
        view = OverviewView(dark=resolved_theme(self._theme_preference) == "dark", parent=self)
        self.overview_view = view
        self.overview_new_button = view.overview_new_button
        self.overview_library_button = view.overview_library_button
        self.overview_import_button = view.overview_import_button
        self.view_library_button = view.view_library_button
        self.overview_summary_label = view.overview_summary_label
        self.recent_table = view.recent_table
        self.recent_empty_label = view.recent_empty_label
        self.overview_library_button.clicked.connect(lambda: self.show_page("Library"))
        self.view_library_button.clicked.connect(lambda: self.show_page("Library"))
        return view

    def _studio_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroller = QScrollArea()
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setWidgetResizable(True)
        container = QWidget()
        container.setObjectName("Page")
        layout = QVBoxLayout(container)
        layout.setSpacing(8)
        layout.setContentsMargins(24, 12, 24, 12)
        scroller.setWidget(container)
        outer.addWidget(scroller, 1)
        self.studio_header = QHBoxLayout()
        self.studio_header.addWidget(label("Studio", "pageHeading"))
        self.studio_intro = label(
            "Import or paste study material to prepare your reading text.", "muted"
        )
        self.studio_header.addWidget(self.studio_intro)
        self.studio_header.addStretch()
        layout.addLayout(self.studio_header)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(tr("Session title (optional)"))
        self.title_edit.setAccessibleName(tr("Session title"))
        self.paste_source_button = self._add_icon(button("Paste"), "clipboard")
        self.paste_source_button.setToolTip(tr("Paste text or a screenshot (⌘V)."))
        self.encoding_combo = QComboBox()
        self.encoding_combo.addItem(tr("Auto encoding"), None)
        for encoding in (
            "utf-8",
            "utf-8-sig",
            "utf-16",
            "utf-16-le",
            "utf-16-be",
            "gb18030",
            "gbk",
            "big5",
            "cp1252",
            "iso8859-1",
        ):
            self.encoding_combo.addItem(encoding, encoding)
        self.encoding_combo.setToolTip(
            tr("TXT / Markdown encoding. Auto never silently discards undecodable characters.")
        )
        self.ocr_progress = QWidget()
        self.ocr_progress.setObjectName("OcrProgress")
        self.ocr_progress.setFixedHeight(36)
        progress_layout = QHBoxLayout(self.ocr_progress)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.addWidget(label("Recognizing image text locally…", "muted"))
        busy = QProgressBar()
        busy.setRange(0, 0)
        busy.setAccessibleName(tr("Image recognition in progress"))
        progress_layout.addWidget(busy, 1)
        self.cancel_ocr_button = button("Cancel recognition")
        progress_layout.addWidget(self.cancel_ocr_button)
        self.ocr_progress.hide()
        layout.addWidget(self.ocr_progress)
        self.source_path_label = label(
            "No source yet · Import a file or paste your original text.", "muted", wrap=True
        )
        self.source_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.clean_mode_combo = QComboBox()
        self.clean_mode_combo.addItem(tr("Keep Original"), "original")
        self.clean_mode_combo.addItem(tr("English Only"), "english")
        self.clean_mode_combo.setAccessibleName(tr("Text preparation mode"))
        self.clean_button = button("Apply to reading text")
        self.discard_preview_button = button("Discard", quiet=True)
        self.discard_preview_button.setToolTip(tr("Discard preview"))
        self.discard_preview_button.hide()
        self.clean_mode_combo.setToolTip(
            tr(
                "English Only prepares a local preview immediately. "
                "Review retained uncertain passages before generating."
            )
        )
        self.review_content = QWidget()
        review_body = QVBoxLayout(self.review_content)
        review_body.setContentsMargins(0, 0, 0, 0)
        self.clean_summary_label = label("", "muted", wrap=True)
        review_body.addWidget(self.clean_summary_label)
        self.changes_edit = QPlainTextEdit()
        self.changes_edit.setObjectName("ChangesText")
        self.changes_edit.setReadOnly(True)
        self.changes_edit.setPlaceholderText(tr("No changes to review yet."))
        self.changes_edit.setAccessibleName(tr("Text change record"))
        self.changes_edit.setFixedHeight(100)
        review_body.addWidget(self.changes_edit)
        self.undo_button = button("Undo", quiet=True)
        self.undo_button.setToolTip(tr("Undo the last speech-text edit"))
        self.reset_button = button("Reset", quiet=True)
        self.reset_button.setToolTip(tr("Reset speech text to the original source"))
        self.review_toggle = disclosure("Review changes", self.review_content)
        preparation = QFrame()
        preparation.setObjectName("StudioPreparation")
        actions = QHBoxLayout(preparation)
        actions.setContentsMargins(6, 0, 6, 0)
        actions.setSpacing(4)
        actions.addWidget(self.clean_mode_combo)
        actions.addWidget(self.clean_button)
        actions.addWidget(self.discard_preview_button)
        actions.addStretch()
        actions.addWidget(self.undo_button)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.review_toggle)
        layout.addWidget(preparation)
        layout.addWidget(self.review_content)
        self.studio_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.studio_splitter.setChildrenCollapsible(False)
        original, original_layout = box("TextPanel", 12)
        original_layout.addLayout(
            row(label("Original text", "heading"), self.paste_source_button, stretch=0)
        )
        self.source_edit = QPlainTextEdit()
        self.source_edit.setObjectName("OriginalText")
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText(
            tr("Paste text or a screenshot (⌘V), or import a file (⌘O).")
        )
        self.source_edit.setMinimumHeight(110)
        self.source_edit.setAccessibleName(tr("Original source text, read only"))
        original_layout.addWidget(self.source_edit)
        self.studio_splitter.addWidget(original)
        prepared, prepared_layout = box("TextPanel", 12)
        self.reading_heading = label("Reading text", "heading")
        self.text_tabs = QTabWidget()
        self.text_tabs.setObjectName("StudioTextTabs")
        # Cocoa allows tabs to shrink to "Speech…" even when the adjacent
        # source pane has space. Let the splitter account for both full titles.
        self.text_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.text_tabs.setUsesScrollButtons(False)
        self.preview_badge = label("Preview — not yet applied", "micro")
        self.preview_badge.setObjectName("PreviewBadge")
        self.preview_badge.setToolTip(tr("Preview — not yet applied"))
        self.preview_badge.hide()
        reading_header = QWidget()
        heading_layout = QHBoxLayout(reading_header)
        heading_layout.setContentsMargins(0, 0, 6, 0)
        heading_layout.setSpacing(4)
        heading_layout.addWidget(self.reading_heading)
        heading_layout.addWidget(self.preview_badge)
        self.text_tabs.setCornerWidget(reading_header, Qt.Corner.TopLeftCorner)
        self.speech_edit = QPlainTextEdit()
        self.speech_edit.setPlaceholderText(
            tr("Import or paste text, then edit the speech draft here.")
        )
        self.speech_edit.setAccessibleName(tr("Editable speech text"))
        self.subtitle_edit = QPlainTextEdit()
        self.subtitle_edit.setReadOnly(True)
        self.subtitle_edit.setPlaceholderText(
            tr("Sentence subtitles follow the reviewed speech draft.")
        )
        self.subtitle_edit.setAccessibleName(tr("Subtitle text, read only"))
        self.text_tabs.addTab(self.speech_edit, tr("Speech text"))
        self.text_tabs.addTab(self.subtitle_edit, tr("Subtitles"))
        prepared_layout.addWidget(self.text_tabs, 1)
        self.character_count_label = label(tr("{count:,} characters", count=0), "micro", wrap=True)
        prepared_layout.addWidget(self.character_count_label)
        self.studio_splitter.addWidget(prepared)
        self.studio_splitter.setStretchFactor(0, 2)
        self.studio_splitter.setStretchFactor(1, 3)
        self.studio_splitter.setSizes([300, 450])
        self.studio_splitter.setMinimumHeight(190)
        layout.addWidget(self.studio_splitter, 1)

        self.studio_options = QFrame()
        self.studio_options.setObjectName("StudioOptions")
        options = QGridLayout(self.studio_options)
        options.setContentsMargins(26, 6, 26, 8)
        options.setHorizontalSpacing(6)
        options.setVerticalSpacing(4)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem(tr("Edge online"), "edge-tts")
        self.engine_combo.addItem(tr("Kokoro offline"), "kokoro")
        self.engine_combo.addItem(tr("Auto"), "auto")
        self.gender_combo = QComboBox()
        self.gender_combo.addItem(tr("US male"), "male")
        self.gender_combo.addItem(tr("US female"), "female")
        self.voice_combo = QComboBox()
        self.rate_spin = QSpinBox()
        self.rate_spin.setFixedWidth(82)
        self.rate_spin.setRange(-50, 100)
        self.rate_spin.setValue(-4)
        self.rate_spin.setSuffix(" %")
        self.pause_spin = QSpinBox()
        self.pause_spin.setRange(0, 2000)
        self.pause_spin.setValue(0)
        self.pause_spin.setSuffix(" ms")
        for caption, field in (
            ("Speech engine", self.engine_combo),
            ("American voice", self.gender_combo),
            ("Voice", self.voice_combo),
            ("Speaking rate", self.rate_spin),
            ("Sentence pause", self.pause_spin),
        ):
            field.setAccessibleName(tr(caption))
            field.setToolTip(tr(caption))
        self.engine_hint_label = label("", "micro", wrap=True)
        self.engine_decision_status = label("", "muted", wrap=True)
        self.engine_settings_button = self._add_icon(button("Engine settings"), "settings")
        self.engine_settings_button.clicked.connect(self._show_engine_settings)
        self.filter_check = QCheckBox(tr("Kokoro v3 audio filter"))
        self.filter_check.setChecked(True)
        self.filter_check.setToolTip(
            tr("Uses the verified kokoro-hf-v3 filter; available only for Kokoro.")
        )
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText(tr("Output folder"))
        self.output_dir_edit.setAccessibleName(tr("Session output directory"))
        self.output_dir_button = self._add_icon(button("Choose…"), "folder")
        options.addWidget(self.title_edit, 0, 0, 1, 2)
        options.addWidget(self.encoding_combo, 0, 2)
        options.addWidget(label("Sentence pause", "muted"), 0, 3)
        options.addWidget(self.pause_spin, 0, 4)
        options.addWidget(self.filter_check, 0, 5)
        options.addWidget(self.output_dir_edit, 1, 0, 1, 3)
        options.addWidget(self.output_dir_button, 1, 3)
        options.addWidget(self.engine_settings_button, 1, 4, 1, 2)
        options.addWidget(self.source_path_label, 2, 0, 1, 3)
        options.addWidget(self.engine_decision_status, 2, 3, 1, 3)
        options.addWidget(self.engine_hint_label, 3, 0, 1, 6)
        self.more_options_toggle = disclosure("More options", self.studio_options)
        outer.addWidget(self.studio_options)

        footer = QFrame()
        footer.setObjectName("Approval")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(26, 10, 26, 12)
        footer_layout.setSpacing(4)
        self.studio_status_label = OptionalStatusLabel()
        self.studio_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.studio_status_label.setProperty("role", "error")
        self.studio_status_label.setWordWrap(True)
        self.studio_status_label.clear()
        self.online_notice = label("", "muted", wrap=True)
        self.generation_summary = label("", "muted")
        self.generation_summary.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        status_row = row(self.studio_status_label, self.online_notice, self.generation_summary)
        status_row.setStretchFactor(self.generation_summary, 1)
        footer_layout.addLayout(status_row)
        self.approval_check = QCheckBox(tr("I approve this reviewed text"))
        self.approval_check.setEnabled(False)
        self.preview_button = button("Preview && review")
        # Compatibility controls remain available for older callers, while the
        # controller owns the single Generate preparation/consent flow.
        self.approval_check.setParent(footer)
        self.preview_button.setParent(footer)
        self.approval_check.hide()
        self.preview_button.hide()
        self.generate_button = button("Generate Audio", primary=True)
        self.generate_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.generate_button.setEnabled(False)
        self.cancel_generation_button = button("Cancel Preparation")
        self.cancel_generation_button.hide()
        generate_actions = QHBoxLayout()
        generate_actions.setSpacing(4)
        for control in (
            self.engine_combo,
            self.gender_combo,
            self.voice_combo,
            self.rate_spin,
            self.more_options_toggle,
        ):
            generate_actions.addWidget(control)
        generate_actions.addStretch()
        generate_actions.addWidget(self.generate_button)
        # Cancellation is transient; placing it beside the status keeps all voice
        # controls usable even in the minimum supported window.
        status_row.insertWidget(2, self.cancel_generation_button)
        footer_layout.addLayout(generate_actions)
        outer.addWidget(footer)
        return page

    def _library_page(self) -> QWidget:
        page, layout = self._page("", "", scroll=False)
        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText(tr("Search title, engine or job ID"))
        self.library_search.setMaximumWidth(260)
        self.library_search.setClearButtonEnabled(True)
        self.library_search.setAccessibleName(tr("Search local library"))
        self.library_choose_folder_button = self._add_icon(
            button("Open existing folder…"), "folder"
        )
        self.library_refresh_button = self._add_icon(button("Refresh"), "refresh")
        filters = QHBoxLayout()
        filters.addWidget(label("Library", "title"))
        filters.addWidget(self.library_search, 1)
        filters.addStretch()
        filters.addWidget(self.library_choose_folder_button)
        filters.addWidget(self.library_refresh_button)
        layout.addLayout(filters)
        self.library_root_label = label(
            "Choose an output folder to discover existing job manifests.", "muted", wrap=True
        )
        self.library_root_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.library_table = table(["Session", "Status", "Engine", "Duration", "Created"])
        for col, width in enumerate((120, 130, 90, 140), 1):
            self.library_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Fixed
            )
            self.library_table.setColumnWidth(col, width)
        self.library_table.setColumnHidden(4, self.width() < 1040)
        layout.addWidget(self.library_table, 1)
        self.library_empty_label = label(
            "Your library is empty. Create a session or open a folder that contains Lextide jobs.",
            "muted",
            wrap=True,
        )
        layout.addWidget(self.library_empty_label)
        self.library_load_button = self._add_icon(
            button("Verify && open in Listen", primary=True), "headphones"
        )
        self.library_open_button = self._add_icon(button("Show files"), "folder")
        self.library_load_button.setEnabled(False)
        self.library_load_button.setToolTip(
            tr(
                "Select a session in the table to verify and open it in Listen; "
                "you can also double-click a session."
            )
        )
        self.library_open_button.setEnabled(False)
        self.library_script_button = button("Load script in Studio")
        self.library_script_button.setEnabled(False)
        inspector = QFrame()
        inspector.setObjectName("LibraryInspector")
        inspector.setFixedHeight(58)
        actions = QHBoxLayout(inspector)
        actions.setContentsMargins(6, 4, 6, 4)
        self.library_root_label.setMinimumWidth(0)
        self.library_root_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        actions.addWidget(self.library_root_label, 1)
        for control in (
            self.library_open_button,
            self.library_script_button,
            self.library_load_button,
        ):
            actions.addWidget(control)
        layout.addWidget(inspector)
        return page

    def _listen_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 12, 20, 8)
        layout.setSpacing(6)
        self.listen_header = QWidget()
        self.listen_header.setFixedHeight(32)
        self.listen_header_row = QHBoxLayout(self.listen_header)
        self.listen_header_row.setContentsMargins(0, 0, 0, 0)
        self.listen_header_row.setSpacing(10)
        title = label("Listen", "title")
        title.setStyleSheet("font-size: 22px;")
        self.listen_subtitle = label("Read synchronized subtitles while listening", "muted")
        self.focus_check = QCheckBox(tr("Focus view"))
        self.listen_header_row.addWidget(title)
        self.listen_header_row.addWidget(self.listen_subtitle)
        self.listen_header_row.addStretch()
        self.listen_header_row.addWidget(self.focus_check)
        layout.addWidget(self.listen_header)
        self.listen_reader, reader_layout = box("ListenReader", margins=0)
        reader_layout.setSpacing(6)
        self.listen_reader.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.subtitle_mode_combo = QComboBox()
        self.subtitle_mode_combo.addItem(tr("English Only"), "english")
        self.subtitle_mode_combo.addItem(tr("English + Chinese"), "bilingual")
        self.subtitle_mode_combo.setAccessibleName(tr("Subtitle display mode"))
        self.subtitle_import_button = button("Import Chinese")
        self.subtitle_edit_button = button("Edit selected Chinese")
        self.subtitle_import_button.setEnabled(False)
        self.subtitle_edit_button.setEnabled(False)
        self.translation_tools = QWidget()
        tools = QHBoxLayout(self.translation_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.addWidget(self.subtitle_import_button)
        tools.addWidget(self.subtitle_edit_button)
        tools.addStretch()
        self.subtitle_status_label = label(
            "Open a verified session to import or edit Chinese subtitles.", "micro", wrap=True
        )
        self.subtitle_status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.font_combo = QComboBox()
        self.font_combo.setObjectName("TranscriptFontSize")
        self.font_combo.setEditable(False)
        for pixels in (12, 14, 16, 18, 20, 22, 24, 28, 32, 36, 40):
            self.font_combo.addItem(f"{pixels} px", pixels)
        self.font_combo.setCurrentIndex(self.font_combo.findData(18))
        self.font_combo.setAccessibleName(tr("Transcript font size"))
        self.follow_check = QCheckBox(tr("Follow current sentence"))
        self.follow_check.setChecked(True)
        self.subtitle_mode_combo.setMinimumWidth(156)
        self.font_combo.setMinimumWidth(86)
        self.reader_controls = QHBoxLayout()
        self.reader_controls.setSpacing(8)
        self.reader_controls.addWidget(self.follow_check)
        self.reader_controls.addSpacing(8)
        self.reader_controls.addStretch()
        self.reader_controls.addWidget(self.subtitle_mode_combo)
        self.reader_controls.addWidget(self.font_combo)
        reader_layout.addLayout(self.reader_controls)
        self.transcript_list = QListWidget()
        self.transcript_list.setItemDelegate(TranscriptDelegate(self.transcript_list))
        self.transcript_list.setObjectName("Transcript")
        self.transcript_list.setWordWrap(True)
        self.transcript_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.transcript_list.setUniformItemSizes(False)
        self.transcript_list.setLayoutMode(QListWidget.LayoutMode.Batched)
        self.transcript_list.setBatchSize(100)
        self.transcript_list.setProperty("subtitle_mode", "english")
        self.transcript_list.setSpacing(2)
        self.transcript_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.transcript_list.setAccessibleName(tr("Sentence transcript; choose a sentence to seek"))
        self.transcript_stack = QStackedWidget()
        self.transcript_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.transcript_stack.setMinimumHeight(120)
        self.transcript_empty = QWidget()
        empty = QVBoxLayout(self.transcript_empty)
        empty.addStretch()
        self.empty_headphones = QLabel()
        self.empty_headphones.setFixedSize(24, 24)
        empty.addWidget(self.empty_headphones, alignment=Qt.AlignmentFlag.AlignHCenter)
        for text, role in (
            ("No session loaded", "heading"),
            ("Open a session from Library", "muted"),
        ):
            hint = label(text, role)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.addWidget(hint)
        self.open_library_button = button("Open Library")
        self.open_library_button.clicked.connect(lambda: self.show_page("Library"))
        empty.addWidget(self.open_library_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        empty.addStretch()
        self.transcript_stack.addWidget(self.transcript_empty)
        self.transcript_stack.addWidget(self.transcript_list)
        reader_layout.addWidget(self.transcript_stack, 1)
        self.transcript_list.setToolTip(tr("Select a sentence to seek to its beginning."))
        layout.addWidget(self.listen_reader, 1)
        self.listen_aside, aside = box(margins=12)
        self.listen_title_label = label("No session loaded", "heading")
        self.listen_title_label.setWordWrap(True)
        aside.addWidget(self.listen_title_label)
        self.listen_detail_label = label(
            "Open a verified session from Library.\n\n"
            "Audio and sentence subtitles stay linked to the same job.",
            "muted",
            wrap=True,
        )
        self.listen_detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        aside.addWidget(self.listen_detail_label)
        self.listen_open_button = self._add_icon(button("Show files"), "folder")
        self.listen_open_button.setEnabled(False)
        aside.addWidget(self.listen_open_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.session_details_toggle = disclosure("Session details", self.listen_aside)
        self.session_details_toggle.setStyleSheet("padding: 4px 8px;")
        reader_layout.addLayout(
            row(
                self.translation_tools,
                self.subtitle_status_label,
                self.session_details_toggle,
                stretch=1,
            )
        )
        layout.addWidget(self.listen_aside)
        self.translation_tools.hide()
        self.subtitle_status_label.hide()
        self._set_reader_font(18)
        return page

    def _tasks_page(self) -> QWidget:
        page, layout = self._page(
            "Tasks", "See what is happening, what is saved, and what to do next."
        )
        self.task_refresh_button = self._add_icon(button("Refresh tasks"), "refresh")
        top = QHBoxLayout()
        top.addWidget(label("Local task history", "heading"))
        top.addStretch()
        top.addWidget(self.task_refresh_button)
        layout.addLayout(top)
        self.tasks_table = table(["Session", "State", "Engine", "Chunks"])
        self.tasks_table.setMaximumHeight(240)
        layout.addWidget(self.tasks_table, 1)
        details, detail_layout = box()
        self.task_stage_label = label("No task selected", "heading", wrap=True)
        detail_layout.addWidget(self.task_stage_label)
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 1)
        self.task_progress.setValue(0)
        self.task_progress.setTextVisible(False)
        self.task_progress.setFixedHeight(7)
        detail_layout.addWidget(self.task_progress)
        self.task_detail_edit = QPlainTextEdit()
        self.task_detail_edit.setReadOnly(True)
        self.task_detail_edit.setPlaceholderText(
            tr("Select a task to see its actual stage, completed chunks and recovery details.")
        )
        self.task_detail_edit.setMinimumHeight(100)
        self.task_diagnostics_toggle = disclosure("Task diagnostics", self.task_detail_edit)
        self.task_pause_button = self._add_icon(button("Pause"), "pause")
        self.task_resume_button = self._add_icon(button("Resume / retry", primary=True), "refresh")
        self.task_cancel_button = self._add_icon(button("Cancel task"), "x")
        for control in (self.task_pause_button, self.task_resume_button, self.task_cancel_button):
            control.setEnabled(False)
        action_row = row(self.task_pause_button, self.task_resume_button, self.task_cancel_button)
        action_row.addStretch()
        detail_layout.addLayout(action_row)
        detail_layout.addWidget(self.task_diagnostics_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        detail_layout.addWidget(self.task_detail_edit, 1)
        layout.addWidget(details, 1)
        layout.addWidget(
            label(
                "Completed chunks are saved. A session becomes ready only after "
                "assembly, verification and delivery succeed.",
                "micro",
                wrap=True,
            )
        )
        return page

    def _settings_page(self) -> QWidget:
        page, layout = self._page(
            "Settings", "A quiet workspace. Clear choices about what stays here."
        )
        layout.parentWidget().setMaximumWidth(872)  # 820 pt form + 26 pt outer margins.
        layout.addWidget(label("Appearance", "heading"))
        appearance, appearance_layout = box()
        self.theme_combo = QComboBox()
        for text, value in (("Follow system", "system"), ("Light", "light"), ("Dark", "dark")):
            self.theme_combo.addItem(tr(text), value)
        appearance_layout.addLayout(row(label("Theme"), self.theme_combo, stretch=0))
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("简体中文", "zh_CN")
        self.language_combo.setCurrentIndex(self.language_combo.findData(current_language()))
        self.language_combo.setAccessibleName(tr("Interface language"))
        appearance_layout.addLayout(row(label("Language"), self.language_combo, stretch=0))
        for combo in (self.theme_combo, self.language_combo):
            combo.setFixedWidth(180)
        self.language_hint = label(
            "Save to apply the language immediately.",
            "muted",
            wrap=True,
        )
        appearance_layout.addWidget(self.language_hint)
        layout.addWidget(appearance)
        self.settings_advanced_content = QWidget()
        advanced_layout = QVBoxLayout(self.settings_advanced_content)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(12)
        advanced_layout.addWidget(label("Media tools · required", "heading"))
        media, media_layout = box()
        media_layout.addWidget(
            label(
                "First use: install FFmpeg separately, then choose the existing ffmpeg and "
                "ffprobe executables below. Leave a field blank to use local detection. "
                "Lextide does not bundle or download these tools.",
                "muted",
                wrap=True,
            )
        )
        self.media_status_label = label("Media tools have not been checked.", "muted", wrap=True)
        self.media_status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        media_layout.addWidget(self.media_status_label)
        media_paths = QGridLayout()
        self.ffmpeg_edit = QLineEdit()
        self.ffprobe_edit = QLineEdit()
        self.ffmpeg_button = self._add_icon(button("Choose…"), "folder")
        self.ffprobe_button = self._add_icon(button("Choose…"), "folder")
        for index, (name, edit, choose) in enumerate(
            (
                ("FFmpeg", self.ffmpeg_edit, self.ffmpeg_button),
                ("ffprobe", self.ffprobe_edit, self.ffprobe_button),
            )
        ):
            edit.setPlaceholderText(
                tr("Optional {name} executable; blank uses local detection", name=name)
            )
            edit.setAccessibleName(tr("{name} executable path", name=name))
            choose.setAccessibleName(tr("Choose {name} executable", name=name))
            media_paths.addWidget(label(name, "muted"), index, 0)
            media_paths.addWidget(edit, index, 1)
            media_paths.addWidget(choose, index, 2)
        media_paths.setColumnStretch(1, 1)
        media_layout.addLayout(media_paths)
        advanced_layout.addWidget(media)
        advanced_layout.addWidget(label("Speech engines", "heading"))
        engines, engines_layout = box()
        engines_layout.addWidget(label("Online · Edge TTS", "heading"))
        engines_layout.addWidget(
            label(
                "Not checked. Each online job asks permission before sending "
                "the exact reviewed text to the speech service.",
                "muted",
                wrap=True,
            )
        )
        engines_layout.addSpacing(4)
        self.model_refresh_button = self._add_icon(button("Check local status"), "refresh")
        engines_layout.addLayout(
            row(label("Local / Offline · Kokoro", "heading"), self.model_refresh_button, stretch=0)
        )
        self.model_status_label = label(
            "Model status has not been checked. No model downloads occur automatically.",
            "muted",
            wrap=True,
        )
        self.model_status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        engines_layout.addWidget(self.model_status_label)
        paths = QGridLayout()
        self.model_dir_edit = QLineEdit()
        self.model_dir_edit.setPlaceholderText(
            tr("Optional model directory; blank uses backend default")
        )
        self.offline_python_edit = QLineEdit()
        self.offline_python_edit.setPlaceholderText(
            tr("Choose the Python 3.13 executable from your existing Kokoro runtime")
        )
        self.model_dir_button = self._add_icon(button("Choose…"), "folder")
        self.model_dir_button.setAccessibleName(tr("Choose existing Kokoro model directory"))
        self.offline_python_button = self._add_icon(button("Choose…"), "folder")
        self.offline_python_button.setAccessibleName(tr("Choose offline Python executable"))
        paths.addWidget(label("Model directory", "muted"), 0, 0)
        paths.addWidget(self.model_dir_edit, 0, 1)
        paths.addWidget(self.model_dir_button, 0, 2)
        paths.addWidget(label("Offline Python", "muted"), 1, 0)
        paths.addWidget(self.offline_python_edit, 1, 1)
        paths.addWidget(self.offline_python_button, 1, 2)
        paths.setColumnStretch(1, 1)
        engines_layout.addLayout(paths)
        self.model_install_label = label(
            "Kokoro is optional. For offline speech, prepare a separate Python 3.13 runtime "
            "with the offline-runtime dependencies and a Kokoro model directory, then select "
            "them here. The app checks existing files only; it does not install models or "
            "fall back to an online engine.",
            "micro",
            wrap=True,
        )
        self.model_install_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        engines_layout.addWidget(self.model_install_label)
        advanced_layout.addWidget(engines)
        self.ocr_diagnostic = label("", "muted", wrap=True)
        self.ocr_diagnostic.hide()
        advanced_layout.addWidget(self.ocr_diagnostic)
        layout.addWidget(label("Privacy & storage", "heading"))
        storage, storage_layout = box()
        self.settings_output_dir_edit = QLineEdit()
        self.settings_output_dir_edit.setPlaceholderText(tr("Default output folder"))
        self.settings_output_dir_button = self._add_icon(button("Choose…"), "folder")
        storage_layout.addWidget(label("Default output folder", "heading"))
        storage_layout.addLayout(
            row(self.settings_output_dir_edit, self.settings_output_dir_button, stretch=0)
        )
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        storage_layout.addWidget(divider)
        storage_layout.addWidget(label("Online text transmission", "heading"))
        storage_layout.addWidget(
            label(
                "Ask before sending. Editing reviewed text invalidates its approval. "
                "Offline errors never fall back to an online engine.",
                "muted",
                wrap=True,
            )
        )
        layout.addWidget(storage)
        self.settings_advanced_toggle = disclosure(
            "Advanced diagnostics", self.settings_advanced_content
        )
        self.runtime_summary = label("Kokoro · Not checked", "muted")
        advanced_header = row(self.settings_advanced_toggle, self.runtime_summary)
        advanced_header.addStretch()
        layout.addLayout(advanced_header)
        layout.addWidget(self.settings_advanced_content)
        layout.addStretch()
        self.settings_footer = QFrame()
        self.settings_footer.setObjectName("SettingsFooter")
        save_layout = QHBoxLayout(self.settings_footer)
        save_layout.setContentsMargins(26, 12, 26, 12)
        self.settings_save_status = label("", "muted", wrap=True)
        self.settings_save_button = button("Save Settings", primary=True)
        self.save_settings = self.settings_save_button
        save_layout.addWidget(self.settings_save_status, 1)
        self.settings_reset_button = button("Reset changes")
        save_layout.addWidget(self.settings_reset_button)
        save_layout.addWidget(self.settings_save_button)
        page.layout().addWidget(self.settings_footer)
        return page

    def _player(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Player")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(10)
        identity = QWidget()
        identity.setMinimumWidth(140)
        identity.setMaximumWidth(200)
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(4)
        self.player_title_label = ElidedPlayerLabel(tr("No audio loaded"))
        self.player_title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.player_title_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.player_detail_label = ElidedPlayerLabel(tr("Open a session in Library"))
        self.player_detail_label.setProperty("role", "micro")
        self.player_detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.player_detail_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        identity_layout.addWidget(self.player_title_label)
        identity_layout.addWidget(self.player_detail_label)
        layout.addWidget(identity)
        self.previous_button = self._add_icon(button("", quiet=True), "skip-back")
        self.previous_button.setToolTip(tr("Previous sentence"))
        self.previous_button.setAccessibleName(tr("Previous sentence"))
        self.play_button = self._add_icon(button(""), "play")
        self.play_button.setObjectName("PlayButton")
        self.play_button.setFixedSize(36, 36)
        self.play_button.setIconSize(QSize(16, 16))
        self.play_button.setToolTip(tr("Play / pause"))
        self.play_button.setAccessibleName(tr("Play or pause audio"))
        self.next_button = self._add_icon(button("", quiet=True), "skip-forward")
        self.next_button.setToolTip(tr("Next sentence"))
        self.next_button.setAccessibleName(tr("Next sentence"))
        for control in (self.previous_button, self.play_button, self.next_button):
            control.setFixedWidth(36)
            control.setEnabled(False)
            layout.addWidget(control)
        self.position_label = label("00:00", "micro")
        self.duration_label = label("00:00", "micro")
        clock_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        clock_font.setPixelSize(12)
        for clock in (self.position_label, self.duration_label):
            clock.setFont(clock_font)
            clock.setStyleSheet("font-size: 12px;")
            clock.setMinimumWidth(clock.fontMetrics().horizontalAdvance("00:00:00"))
            clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.duration_label.setText("--:--")
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("PlaybackSeek")
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setEnabled(False)
        self.seek_slider.setMinimumWidth(80)
        self.seek_slider.setAccessibleName(tr("Playback position"))
        layout.addWidget(self.position_label)
        layout.addWidget(self.seek_slider, 1)
        layout.addWidget(self.duration_label)
        self.playback_rate_combo = QComboBox()
        for speed in (0.75, 1.0, 1.25, 1.5, 2.0):
            self.playback_rate_combo.addItem(f"{speed:g}×", speed)
        self.playback_rate_combo.setCurrentIndex(1)
        self.playback_rate_combo.setFixedWidth(76)
        self.playback_rate_combo.setAccessibleName(tr("Playback speed"))
        layout.addWidget(self.playback_rate_combo)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("PlaybackVolume")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(66)
        self.volume_slider.setToolTip(tr("Volume"))
        self.volume_slider.setAccessibleName(tr("Playback volume"))
        layout.addWidget(self.volume_slider)
        return frame

    def set_player_loaded(self, loaded: bool) -> None:
        height = 64 if loaded else 56
        if self.player_bar.maximumHeight() != height:
            self.player_bar.setFixedHeight(height)
            self.player_bar.layout().setContentsMargins(
                20, 8 if loaded else 9, 20, 8 if loaded else 10
            )
            self.player_detail_label.setVisible(loaded)

    def _shortcuts(self) -> None:
        for shortcut, callback, title in (
            (QKeySequence.StandardKey.Open, self.import_button.click, "Import file"),
            (QKeySequence.StandardKey.New, self.new_project_button.click, "New session"),
        ):
            action = QAction(tr(title), self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            self.addAction(action)

    def show_page(self, name: str) -> None:
        name = next((page for page in self.PAGES if page.lower() == name.lower()), name)
        if name not in self.pages:
            raise ValueError(f"Unknown Lextide page: {name}")
        # Keep a single Cmd+O action and Import button when changing surfaces.
        if name == "Studio":
            self.studio_header.addWidget(self.import_button)
        elif self.import_button.parentWidget() is not self.toolbar:
            self.toolbar.layout().insertWidget(
                self.toolbar.layout().indexOf(self.new_project_button), self.import_button
            )
        self.import_button.show()
        self.page_stack.setCurrentWidget(self.pages[name])
        self.breadcrumb_label.setText(tr(name))
        for page, control in self.nav_buttons.items():
            control.setChecked(page == name)
        if name != "Listen" and self.focus_check.isChecked():
            self.focus_check.setChecked(False)
        self._refresh_icons()
        self.page_changed.emit(name)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact_studio = event.size().width() < 1080
        self.studio_intro.setVisible(not compact_studio)
        self.preview_badge.setText(
            tr("Not yet applied") if compact_studio else tr("Preview — not yet applied")
        )
        self.library_table.setColumnHidden(4, event.size().width() < 1040)

    def set_theme(self, preference: str = "system") -> None:
        self._theme_preference = (
            preference if preference in {"system", "light", "dark"} else "system"
        )
        self._colors = apply_theme(self._theme_preference)
        self.overview_view.set_dark(resolved_theme(self._theme_preference) == "dark")
        # Hidden Qt layouts defer geometry until first shown. Compute the tray's
        # intrinsic size with the actual theme so its initial rows match their controls.
        if self.studio_options.isHidden():
            self.studio_options.layout().invalidate()
            self.studio_options.adjustSize()
        self._refresh_icons()

    def _system_theme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self._theme_preference == "system":
            self.set_theme("system")

    def _refresh_icons(self) -> None:
        colors = getattr(self, "_colors", None)
        if not colors:
            return
        self.empty_headphones.setPixmap(icon("headphones", colors["muted"], 24).pixmap(24, 24))
        for control, resource in self._icons:
            if control is getattr(self, "play_button", None):
                self._refresh_play_icon()
                continue
            color = colors["on_accent"] if control.property("primary") else colors["muted"]
            if control.objectName() == "NavButton" and control.isChecked():
                color = colors["accent"]
            image = icon(resource, color)
            if control.property("primary"):
                disabled = icon(resource, colors["disabled"])
                image.addPixmap(disabled.pixmap(control.iconSize()), QIcon.Mode.Disabled)
            control.setIcon(image)

    def _refresh_play_icon(self) -> None:
        resource = "pause" if self._playing else "play"
        image = icon(resource, self._colors["on_accent"], 16)
        disabled = icon(resource, self._colors["disabled"], 16)
        image.addPixmap(disabled.pixmap(self.play_button.iconSize()), QIcon.Mode.Disabled)
        self.play_button.setIcon(image)

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self._refresh_play_icon()
        self.play_button.setToolTip(tr("Pause") if playing else tr("Play"))
        self.play_button.setAccessibleName(tr("Pause audio") if playing else tr("Play audio"))

    def _set_focus_mode(self, enabled: bool) -> None:
        self.sidebar.setVisible(not enabled)
        self.toolbar.setVisible(not enabled)
        row = self.reader_controls if enabled else self.listen_header_row
        row.addWidget(self.focus_check)
        self.focus_check.show()
        self.listen_header.setVisible(not enabled)
        self.listen_subtitle.setVisible(not enabled)
        self.pages["Listen"].layout().setContentsMargins(
            22 if enabled else 20, 10 if enabled else 12, 22 if enabled else 20, 8
        )
        self.translation_tools.setVisible(not enabled and bool(self._cues))
        self.focus_check.setText(tr("Exit focus view") if enabled else tr("Focus view"))
        self.session_details_toggle.setVisible(not enabled)
        self.listen_aside.setVisible(not enabled and self.session_details_toggle.isChecked())

    def _set_reader_font(self, pixels: int) -> None:
        font = QFont(self.font())
        font.setPixelSize(pixels)
        self.transcript_list.setFont(font)
        # Explicit per-widget QSS wins over the application's body-size rule.
        self.transcript_list.setStyleSheet(f"QListWidget {{ font-size: {pixels}px; }}")
        self.transcript_list.doItemsLayout()

    def _engine_ui(self) -> None:
        offline = self.engine_combo.currentData() == "kokoro"
        self.filter_check.setEnabled(offline)
        self.engine_hint_label.hide()
        self.engine_hint_label.setText(
            tr("Local / Offline · uses installed Kokoro models; no automatic online fallback.")
            if offline
            else tr("Online service · your reviewed text is sent only after explicit permission.")
        )

    def _show_engine_settings(self) -> None:
        self.settings_advanced_toggle.setChecked(True)
        self.show_page("Settings")
        scroller = self.pages["Settings"].findChild(QScrollArea)
        if scroller is not None:
            scroller.widget().layout().activate()
            scroller.verticalScrollBar().setValue(self.settings_advanced_toggle.geometry().top())

    def _update_character_count(self) -> None:
        self.character_count_label.setText(
            tr(
                "{count:,} characters",
                count=len(self.speech_edit.toPlainText()),
            )
        )

    @staticmethod
    def _duration(seconds: Any) -> str:
        if not isinstance(seconds, (float, int)) or seconds < 0:
            return "—"
        total = int(seconds)
        return f"{total // 60:02d}:{total % 60:02d}"

    @staticmethod
    def _fill_table(
        widget: QTableWidget, rows: Sequence[dict[str, Any]], columns: Sequence[str]
    ) -> None:
        selected = widget.item(widget.currentRow(), 0)
        selected_path = selected.data(Qt.ItemDataRole.UserRole).get("path") if selected else None
        widget.setUpdatesEnabled(False)
        sorting = widget.isSortingEnabled()
        widget.setSortingEnabled(False)
        widget.setRowCount(len(rows))
        for row_index, record in enumerate(rows):
            for column, key in enumerate(columns):
                if key == "duration_seconds":
                    value = MainWindow._duration(record.get(key))
                elif key == "title":
                    value = record.get("title") or record.get("job_id") or tr("Untitled session")
                elif key == "status":
                    value = tr(str(record.get(key, "—")))
                elif key == "chunks":
                    value = record.get("chunks")
                    if value is None:
                        value = (
                            f"{record.get('completed_chunks', 0)} / "
                            f"{record.get('total_chunks', '—')}"
                        )
                else:
                    value = record.get(key, "—")
                item = QTableWidgetItem(value if isinstance(value, str) else str(value))
                item.setData(Qt.ItemDataRole.UserRole, dict(record))
                if key == "duration_seconds":
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if key == "status":
                    item.setIcon(status_dot(str(record.get(key, "")), widget.palette()))
                item.setToolTip(str(record.get("path") or record.get("export_path") or value))
                widget.setItem(row_index, column, item)
            if selected_path and record.get("path") == selected_path:
                widget.selectRow(row_index)
        widget.setSortingEnabled(sorting)
        widget.setUpdatesEnabled(True)

    def set_history(self, rows: Sequence[dict[str, Any]]) -> None:
        self._history = [dict(item) for item in rows]
        self._fill_table(self.recent_table, self._history[:4], ("title", "status", "engine"))
        self.recent_empty_label.setVisible(not rows)
        self.recent_table.setVisible(bool(rows))
        self.overview_summary_label.setText(
            tr(
                "Found {count} local tasks/sessions. Files are verified before playback.",
                count=len(rows),
            )
            if rows
            else tr("No local sessions yet. Import text to create your first listening session.")
        )
        self._filter_history()

    def _filter_history(self) -> None:
        query = self.library_search.text().strip().casefold()
        rows = [
            record
            for record in self._history
            if not query
            or query
            in " ".join(
                str(record.get(key, "")) for key in ("title", "job_id", "engine", "path")
            ).casefold()
        ]
        self._fill_table(
            self.library_table,
            rows,
            ("title", "status", "engine", "duration_seconds", "created_at"),
        )
        self.library_empty_label.setVisible(not rows)
        self.library_empty_label.setText(
            tr("No sessions match this search. Clear the search to see all saved sessions.")
            if query
            else tr(
                "Your library is empty. Create a session or open a folder "
                "that contains Lextide jobs."
            )
        )
        self._library_selection()

    def _library_selection(self) -> None:
        selected = self.library_table.currentRow() >= 0 and bool(self.library_table.selectedItems())
        self.library_load_button.setEnabled(selected)
        self.library_open_button.setEnabled(selected)
        self.library_script_button.setEnabled(selected)

    def set_tasks(self, rows: Sequence[dict[str, Any]]) -> None:
        self._fill_table(self.tasks_table, rows, ("title", "status", "engine", "chunks"))

    def set_transcript(self, cues: Sequence[Any]) -> None:
        self._cues = list(cues)
        self.transcript_stack.setCurrentWidget(
            self.transcript_list if self._cues else self.transcript_empty
        )
        self.translation_tools.setVisible(bool(self._cues) and not self.focus_check.isChecked())
        self.subtitle_status_label.setVisible(bool(self._cues))
        self.transcript_list.setProperty("active_cue", -1)
        self.transcript_list.clear()
        for index, cue in enumerate(cues):
            if isinstance(cue, dict):
                text, start = str(cue.get("text", "")), cue.get("start_ms", 0)
                if "start_seconds" in cue:
                    start = float(cue["start_seconds"]) * 1000
            else:
                text, start = str(cue.text), getattr(cue, "start_ms", 0)
            item = QListWidgetItem(f"{self._duration(float(start) / 1000)}   {text}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.transcript_list.addItem(item)

    def set_active_cue(self, index: int) -> None:
        self.transcript_list.setProperty("active_cue", index)
        self.transcript_list.viewport().update()
        if not self.follow_check.isChecked():
            return
        if not 0 <= index < self.transcript_list.count():
            self.transcript_list.clearSelection()
            return
        item = self.transcript_list.item(index)
        self.transcript_list.setCurrentItem(item)
        self.transcript_list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    def set_subtitle_mode(self, mode: str) -> None:
        self.transcript_list.setProperty("subtitle_mode", mode)
        for index in range(self.transcript_list.count()):
            self._update_accessible_transcript_item(self.transcript_list.item(index))
        self.transcript_list.doItemsLayout()
        self.transcript_list.viewport().update()

    def _update_accessible_transcript_item(self, item: QListWidgetItem) -> None:
        chinese = (
            item.data(CHINESE_ROLE)
            if self.transcript_list.property("subtitle_mode") == "bilingual"
            else None
        )
        item.setData(
            Qt.ItemDataRole.AccessibleTextRole,
            item.text() + (f"\n{chinese}" if chinese else ""),
        )

    def set_chinese_translations(self, texts: Sequence[str | None]) -> None:
        for index in range(self.transcript_list.count()):
            item = self.transcript_list.item(index)
            chinese = texts[index] if index < len(texts) else None
            if item.data(CHINESE_ROLE) != chinese:
                item.setData(CHINESE_ROLE, chinese)
            self._update_accessible_transcript_item(item)
        self.transcript_list.doItemsLayout()
        self.transcript_list.viewport().update()

    def set_busy(self, busy: bool) -> None:
        self.preview_button.setEnabled(not busy)
        self.generate_button.setEnabled(not busy and self.approval_check.isChecked())
        self.approval_check.setEnabled(not busy and self.approval_check.isEnabled())

    def show_error(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.close_allowed:
            event.accept()
        else:
            event.ignore()
            self.closing.emit()
