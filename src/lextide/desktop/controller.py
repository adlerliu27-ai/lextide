"""Native presentation controller; all expensive backend work stays in workers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QObject, QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from .i18n import current_language, tr
from .messages import cleaning_reason, localize_error, localize_note
from .player import LocalPlayer
from .preferences import load_preferences, save_preferences
from .process import WorkerClient


def confirm_transmission(parent, text: str, settings: dict, sha256: str) -> bool:
    """Every invocation starts unchecked. No approval is stored in preferences."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("Approve this online transmission"))
    dialog.resize(740, 570)
    layout = QVBoxLayout(dialog)
    label = QLabel(
        tr(
            "All text below will be sent to Microsoft Edge TTS.\n"
            "Only this speech text and voice settings are sent, not source files or paths.\n"
            "Voice: {voice}    Rate: {rate}\nText SHA-256: {sha}",
            voice=settings["voice"],
            rate=settings["rate"],
            sha=sha256,
        )
    )
    label.setWordWrap(True)
    layout.addWidget(label)
    preview = QPlainTextEdit()
    preview.setReadOnly(True)
    preview.setPlainText(text)
    preview.setAccessibleName(tr("Complete text to be sent in this run"))
    layout.addWidget(preview, 1)
    consent = QCheckBox(
        tr("I explicitly allow sending all the text above and the voice settings for this run")
    )
    consent.setChecked(False)
    layout.addWidget(consent)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Authorize and start"))
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
    buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
    consent.toggled.connect(buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog.exec() == QDialog.DialogCode.Accepted and consent.isChecked()


class Controller(QObject):
    def __init__(self, window, data_dir: Path):
        super().__init__(window)
        self.w = window
        self.root = data_dir.expanduser().resolve()
        self.preferences = load_preferences(self.root)
        self.workers: set[WorkerClient] = set()
        self.synthesis: WorkerClient | None = None
        self.active_task: str | None = None
        self.preview: dict | None = None
        self.preview_signature: str | None = None
        self.revision = 0
        self.provenance: dict = {}
        self.cleaning: dict | None = None
        self.history: list[dict] = []
        self.capabilities: dict[str, dict] = {}
        self.active_title = ""
        self.last_verified: dict | None = None
        self.closing = False
        self.confirming = False
        self.poll_pending = False
        self.history_pending = False
        self.player = LocalPlayer(window)
        self._connect()
        self._restore_preferences()
        self._invalidate()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(1500)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        QTimer.singleShot(0, self.refresh_history)
        QTimer.singleShot(50, self.refresh_engines)

    def _connect(self):
        w = self.w
        for button in (w.import_button, w.overview_import_button, w.import_studio_button):
            button.clicked.connect(self.import_file)
        for button in (w.new_project_button, w.overview_new_button):
            button.clicked.connect(self.new_draft)
        w.paste_source_button.clicked.connect(self.paste_text)
        w.speech_edit.textChanged.connect(self._invalidate)
        w.engine_combo.currentIndexChanged.connect(self._engine_changed)
        w.voice_combo.currentTextChanged.connect(self._invalidate)
        w.rate_spin.valueChanged.connect(self._invalidate)
        w.filter_check.toggled.connect(self._invalidate)
        w.pause_spin.valueChanged.connect(self._invalidate)
        for key in ("model_dir", "offline_python", "ffmpeg", "ffprobe"):
            getattr(w, f"{key}_edit").textChanged.connect(self._runtime_changed)
            getattr(w, f"{key}_button").clicked.connect(
                lambda checked=False, name=key: self.choose_runtime(name)
            )
        w.output_dir_edit.textChanged.connect(self._invalidate)
        w.preview_button.clicked.connect(self.prepare_preview)
        w.approval_check.toggled.connect(self._gate)
        w.generate_button.clicked.connect(self.generate)
        w.undo_button.clicked.connect(w.speech_edit.undo)
        w.speech_edit.undoAvailable.connect(w.undo_button.setEnabled)
        w.undo_button.setEnabled(False)
        w.reset_button.clicked.connect(self.reset_text)
        w.output_dir_button.clicked.connect(self.choose_output)
        w.settings_output_dir_button.clicked.connect(lambda: self.choose_output(settings=True))
        w.settings_save_button.clicked.connect(self.save_settings)
        w.font_combo.currentIndexChanged.connect(self._save_font_size)
        w.clean_button.clicked.connect(self.apply_cleaning)
        w.clean_mode_combo.currentIndexChanged.connect(self._cleaning_mode_changed)
        w.cleaning_strength_combo.currentIndexChanged.connect(self._cleaning_mode_changed)
        w.model_refresh_button.clicked.connect(self.refresh_engines)
        w.library_refresh_button.clicked.connect(self.refresh_history)
        w.task_refresh_button.clicked.connect(self.refresh_history)
        w.library_search.textChanged.connect(self._display_history)
        w.library_choose_folder_button.clicked.connect(self.choose_library)
        w.library_load_button.clicked.connect(self.load_selected)
        w.library_table.cellDoubleClicked.connect(lambda *_: self.load_selected())
        w.library_table.itemSelectionChanged.connect(self._library_details)
        w.recent_table.cellDoubleClicked.connect(lambda *_: self.load_selected(recent=True))
        w.library_open_button.clicked.connect(self.open_selected_folder)
        w.listen_open_button.clicked.connect(self.open_playing_folder)
        w.task_pause_button.clicked.connect(self.pause)
        w.task_resume_button.clicked.connect(self.resume)
        w.task_cancel_button.clicked.connect(self.cancel)
        w.tasks_table.itemSelectionChanged.connect(self._task_selection)
        w.theme_combo.currentIndexChanged.connect(self._save_theme)
        w.closing.connect(self.shutdown)

    def _restore_preferences(self):
        w = self.w
        w.output_dir_edit.setText(self.preferences["out_dir"])
        w.settings_output_dir_edit.setText(self.preferences["out_dir"])
        for key in ("model_dir", "offline_python", "ffmpeg", "ffprobe"):
            getattr(w, f"{key}_edit").setText(self.preferences[key])
        w.theme_combo.setCurrentIndex(w.theme_combo.findData(self.preferences["theme"]))
        w.set_theme(self.preferences["theme"])
        w.language_combo.setCurrentIndex(w.language_combo.findData(self.preferences["language"]))
        w.font_combo.setCurrentIndex(
            w.font_combo.findData(self.preferences["transcript_font_size"])
        )
        w.pause_spin.setValue(0)
        self._engine_changed()

    def _runtime(self):
        return {
            k: v
            for k, v in {
                "model_dir": self.w.model_dir_edit.text().strip(),
                "offline_python": self.w.offline_python_edit.text().strip(),
                "ffmpeg": self.w.ffmpeg_edit.text().strip(),
                "ffprobe": self.w.ffprobe_edit.text().strip(),
            }.items()
            if v
        }

    def _runtime_changed(self):
        self.capabilities.clear()
        self.w.model_status_label.setText(tr("Local paths changed. Check local status again."))
        self.w.media_status_label.setText(
            tr("Local paths changed. Check FFmpeg and ffprobe again.")
        )
        self._invalidate()

    def _settings(self):
        return {
            "engine": self.w.engine_combo.currentData(),
            "voice": self.w.voice_combo.currentText().strip(),
            "rate": f"{self.w.rate_spin.value():+d}%",
            "timeout": 90,
        }

    def _signature(self):
        return json.dumps(
            {
                "text": self.w.speech_edit.toPlainText(),
                "settings": self._settings(),
                "filter": self.w.filter_check.isChecked(),
                "pause": self.w.pause_spin.value(),
                "out_dir": self.w.output_dir_edit.text(),
                "runtime": self._runtime(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _invalidate(self, *_):
        self.revision += 1
        self.preview = None
        self.preview_signature = None
        self.w.approval_check.setChecked(False)
        self.w.approval_check.setEnabled(False)
        self.w.subtitle_edit.setPlainText(self.w.speech_edit.toPlainText())
        size = len(self.w.speech_edit.toPlainText().encode("utf-8"))
        self.w.studio_status_label.setText(
            tr("Needs review · {size:,} UTF-8 bytes / 1 MiB", size=size)
        )
        self._gate()

    def _gate(self, *_):
        self.w.generate_button.setEnabled(
            self.preview is not None
            and self.w.approval_check.isChecked()
            and self.synthesis is None
            and self.preview_signature == self._signature()
            and self.capabilities.get(self.w.engine_combo.currentData(), {}).get("available")
            is not False
            and (
                self.w.engine_combo.currentData() != "kokoro"
                or self.capabilities.get("kokoro", {}).get("available") is True
            )
        )

    def _engine_changed(self, *_):
        kokoro = self.w.engine_combo.currentData() == "kokoro"
        with QSignalBlocker(self.w.voice_combo):
            self.w.voice_combo.clear()
            capabilities = self.capabilities.get(self.w.engine_combo.currentData(), {})
            self.w.voice_combo.addItems(
                list(capabilities.get("voices", []))
                or (["af_heart"] if kokoro else ["en-US-GuyNeural"])
            )
            self.w.voice_combo.setCurrentText("af_heart" if kokoro else "en-US-GuyNeural")
            rate_range = capabilities.get("rate_range", [-50, 100])
            self.w.rate_spin.setRange(*rate_range)
        self.w.filter_check.setEnabled(kokoro)
        self._invalidate()
        if kokoro and not self.capabilities.get("kokoro", {}).get("available"):
            self.w.status_label.setText(
                tr("Kokoro is unavailable. Check the model and runtime in Settings.")
            )

    def new_draft(self):
        if self.w.speech_edit.toPlainText().strip():
            answer = QMessageBox.question(
                self.w,
                tr("New draft"),
                tr(
                    "Replace the current unsubmitted draft? Existing tasks and exports "
                    "will not change."
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.w.show_page("Studio")
                return
        self.provenance = {}
        self.cleaning = None
        self.w.clean_summary_label.clear()
        self.w.source_edit.clear()
        self.w.speech_edit.clear()
        self.w.changes_edit.clear()
        self.w.source_path_label.setText(tr("Pasted text · local draft"))
        self.w.title_edit.setText(tr("Untitled session"))
        self.w.show_page("Studio")
        self.w.speech_edit.setFocus()

    def reset_text(self):
        if not self._confirm_replacement():
            return
        self.cleaning = None
        self.w.clean_summary_label.clear()
        cursor = self.w.speech_edit.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(self.w.source_edit.toPlainText())
        cursor.endEditBlock()

    def _confirm_replacement(self):
        if self.w.speech_edit.toPlainText() == self.w.source_edit.toPlainText():
            return True
        return (
            QMessageBox.question(
                self.w,
                tr("Replace edited speech text?"),
                tr(
                    "This replaces your current speech draft with a new version "
                    "of the original text. "
                    "You can use Undo afterwards. Existing tasks are unchanged."
                ),
            )
            == QMessageBox.StandardButton.Yes
        )

    def _cleaning_mode_changed(self, *_):
        self.w.cleaning_strength_combo.setEnabled(
            self.w.clean_mode_combo.currentData() == "english"
        )
        # No text changes until Apply is explicitly clicked. Also reject stale
        # worker results and require a new review after a mode selection changes.
        self._invalidate()

    def apply_cleaning(self):
        # Preparation is an explicit local action, always before preview and consent.
        if self.confirming or self.closing or self.synthesis:
            return
        # Qt normalizes CRLF and paragraph separators for display. Keep the
        # importer/pasted-source snapshot exact for provenance and its hash.
        source = self.provenance.get("text", self.w.source_edit.toPlainText())
        if not source:
            source = self.w.speech_edit.toPlainText()
        if not source.strip():
            self._error(tr("Add source text before preparing an English draft."))
            return
        if self.w.source_edit.toPlainText() and not self._confirm_replacement():
            return
        mode = self.w.clean_mode_combo.currentData()
        strength = self.w.cleaning_strength_combo.currentData()
        revision = self.revision
        self.w.status_label.setText(tr("Preparing text locally. Nothing is sent online."))
        self._request(
            {"command": "clean", "text": source, "mode": mode, "cleaning_mode": strength},
            lambda data: self._cleaned(data, revision, source, mode, strength),
        )

    def _cleaned(self, data, revision, source, mode, strength="conservative"):
        if (
            revision != self.revision
            or mode != self.w.clean_mode_combo.currentData()
            or strength != self.w.cleaning_strength_combo.currentData()
        ):
            self._error(
                tr(
                    "The draft or preparation mode changed. Your edits were kept; "
                    "apply preparation again."
                )
            )
            return
        self.w.source_edit.setPlainText(source)
        self.provenance["text"] = source
        self.cleaning = {k: v for k, v in data.items() if k != "original_text"}
        self.cleaning.update(
            {
                "mode": mode,
                "cleaning_mode": strength if mode == "english" else "conservative",
                "original_sha256": hashlib.sha256(source.encode()).hexdigest(),
            }
        )
        cursor = self.w.speech_edit.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(data["cleaned_text"])
        cursor.endEditBlock()
        self._invalidate()  # Also invalidates approval when the resulting text is identical.
        summary = tr(
            (
                "Original: {original:,} characters · Prepared: {cleaned:,} · "
                "Chinese lines removed: {removed} · Review warnings: {warnings}"
            ),
            original=len(source),
            cleaned=len(data["cleaned_text"]),
            removed=data["removed_chinese_lines"],
            warnings=len(data["warnings"]),
        )
        if mode == "english" and strength == "remove_chinese_translations":
            summary += "\n" + tr(
                "Chinese translation segments removed: {count}. "
                "Compare with the original before approving.",
                count=data["removed_chinese_segments"],
            )
        details = [
            tr(
                "Line {line}: {reason}",
                line=note.get("line", "?"),
                reason=cleaning_reason(note["reason"]),
            )
            for note in data["warnings"][:50]
        ]
        if not data["cleaned_text"].strip():
            details.append(
                tr("No English speech text remains. Add or edit text before previewing.")
            )
        self.w.clean_summary_label.setText(summary + ("\n" + "\n".join(details) if details else ""))
        self.w.status_label.setText(
            tr("Text preparation finished. Review and edit the full draft before approving it.")
        )

    def _request(self, payload, callback=None, *, timeout=60_000, on_error=None):
        client = WorkerClient(payload, self.root / "requests", timeout_ms=timeout, parent=self)
        self.workers.add(client)
        if callback:
            client.result_received.connect(callback)
        client.error_received.connect(on_error or self._error)
        client.finished.connect(lambda: self._worker_finished(client))
        QTimer.singleShot(0, client.start)
        return client

    def _worker_finished(self, client):
        self.workers.discard(client)
        if client is self.synthesis:
            self.synthesis = None
            self.w.set_busy(False)
            self._task_selection()
            self._gate()
            if not self.closing:
                self.refresh_history()
        client.deleteLater()
        if self.closing and not self.workers:
            self.w.close_allowed = True
            QTimer.singleShot(0, self.w.close)

    def _error(self, message):
        message = localize_error(message)
        self.w.status_label.setText(message)
        if not self.closing:
            self.w.show_error(tr("Operation not completed"), message)

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self.w, tr("Import a local document"), "", tr("Documents (*.txt *.md *.docx *.pdf)")
        )
        if not path:
            return
        revision = self.revision
        self.w.status_label.setText(tr("Extracting the document locally. Nothing is uploaded."))
        self._request(
            {
                "command": "import",
                "path": path,
                "encoding": self.w.encoding_combo.currentData() or "auto",
            },
            lambda data: self._imported(data, revision),
        )

    def _imported(self, data, revision):
        if self.revision != revision:
            self._error(
                tr(
                    "The draft changed during extraction. Your edits were kept; import "
                    "again to replace them."
                )
            )
            return
        self.provenance = data.copy()
        self.cleaning = None
        self.w.clean_summary_label.clear()
        self.w.source_edit.setPlainText(data["text"])
        self.w.speech_edit.setPlainText(data["text"])
        self.w.source_path_label.setText(data.get("source_path", "Pasted text"))
        self.w.title_edit.setText(data.get("source_name", tr("Untitled session")))
        self.w.changes_edit.setPlainText(
            "\n".join(localize_note(x) for x in data.get("changes", []) + data.get("warnings", []))
        )
        self.w.status_label.setText(
            tr("Extraction complete. The source file is unchanged; edit and preview the text.")
        )
        self.w.show_page("Studio")

    def paste_text(self):
        text, ok = QInputDialog.getMultiLineText(
            self.w,
            tr("Paste source text"),
            tr("Kept on this device. Review the speech text before synthesis."),
        )
        if ok:
            if len(text.encode("utf-8")) > 1024 * 1024:
                self._error(tr("Text exceeds the 1 MiB limit."))
                return
            self._imported(
                {
                    "text": text,
                    "source_name": "Pasted text",
                    "format": "text",
                    "changes": [],
                    "warnings": [],
                },
                self.revision,
            )

    def prepare_preview(self):
        revision = self.revision
        self.w.status_label.setText(tr("Checking text and chunks locally."))
        self._request(
            {
                "command": "preview",
                "text": self.w.speech_edit.toPlainText(),
                "settings": self._settings(),
                "pause_ms": self.w.pause_spin.value(),
                "audio_filter": "on" if self.w.filter_check.isChecked() else "off",
            },
            lambda data: self._prepared(data, revision),
        )

    def _prepared(self, data, revision):
        if revision != self.revision:
            self.w.status_label.setText(tr("Text changed during preview. Preview it again."))
            return
        if self.w.speech_edit.toPlainText() != data["text"]:
            with QSignalBlocker(self.w.speech_edit):
                cursor = self.w.speech_edit.textCursor()
                cursor.beginEditBlock()
                cursor.select(QTextCursor.SelectionType.Document)
                cursor.insertText(data["text"])
                cursor.endEditBlock()
        self.w.subtitle_edit.setPlainText(data["text"])
        self.preview = data
        self.w.undo_button.setEnabled(self.w.speech_edit.document().isUndoAvailable())
        self.preview_signature = self._signature()
        changes = data.get("changes", [])
        messages = [localize_note(x) for x in self.provenance.get("changes", [])]
        messages.extend(localize_note(x) for x in self.provenance.get("warnings", []))
        messages.extend(json.dumps(c, ensure_ascii=False) for c in changes)
        if self.w.source_edit.toPlainText() != data["text"]:
            messages.append(
                tr(
                    "The speech text differs from the extracted source. Both versions "
                    "will be saved separately."
                )
            )
        self.w.changes_edit.setPlainText(
            "\n".join(messages) or tr("No additional cleanup was applied.")
        )
        self.w.studio_status_label.setText(
            tr(
                (
                    "{characters:,} characters · {segments} segments · {chunks} chunks "
                    "· Review the full speech text"
                ),
                characters=data["character_count"],
                segments=data["sentence_count"],
                chunks=data["chunk_count"],
            )
        )
        self.w.character_count_label.setText(
            tr(
                "{characters:,} characters · {chunks} chunks",
                characters=data["character_count"],
                chunks=data["chunk_count"],
            )
        )
        self.w.approval_check.setChecked(False)
        self.w.approval_check.setEnabled(True)
        self.w.status_label.setText(
            tr("Local preview complete. No online request or synthesis has started.")
        )
        self._gate()

    def generate(self):
        if (
            self.closing
            or self.confirming
            or self.synthesis
            or not self.preview
            or self.preview_signature != self._signature()
        ):
            return
        if not self.w.approval_check.isChecked():
            return
        preview = self.preview.copy()
        signature = self.preview_signature
        settings = self._settings()
        allow_online = False
        if settings["engine"] == "edge-tts":
            self.confirming = True
            try:
                allow_online = confirm_transmission(
                    self.w, preview["text"], settings, preview["sha256"]
                )
            finally:
                self.confirming = False
            if not allow_online:
                return
        if self.closing or self.synthesis or signature != self._signature():
            self.w.status_label.setText(
                tr("Reviewed content or task state changed. Review it again.")
            )
            return
        out_dir = self.w.output_dir_edit.text().strip()
        if not out_dir or not Path(out_dir).is_absolute():
            self._error(tr("Choose an absolute path for the output directory."))
            return
        self.preferences["out_dir"] = out_dir
        if not self._remember_root(out_dir):
            return
        self.active_title = self.w.title_edit.text()
        self.active_task = None
        self._start_synthesis(
            {
                "command": "start",
                "text": preview["text"],
                "settings": settings,
                "approved_sha256": preview["sha256"],
                "allow_online": allow_online,
                "out_dir": out_dir,
                "cache_dir": str(self.root / "chunk-cache"),
                "audio_filter": "on" if self.w.filter_check.isChecked() else "off",
                "pause_ms": self.w.pause_spin.value(),
                "runtime": self._runtime(),
                "provenance": {
                    **self.provenance,
                    "title": self.w.title_edit.text(),
                    "cleaning": self.cleaning,
                },
            }
        )

    def _start_synthesis(self, payload):
        self.w.set_busy(True)
        self.w.approval_check.setChecked(False)
        self.synthesis = self._request(payload, self._synthesized, timeout=0)
        self.synthesis.event_received.connect(self._synthesis_event)
        self._task_selection()
        self.w.show_page("Tasks")
        self.w.task_stage_label.setText(tr("Preparing · Starting a local task"))
        self.w.task_progress.setRange(0, 0)
        self._gate()

    def _synthesis_event(self, event):
        if event.get("task_path"):
            self.active_task = event["task_path"]
        if event.get("event") == "task_created":
            self.refresh_history()
        if event.get("event") == "progress":
            self._progress(event)

    def _progress(self, data):
        done, total = data.get("completed_chunks", 0), data.get("total_chunks", 0)
        self.w.task_stage_label.setText(
            tr(
                "{status} · {done} / {total} chunks",
                status=tr(data.get("status", "preparing")),
                done=done,
                total=total,
            )
        )
        self.w.task_progress.setRange(0, max(total, 1))
        self.w.task_progress.setValue(done)
        self.w.task_detail_edit.setPlainText(
            json.dumps(
                {k: v for k, v in data.items() if k not in {"review_text", "text", "chunks"}},
                ensure_ascii=False,
                indent=2,
            )
        )

    def _synthesized(self, data):
        if self.closing:
            return
        self._progress(data)
        if data.get("status") == "completed" and data.get("export_path"):
            self.w.status_label.setText(tr("Task verified and saved locally."))
            self._verify(data["export_path"], self.active_title)
        else:
            self.w.status_label.setText(
                tr(
                    "Task status: {status}. See Tasks for details.",
                    status=tr(data.get("status", "unknown")),
                )
            )

    def _poll(self):
        if not self.synthesis or not self.active_task or self.poll_pending or self.closing:
            return
        self.poll_pending = True
        client = self._request(
            {"command": "status", "task_path": self.active_task},
            self._progress,
            timeout=15_000,
            on_error=lambda text: self.w.status_label.setText(localize_error(text)),
        )
        client.finished.connect(lambda: setattr(self, "poll_pending", False))

    def pause(self):
        if self.synthesis:
            self.w.task_stage_label.setText(tr("Pausing safely; completed chunks will be kept…"))
            self.synthesis.interrupt()

    def _selected(self, table):
        row = table.currentRow()
        item = table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _task_selection(self):
        row = self._selected(self.w.tasks_table)
        if row:
            self.w.task_detail_edit.setPlainText(json.dumps(row, ensure_ascii=False, indent=2))
        unfinished = bool(row) and row.get("status") != "completed"
        self.w.task_resume_button.setEnabled(unfinished and self.synthesis is None)
        self.w.task_cancel_button.setEnabled(
            unfinished or (not row and self.synthesis is not None and self.active_task is not None)
        )
        self.w.task_pause_button.setEnabled(self.synthesis is not None)

    def resume(self):
        row = self._selected(self.w.tasks_table)
        if not row or self.synthesis:
            return
        self._request(
            {"command": "status", "task_path": row["path"]},
            lambda data: self._resume_ready(row["path"], data),
        )

    def _resume_ready(self, path, data):
        if self.closing or self.confirming or self.synthesis:
            return
        allowed = False
        if data["settings"]["engine"] == "edge-tts":
            self.confirming = True
            try:
                allowed = confirm_transmission(
                    self.w, data["review_text"], data["settings"], data["sha256"]
                )
            finally:
                self.confirming = False
            if not allowed:
                return
        if self.closing or self.synthesis:
            return
        self.active_task = path
        self.active_title = Path(path).name
        self._start_synthesis(
            {
                "command": "resume",
                "task_path": path,
                "approved_sha256": data["sha256"],
                "allow_online": allowed,
                "runtime": self._runtime(),
            }
        )

    def cancel(self):
        row = self._selected(self.w.tasks_table)
        path = row["path"] if row else self.active_task if self.synthesis else None
        if not path:
            return
        answer = QMessageBox.question(
            self.w,
            tr("Cancel task"),
            tr(
                "Cancel this task? Valid chunks and existing exports will be kept:\n{path}",
                path=path,
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._request({"command": "cancel", "task_path": path}, lambda _: self.refresh_history())

    def refresh_history(self):
        if self.history_pending or self.closing:
            return
        self.history_pending = True
        client = self._request(
            {
                "command": "history",
                "roots": self.preferences["roots"],
                "paths": self.preferences["paths"],
            },
            self._history_loaded,
        )
        client.finished.connect(lambda: setattr(self, "history_pending", False))

    def _history_loaded(self, data):
        self.history = data["items"]
        self._display_history()
        self.w.set_tasks([row for row in self.history if row.get("kind") == "task"])
        self._task_selection()
        if data.get("warnings") or data.get("truncated"):
            self.w.status_label.setText(
                tr(
                    "Some history entries could not be scanned safely. Choose a "
                    "specific task folder to load it."
                )
            )

    def _display_history(self, *_):
        self.w.set_history(self.history)
        self._library_details()
        self.w.overview_summary_label.setText(
            tr("No local sessions yet. Import text to create your first listening session.")
            if not self.history
            else tr(
                "Found {count} local tasks/sessions. Files are verified before playback.",
                count=len(self.history),
            )
        )

    def _library_details(self):
        row = self._selected(self.w.library_table)
        if row:
            text = tr(
                "Created: {created} · Source: {source}\n{path}",
                created=row.get("created_at") or tr("Unknown"),
                source=Path(row["source_path"]).name
                if row.get("source_path")
                else tr("Pasted text"),
                path=row["path"],
            )
            tooltip = tr(
                "Source: {source}\n{path}",
                source=row.get("source_path") or tr("Pasted text"),
                path=row["path"],
            )
        else:
            text = tr(
                "{count} local folders · Select a session to see its source and creation time.",
                count=len(self.preferences["roots"]),
            )
            tooltip = "\n".join(self.preferences["roots"])
        self.w.library_root_label.setText(text)
        self.w.library_root_label.setToolTip(tooltip)

    def _remember_root(self, path):
        if path not in self.preferences["roots"]:
            if len(self.preferences["roots"]) >= 20:
                self._error(
                    tr(
                        "At most 20 history folders can be saved. Use one existing "
                        "collection folder."
                    )
                )
                return False
            self.preferences["roots"].append(path)
        return self._save_preferences()

    def choose_library(self):
        path = QFileDialog.getExistingDirectory(
            self.w, tr("Choose a collection folder or an existing task/job")
        )
        if path and self._remember_root(path):
            self.refresh_history()

    def load_selected(self, *, recent=False):
        row = self._selected(self.w.recent_table if recent else self.w.library_table)
        if row:
            if row.get("kind") == "task" and row.get("status") != "completed":
                self.w.show_page("Tasks")
                self.w.status_label.setText(
                    tr("This task is unfinished. Select it in Tasks to resume.")
                )
                return
            self._verify(row.get("export_path") or row["path"], row.get("title", ""))

    def _verify(self, path, title=""):
        self.w.status_label.setText(
            tr("Decoding locally and checking the manifest, hashes and subtitles.")
        )
        self._request(
            {"command": "verify", "path": path, "runtime": self._runtime()},
            lambda data: self._verified(data, title),
            timeout=180_000,
        )

    def _verified(self, data, title):
        if self.closing:
            return
        self.last_verified = data
        self.player.load(data, title)
        self.w.listen_open_button.setEnabled(True)
        self.w.show_page("Listen")
        self.w.status_label.setText(tr("Output integrity verified. Ready for offline playback."))

    def open_selected_folder(self):
        row = self._selected(self.w.library_table)
        if row:
            self._open_folder(row.get("export_path") or row["path"])

    def open_playing_folder(self):
        if self.last_verified:
            self._open_folder(self.last_verified["job_path"])

    def _open_folder(self, path):
        directory = Path(path)
        if not directory.is_dir() or not QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(directory))
        ):
            self._error(tr("Cannot open the local output folder."))

    def choose_output(self, *, settings=False):
        path = QFileDialog.getExistingDirectory(self.w, tr("Choose a local output folder"))
        if path:
            self.w.output_dir_edit.setText(path)
            self.w.settings_output_dir_edit.setText(path)
            if settings:
                self.save_settings()

    def choose_runtime(self, key):
        edit = getattr(self.w, f"{key}_edit")
        current = edit.text().strip()
        if key == "model_dir":
            path = QFileDialog.getExistingDirectory(
                self.w, tr("Choose an existing Kokoro model directory"), current
            )
        else:
            title = (
                tr("Choose an existing Python 3.13 runtime executable")
                if key == "offline_python"
                else tr("Choose an existing {name} executable", name=key)
            )
            path, _ = QFileDialog.getOpenFileName(self.w, title, current, tr("All files (*)"))
        if path:
            edit.setText(path)

    def save_settings(self):
        out = self.w.settings_output_dir_edit.text().strip()
        if not Path(out).is_absolute():
            self._error(tr("The output directory must be an absolute path."))
            return
        self.preferences.update(
            {
                "out_dir": out,
                "model_dir": self.w.model_dir_edit.text().strip(),
                "offline_python": self.w.offline_python_edit.text().strip(),
                "ffmpeg": self.w.ffmpeg_edit.text().strip(),
                "ffprobe": self.w.ffprobe_edit.text().strip(),
                "language": self.w.language_combo.currentData(),
            }
        )
        self.w.output_dir_edit.setText(out)
        if not self._remember_root(out):
            return
        if self.preferences["language"] != current_language():
            self.w.status_label.setText(
                tr(
                    "Settings saved. Restart Lextide to apply the language; speech "
                    "text and voice settings are unchanged."
                )
            )
            self.w.language_hint.setText(tr("Language saved. Restart Lextide to apply it."))
        else:
            self.w.status_label.setText(
                tr("Local settings saved. Online permission is never saved.")
            )
        self.refresh_engines()

    def _save_theme(self):
        self.preferences["theme"] = self.w.theme_combo.currentData()
        self._save_preferences()

    def _save_font_size(self):
        self.preferences["transcript_font_size"] = self.w.font_combo.currentData()
        self._save_preferences()

    def _save_preferences(self):
        try:
            save_preferences(self.root, self.preferences)
            return True
        except OSError:
            self._error(
                tr("Could not save local settings. Check disk space and folder permissions.")
            )
            return False

    def refresh_engines(self):
        self.w.model_status_label.setText(
            tr("Checking capabilities and model integrity locally; no online requests…")
        )
        self.w.media_status_label.setText(tr("Checking FFmpeg and ffprobe in the background…"))
        runtime = self._runtime()
        self._request(
            {"command": "engines", "runtime": runtime},
            lambda data: self._engines(data) if self._runtime() == runtime else None,
        )

    def _engines(self, data):
        self.capabilities = {e["engine_id"]: e for e in data.get("engines", [])}
        model = data.get("model", {})
        media = data.get("media_tools", {})
        media_lines = []
        for name in ("ffmpeg", "ffprobe"):
            tool = media.get(name, {})
            media_lines.append(
                tr("{name} · Found: {path}", name=name, path=tool.get("path", ""))
                if tool.get("available")
                else tr("{name} · Not found. Choose an existing executable.", name=name)
            )
        self.w.media_status_label.setText(
            "\n".join(media_lines)
            + tr("\nOnly executable paths are checked here. Audio is verified before playback.")
        )
        ready = (
            tr("Installed and integrity verified")
            if model.get("ready")
            else tr("Model missing or integrity check failed")
        )
        runtime = (
            tr("Runtime metadata check passed")
            if data.get("offline_runtime", {}).get("ready")
            else tr(
                "Runtime unavailable; choose Python 3.13 with the offline dependencies installed"
            )
        )
        self.w.model_status_label.setText(
            tr(
                "Edge TTS · Online · Network service not checked\n\nKokoro · Offline · {ready}\n"
                "{runtime}. This does not verify a real synthesis run.\nModel location: {root}\n"
                "Kokoro is optional. Missing dependencies never trigger "
                "downloads or online fallback.",
                ready=ready,
                runtime=runtime,
                root=model.get("root", tr("Default model directory")),
            )
        )
        if any(not media.get(name, {}).get("available") for name in ("ffmpeg", "ffprobe")):
            self.w.status_label.setText(
                tr(
                    "Media tools unavailable. Select FFmpeg and ffprobe in Settings "
                    "and check local status."
                )
            )
        else:
            self.w.status_label.setText(
                tr("FFmpeg and ffprobe found. Kokoro is optional; see its status in Settings.")
            )
        # Capability discovery is not a synthesis health check.
        for engine in data.get("engines", []):
            if engine.get("engine_id") == self.w.engine_combo.currentData():
                voices = engine.get("voices", [])
                if voices and all(isinstance(v, str) for v in voices):
                    current = self.w.voice_combo.currentText()
                    with QSignalBlocker(self.w.voice_combo):
                        self.w.voice_combo.clear()
                        self.w.voice_combo.addItems(voices)
                        self.w.voice_combo.setCurrentText(current)
        self._gate()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.poll_timer.stop()
        self.player.media.stop()
        self.w.status_label.setText(tr("Saving and pausing tasks owned by this window…"))
        for worker in tuple(self.workers):
            if worker is self.synthesis:
                worker.interrupt()
            else:
                worker.kill()
        if not self.workers:
            self.w.close_allowed = True
            # shutdown can run inside closeEvent; Qt ignores a reentrant close().
            QTimer.singleShot(0, self.w.close)
        else:
            # Only this window's child processes; G3 atomic state supports interrupted recovery.
            QTimer.singleShot(10_000, self._force_owned_workers)

    def _force_owned_workers(self):
        for worker in tuple(self.workers):
            worker.kill()
