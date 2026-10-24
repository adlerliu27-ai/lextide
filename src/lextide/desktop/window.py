"""Qt Widgets presentation for Lextide, without service or playback side effects."""

from __future__ import annotations

from collections.abc import Sequence
from math import ceil
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QFont, QIcon, QKeySequence, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .i18n import current_language, tr
from .theme import ASSETS, apply_theme, brand_symbol, icon


def label(text: str, role: str = "", *, wrap: bool = False) -> QLabel:
    widget = QLabel(tr(text))
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


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


def table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels([tr(header) for header in headers])
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


class TranscriptDelegate(QStyledItemDelegate):
    """Keep wrapped rows tall enough for the font and the existing item padding."""

    def sizeHint(self, option, index):
        self.initStyleOption(option, index)
        widget = self.parent()
        # Transcript QSS uses 12 px padding, 8 px margins and a 3 px left border.
        # Reserve a little extra width so Qt's text rectangle never needs more lines.
        width = max(1, widget.viewport().width() - 2 * widget.spacing() - 48)
        text = QTextLayout(option.text, option.font)
        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        text.setTextOption(text_option)
        text.beginLayout()
        height = 0.0
        while True:
            line = text.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            height += line.height()
        text.endLayout()
        # Include vertical padding (30 px), margins (6 px), and font rounding.
        return QSize(width, ceil(height) + 40)


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
        content_layout.addWidget(self._toolbar())
        self.page_stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        for name in self.PAGES:
            page = getattr(self, f"_{name.lower()}_page")()
            self.pages[name] = page
            self.page_stack.addWidget(page)
        content_layout.addWidget(self.page_stack, 1)
        content_layout.addWidget(self._player())
        self.status_label = label("Ready · Your source files stay on this device.", "micro")
        self.status_label.setContentsMargins(16, 3, 16, 3)
        content_layout.addWidget(self.status_label)
        self._shortcuts()
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
        sidebar.setFixedWidth(222)
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
        icons = ("home", "file-text", "library", "headphones", "list-check", "settings")
        for name, resource in zip(self.PAGES, icons):
            if name == "Settings":
                layout.addSpacing(21)
                layout.addWidget(label("PREFERENCES", "micro"))
            control = self._add_icon(button("  " + tr(name)), resource)
            control.setObjectName("NavButton")
            control.setCheckable(True)
            control.clicked.connect(lambda checked=False, page=name: self.show_page(page))
            self.nav_buttons[name] = control
            layout.addWidget(control)
        layout.addStretch()
        local = label("Local library", "heading")
        layout.addWidget(local)
        layout.addWidget(label("Saved on this device", "muted"))
        layout.addSpacing(12)
        layout.addWidget(label("Lextide · Desktop", "micro"))
        return sidebar

    def _toolbar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Toolbar")
        frame.setFixedHeight(56)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(26, 7, 26, 7)
        layout.setSpacing(10)
        layout.addWidget(label("Workspace   /", "muted"))
        self.breadcrumb_label = label("Overview")
        layout.addWidget(self.breadcrumb_label)
        layout.addStretch()
        self.import_button = self._add_icon(button("Import file"), "upload")
        self.new_project_button = self._add_icon(button("New session", primary=True), "plus")
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
        layout.addWidget(label(title, "title"))
        hint = label(subtitle, "muted", wrap=True)
        layout.addWidget(hint)
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
        page, layout = self._page(
            "Your listening workspace",
            "Turn written content into natural speech, synchronized subtitles, "
            "and focused listening.",
        )
        hero, hero_layout = box("Hero", 25)
        hero_row = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(10)
        copy.addWidget(label("WELCOME TO LEXTIDE", "eyebrow"))
        copy.addWidget(label("Make every word\nworth listening to.", "title"))
        copy.addWidget(
            label("A quiet space to turn your reading into natural audio.", "muted", wrap=True)
        )
        self.overview_new_button = self._add_icon(button("Create a session", primary=True), "plus")
        self.overview_library_button = button("Open library")
        self.overview_library_button.clicked.connect(lambda: self.show_page("Library"))
        actions = row(self.overview_new_button, self.overview_library_button)
        actions.addStretch()
        copy.addLayout(actions)
        hero_row.addLayout(copy, 1)
        art = QLabel()
        art.setObjectName("OverviewBrand")
        art.setPixmap(brand_symbol(116))
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art.setFixedSize(142, 142)
        hero_row.addWidget(art)
        hero_layout.addLayout(hero_row)
        layout.addWidget(hero)
        next_row = QHBoxLayout()
        next_row.setSpacing(14)
        start, start_layout = box()
        start_layout.addWidget(label("START WITH YOUR WORDS", "eyebrow"))
        start_layout.addWidget(label("Import a document", "heading"))
        start_layout.addWidget(
            label(
                "TXT, Markdown, DOCX or text-based PDF.\n"
                "Text is extracted locally for your review.",
                "muted",
                wrap=True,
            )
        )
        self.overview_import_button = self._add_icon(button("Choose a file"), "upload")
        start_layout.addWidget(self.overview_import_button, alignment=Qt.AlignmentFlag.AlignLeft)
        next_row.addWidget(start)
        summary, summary_layout = box()
        summary_layout.addWidget(label("ON THIS DEVICE", "eyebrow"))
        summary_layout.addWidget(label("Your local sessions", "heading"))
        self.overview_summary_label = label(
            "No sessions loaded. Create a session or open an existing output folder in Library.",
            "muted",
            wrap=True,
        )
        summary_layout.addWidget(self.overview_summary_label)
        summary_layout.addStretch()
        next_row.addWidget(summary)
        layout.addLayout(next_row)
        recent_row = QHBoxLayout()
        recent_row.addWidget(label("Recent sessions", "heading"))
        recent_row.addStretch()
        view_library = button("View library  →", quiet=True)
        view_library.clicked.connect(lambda: self.show_page("Library"))
        recent_row.addWidget(view_library)
        layout.addLayout(recent_row)
        self.recent_table = table(["Session", "Status", "Engine"])
        self.recent_table.setMaximumHeight(207)
        layout.addWidget(self.recent_table)
        self.recent_empty_label = label(
            "Your first session will appear here after it has been saved.", "muted"
        )
        layout.addWidget(self.recent_empty_label)
        layout.addStretch()
        return page

    def _studio_page(self) -> QWidget:
        page, layout = self._page(
            "Prepare your text",
            "Keep the source. Review what is spoken and what appears in subtitles.",
        )
        layout.setSpacing(9)
        layout.setContentsMargins(26, 20, 26, 16)
        steps = label(
            "1  Add text     —     2  Review & prepare     —     3  Generate     —     4  Listen",
            "muted",
            wrap=True,
        )
        layout.addWidget(steps)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(tr("Session title (optional)"))
        self.title_edit.setAccessibleName(tr("Session title"))
        self.import_studio_button = self._add_icon(button("Import file"), "upload")
        self.paste_source_button = button("Paste text")
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
        layout.addLayout(
            row(
                self.title_edit,
                self.encoding_combo,
                self.import_studio_button,
                self.paste_source_button,
                stretch=0,
            )
        )
        self.source_path_label = label(
            "No source yet · Import a file or paste your original text.", "muted", wrap=True
        )
        self.source_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.source_path_label)
        self.clean_mode_combo = QComboBox()
        self.clean_mode_combo.addItem(tr("Original"), "original")
        self.clean_mode_combo.addItem(tr("English Only"), "english")
        self.clean_mode_combo.setAccessibleName(tr("Text preparation mode"))
        self.clean_button = button("Apply text preparation")
        preparation_row = row(label("Text preparation"), self.clean_mode_combo, self.clean_button)
        preparation_row.addStretch()
        layout.addLayout(preparation_row)
        self.cleaning_strength_combo = QComboBox()
        self.cleaning_strength_combo.setAccessibleName(tr("Cleaning Mode"))
        self.cleaning_strength_combo.addItem(tr("Conservative"), "conservative")
        self.cleaning_strength_combo.addItem(
            tr("Remove Chinese Translations"), "remove_chinese_translations"
        )
        self.cleaning_strength_combo.setEnabled(False)
        self.cleaning_strength_combo.setToolTip(
            tr(
                "Enhanced mode can remove translations containing names or numbers. "
                "Review every result; uncertain text is kept."
            )
        )
        strength_row = row(label("Cleaning Mode"), self.cleaning_strength_combo)
        strength_row.addStretch()
        layout.addLayout(strength_row)
        self.clean_summary_label = label(
            "Removes clear Chinese translations and formatting locally. "
            "Uncertain passages are kept for review.",
            "micro",
            wrap=True,
        )
        layout.addWidget(self.clean_summary_label)
        self.studio_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.studio_splitter.setChildrenCollapsible(False)
        original, original_layout = box("TextPanel", 12)
        original_layout.addLayout(
            row(label("Original text", "heading"), label("Read-only", "micro"), stretch=0)
        )
        self.source_edit = QPlainTextEdit()
        self.source_edit.setObjectName("OriginalText")
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText(
            tr("Your extracted or pasted source stays here, unchanged.")
        )
        self.source_edit.setMinimumHeight(180)
        self.source_edit.setAccessibleName(tr("Original source text, read only"))
        original_layout.addWidget(self.source_edit)
        original_layout.addWidget(label("Never overwritten · source preserved", "micro"))
        self.studio_splitter.addWidget(original)
        prepared, prepared_layout = box("TextPanel", 12)
        self.text_tabs = QTabWidget()
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
        prepared_layout.addWidget(self.text_tabs)
        self.character_count_label = label(
            "0 characters · preview to calculate chunks", "micro", wrap=True
        )
        prepared_layout.addWidget(self.character_count_label)
        self.studio_splitter.addWidget(prepared)
        review, review_layout = box("Review", 12)
        review_layout.addWidget(label("Text review", "heading"))
        review_layout.addWidget(
            label(
                "Preview records the actual changes. Editing invalidates approval.",
                "muted",
                wrap=True,
            )
        )
        self.changes_edit = QPlainTextEdit()
        self.changes_edit.setObjectName("ChangesText")
        self.changes_edit.setReadOnly(True)
        self.changes_edit.setPlaceholderText(tr("No changes to review yet."))
        self.changes_edit.setAccessibleName(tr("Text change record"))
        review_layout.addWidget(self.changes_edit)
        self.undo_button = button("Undo")
        self.undo_button.setToolTip(tr("Undo the last speech-text edit"))
        self.reset_button = button("Reset")
        self.reset_button.setToolTip(tr("Reset speech text to the original source"))
        review_layout.addLayout(row(self.undo_button, self.reset_button))
        review.setMinimumWidth(190)
        self.studio_splitter.addWidget(review)
        self.studio_splitter.setSizes([285, 310, 215])
        self.studio_splitter.setMinimumHeight(250)
        self.studio_splitter.setMaximumHeight(270)
        for index in range(self.studio_splitter.count()):
            self.studio_splitter.widget(index).setMaximumHeight(270)
        layout.addWidget(self.studio_splitter)
        layout.addWidget(
            label(
                "G4 subtitles use the reviewed speech text. "
                "Independent subtitle wording is not available in this version.",
                "micro",
                wrap=True,
            )
        )
        voice_card, voice_layout = box(margins=14)
        fields = QGridLayout()
        fields.setHorizontalSpacing(14)
        fields.setVerticalSpacing(6)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem(tr("Online · Edge TTS"), "edge-tts")
        self.engine_combo.addItem(tr("Local / Offline · Kokoro"), "kokoro")
        self.voice_combo = QComboBox()
        self.voice_combo.setEditable(True)
        self.voice_combo.setMinimumContentsLength(14)
        self.voice_combo.addItem("en-US-GuyNeural")
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(-50, 100)
        self.rate_spin.setValue(-4)
        self.rate_spin.setSuffix(" %")
        self.pause_spin = QSpinBox()
        self.pause_spin.setRange(0, 2000)
        self.pause_spin.setValue(0)
        self.pause_spin.setSuffix(" ms")
        for column, (caption, field) in enumerate(
            (
                ("Speech engine", self.engine_combo),
                ("Voice", self.voice_combo),
                ("Speaking rate", self.rate_spin),
                ("Sentence pause", self.pause_spin),
            )
        ):
            fields.addWidget(label(caption, "muted"), 0, column)
            field.setAccessibleName(tr(caption))
            fields.addWidget(field, 1, column)
        fields.setColumnStretch(0, 3)
        fields.setColumnStretch(1, 3)
        fields.setColumnStretch(2, 1)
        fields.setColumnStretch(3, 1)
        voice_layout.addLayout(fields)
        self.engine_hint_label = label(
            "Online service · your reviewed text is sent only after explicit permission.",
            "micro",
            wrap=True,
        )
        voice_layout.addWidget(self.engine_hint_label)
        self.filter_check = QCheckBox(tr("Kokoro v3 audio filter"))
        self.filter_check.setChecked(True)
        self.filter_check.setToolTip(
            tr("Uses the verified kokoro-hf-v3 filter; available only for Kokoro.")
        )
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText(tr("Output folder"))
        self.output_dir_edit.setAccessibleName(tr("Session output directory"))
        self.output_dir_button = self._add_icon(button("Choose…"), "folder")
        voice_layout.addLayout(
            row(self.filter_check, self.output_dir_edit, self.output_dir_button, stretch=1)
        )
        layout.addWidget(voice_card)
        layout.addStretch()
        footer = QFrame()
        footer.setObjectName("Approval")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(26, 11, 26, 11)
        footer_layout.setSpacing(6)
        self.studio_status_label = label("Review your text before generation.", "muted", wrap=True)
        footer_layout.addWidget(self.studio_status_label)
        self.approval_check = QCheckBox(tr("I approve this reviewed text"))
        self.approval_check.setEnabled(False)
        self.preview_button = button("Preview && review")
        self.generate_button = self._add_icon(button("Generate audio", primary=True), "plus")
        self.generate_button.setEnabled(False)
        footer_layout.addLayout(
            row(self.approval_check, self.preview_button, self.generate_button, stretch=0)
        )
        page.layout().addWidget(footer)
        return page

    def _library_page(self) -> QWidget:
        page, layout = self._page(
            "Your library",
            "Saved sessions and finished audio, together on this device.",
            scroll=False,
        )
        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText(tr("Search title, engine or job ID"))
        self.library_search.setClearButtonEnabled(True)
        self.library_search.setAccessibleName(tr("Search local library"))
        self.library_choose_folder_button = self._add_icon(
            button("Open existing folder…"), "folder"
        )
        self.library_refresh_button = self._add_icon(button("Refresh"), "refresh")
        layout.addLayout(
            row(
                self.library_search,
                self.library_choose_folder_button,
                self.library_refresh_button,
                stretch=0,
            )
        )
        self.library_root_label = label(
            "Choose an output folder to discover existing job manifests.", "muted", wrap=True
        )
        self.library_root_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.library_root_label)
        self.library_table = table(["Session", "Status", "Engine", "Duration"])
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
        self.library_open_button = self._add_icon(button("Show output folder"), "folder")
        self.library_load_button.setEnabled(False)
        self.library_load_button.setToolTip(
            tr(
                "Select a session in the table to verify and open it in Listen; "
                "you can also double-click a session."
            )
        )
        self.library_open_button.setEnabled(False)
        actions = row(self.library_load_button, self.library_open_button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(
            label(
                "Select a session above to enable Verify & open in Listen, or double-click it. "
                "Files are verified before playback. A missing file keeps its history record "
                "and can be located in an existing folder.",
                "micro",
                wrap=True,
            )
        )
        return page

    def _listen_page(self) -> QWidget:
        page, layout = self._page(
            "Listen", "One sentence at a time. A little more space to listen.", scroll=False
        )
        self.listen_title_label = label("No session loaded", "heading")
        self.listen_title_label.setWordWrap(True)
        self.focus_check = QCheckBox(tr("Focus view"))
        layout.addLayout(row(self.listen_title_label, self.focus_check, stretch=0))
        body = QHBoxLayout()
        body.setSpacing(16)
        reader, reader_layout = box(margins=12)
        self.font_combo = QComboBox()
        self.font_combo.setObjectName("TranscriptFontSize")
        self.font_combo.setEditable(False)
        for pixels in (12, 14, 16, 18, 20, 22, 24, 28, 32, 36, 40):
            self.font_combo.addItem(f"{pixels} px", pixels)
        self.font_combo.setCurrentIndex(self.font_combo.findData(18))
        self.font_combo.setAccessibleName(tr("Transcript font size"))
        reader_layout.addLayout(
            row(
                label("Transcript", "heading"),
                label("Text size", "micro"),
                self.font_combo,
                stretch=0,
            )
        )
        self.transcript_list = QListWidget()
        self.transcript_list.setItemDelegate(TranscriptDelegate(self.transcript_list))
        self.transcript_list.setObjectName("Transcript")
        self.transcript_list.setWordWrap(True)
        self.transcript_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.transcript_list.setUniformItemSizes(False)
        self.transcript_list.setSpacing(3)
        self.transcript_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.transcript_list.setAccessibleName(tr("Sentence transcript; choose a sentence to seek"))
        reader_layout.addWidget(self.transcript_list, 1)
        self.follow_check = QCheckBox(tr("Follow current sentence"))
        self.follow_check.setChecked(True)
        reader_layout.addLayout(
            row(
                label("Select a sentence to seek to its beginning.", "micro"),
                self.follow_check,
                stretch=0,
            )
        )
        body.addWidget(reader, 1)
        self.listen_aside, aside = box(margins=16)
        self.listen_aside.setFixedWidth(220)
        aside.addWidget(label("Session details", "heading"))
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
        aside.addStretch()
        self.listen_open_button = self._add_icon(button("Show files"), "folder")
        self.listen_open_button.setEnabled(False)
        aside.addWidget(self.listen_open_button)
        aside.addWidget(
            label(
                "Sentence-level subtitles\nPlayback speed is separate from synthesis rate.",
                "micro",
                wrap=True,
            )
        )
        body.addWidget(self.listen_aside)
        layout.addLayout(body, 1)
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
        detail_layout.addWidget(self.task_detail_edit, 1)
        self.task_pause_button = self._add_icon(button("Pause"), "pause")
        self.task_resume_button = self._add_icon(button("Resume / retry", primary=True), "refresh")
        self.task_cancel_button = self._add_icon(button("Cancel task"), "x")
        for control in (self.task_pause_button, self.task_resume_button, self.task_cancel_button):
            control.setEnabled(False)
        action_row = row(self.task_pause_button, self.task_resume_button, self.task_cancel_button)
        action_row.addStretch()
        detail_layout.addLayout(action_row)
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
        self.language_hint = label(
            "Save preferences, then restart Lextide to apply the language change.",
            "muted",
            wrap=True,
        )
        appearance_layout.addWidget(self.language_hint)
        appearance_layout.addWidget(
            label(
                "Uses the system font and native controls. No decorative motion is used.",
                "muted",
                wrap=True,
            )
        )
        layout.addWidget(appearance)
        layout.addWidget(label("Media tools · required", "heading"))
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
        layout.addWidget(media)
        layout.addWidget(label("Speech engines", "heading"))
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
        layout.addWidget(engines)
        layout.addWidget(label("Privacy & storage", "heading"))
        storage, storage_layout = box()
        storage_layout.addWidget(label("Online text transmission", "heading"))
        storage_layout.addWidget(
            label(
                "Ask before sending. Editing reviewed text invalidates its approval. "
                "Offline errors never fall back to an online engine.",
                "muted",
                wrap=True,
            )
        )
        self.settings_output_dir_edit = QLineEdit()
        self.settings_output_dir_edit.setPlaceholderText(tr("Default output folder"))
        self.settings_output_dir_button = self._add_icon(button("Choose…"), "folder")
        storage_layout.addLayout(
            row(self.settings_output_dir_edit, self.settings_output_dir_button, stretch=0)
        )
        storage_layout.addWidget(
            label(
                "Existing audio stays playable offline. Cache, finished audio and model files "
                "are separate; this version does not automatically delete them.",
                "micro",
                wrap=True,
            )
        )
        layout.addWidget(storage)
        self.settings_save_button = button("Save preferences", primary=True)
        layout.addWidget(self.settings_save_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addStretch()
        return page

    def _player(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Player")
        frame.setMinimumHeight(78)
        frame.setMaximumHeight(88)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(10)
        identity = QWidget()
        identity.setMinimumWidth(140)
        identity.setMaximumWidth(200)
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(4)
        self.player_title_label = label("No audio loaded")
        self.player_title_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.player_detail_label = label("Open a session in Library", "micro")
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
        self.position_label.setMinimumWidth(34)
        self.duration_label.setMinimumWidth(34)
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
        self.page_stack.setCurrentWidget(self.pages[name])
        self.breadcrumb_label.setText(tr(name))
        for page, control in self.nav_buttons.items():
            control.setChecked(page == name)
        if name != "Listen" and self.focus_check.isChecked():
            self.focus_check.setChecked(False)
        self._refresh_icons()
        self.page_changed.emit(name)

    def set_theme(self, preference: str = "system") -> None:
        self._theme_preference = (
            preference if preference in {"system", "light", "dark"} else "system"
        )
        self._colors = apply_theme(self._theme_preference)
        self._refresh_icons()

    def _system_theme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self._theme_preference == "system":
            self.set_theme("system")

    def _refresh_icons(self) -> None:
        colors = getattr(self, "_colors", None)
        if not colors:
            return
        for control, resource in self._icons:
            if control is getattr(self, "play_button", None):
                resource = "pause" if self._playing else "play"
            color = colors["on_accent"] if control.property("primary") else colors["muted"]
            if control.objectName() == "NavButton" and control.isChecked():
                color = colors["accent"]
            control.setIcon(icon(resource, color))

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.play_button.setIcon(icon("pause" if playing else "play", self._colors["text"]))
        self.play_button.setToolTip(tr("Pause") if playing else tr("Play"))
        self.play_button.setAccessibleName(tr("Pause audio") if playing else tr("Play audio"))

    def _set_focus_mode(self, enabled: bool) -> None:
        self.sidebar.setVisible(not enabled)
        self.listen_aside.setVisible(not enabled)

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
        self.engine_hint_label.setText(
            tr("Local / Offline · uses installed Kokoro models; no automatic online fallback.")
            if offline
            else tr("Online service · your reviewed text is sent only after explicit permission.")
        )

    def _update_character_count(self) -> None:
        self.character_count_label.setText(
            tr(
                "{count:,} characters · preview to calculate chunks",
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
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, dict(record))
                item.setToolTip(str(record.get("path") or record.get("export_path") or value))
                widget.setItem(row_index, column, item)
            if selected_path and record.get("path") == selected_path:
                widget.selectRow(row_index)
        widget.setUpdatesEnabled(True)

    def set_history(self, rows: Sequence[dict[str, Any]]) -> None:
        self._history = [dict(item) for item in rows]
        self._fill_table(self.recent_table, self._history[:4], ("title", "status", "engine"))
        self.recent_empty_label.setVisible(not rows)
        self.recent_table.setVisible(bool(rows))
        self.overview_summary_label.setText(
            tr(
                "{count} saved sessions in the selected folder. "
                "Open Library to verify files and continue listening.",
                count=len(rows),
            )
            if rows
            else tr(
                "No sessions found. Create a session or open an existing output folder in Library."
            )
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
            self.library_table, rows, ("title", "status", "engine", "duration_seconds")
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

    def set_tasks(self, rows: Sequence[dict[str, Any]]) -> None:
        self._fill_table(self.tasks_table, rows, ("title", "status", "engine", "chunks"))

    def set_transcript(self, cues: Sequence[Any]) -> None:
        self._cues = list(cues)
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
        if not 0 <= index < self.transcript_list.count():
            self.transcript_list.clearSelection()
            return
        item = self.transcript_list.item(index)
        self.transcript_list.setCurrentItem(item)
        if self.follow_check.isChecked():
            self.transcript_list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

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
