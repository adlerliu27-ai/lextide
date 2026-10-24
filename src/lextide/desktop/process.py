"""Bounded JSON IPC. Each worker is owned by this window, never by a shell."""

from __future__ import annotations

import json
import os
import signal
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ..runtime import desktop_worker_command
from .i18n import tr

MAX_MESSAGE = 8 * 1024 * 1024


class WorkerClient(QObject):
    event_received = Signal(dict)
    result_received = Signal(dict)
    error_received = Signal(str)
    finished = Signal()

    def __init__(self, request: dict, request_dir: Path, *, timeout_ms=60_000, parent=None):
        super().__init__(parent)
        self.request = request
        self.request_dir = request_dir
        self.path: Path | None = None
        self.buffer = bytearray()
        self.settled = False
        self.has_finished = False
        self.stopping = False
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.started.connect(self._started)
        self.process.readyReadStandardError.connect(self._discard_stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(timeout_ms)
        self.timer.timeout.connect(self._timeout)
        self.timeout_ms = timeout_ms

    @property
    def running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self):
        if self.stopping:
            self._finished()
            return
        try:
            payload = json.dumps(self.request, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(payload) > 4 * 1024 * 1024:
                raise ValueError(tr("The local request is too large. Use shorter text."))
            self.request_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, name = tempfile.mkstemp(prefix="request-", suffix=".json", dir=self.request_dir)
            self.path = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            command = desktop_worker_command(self.path)
            self.process.setProgram(command[0])
            self.process.setArguments(command[1:])
            self.process.start()
            if self.timeout_ms:
                self.timer.start()
        except (OSError, ValueError):
            self._fail(tr("Could not start the local task. Check disk space and permissions."))
            self._finished()

    def interrupt(self):
        """SIGINT lets asyncio cancel and G3 persist paused state."""
        self.stopping = True
        if self.running and self.process.processId() > 0:
            try:
                os.kill(self.process.processId(), signal.SIGINT)
            except ProcessLookupError:
                pass

    def _started(self):
        if self.stopping:
            self.interrupt()

    def kill(self):
        self.stopping = True
        if self.running:
            self.process.kill()

    def _fail(self, message):
        if not self.settled:
            self.settled = True
            self.error_received.emit(message)

    def _timeout(self):
        self._fail(
            tr(
                "The local operation timed out. Its worker was stopped; source "
                "files and saved tasks are kept."
            )
        )
        self.kill()

    def _discard_stderr(self):
        # Library diagnostics can contain document content; never expose or persist them.
        self.process.readAllStandardError()

    def _read(self):
        self.buffer.extend(bytes(self.process.readAllStandardOutput()))
        if len(self.buffer) > MAX_MESSAGE:
            self._fail(tr("The local worker returned too much data."))
            self.kill()
            return
        while b"\n" in self.buffer:
            line, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                self._fail(tr("The local worker returned an invalid message."))
                self.kill()
                return
            kind = event.get("event")
            if kind == "result":
                self.settled = True
                self.result_received.emit(event.get("data", {}))
            elif kind == "error":
                self._fail(
                    f"{event.get('code', 'ERROR')}: {event.get('message', 'Operation failed')}"
                )
            else:
                self.event_received.emit(event)

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._fail(tr("The local Python worker could not start. Check the installation."))
            self._finished()

    def _finished(self, *_):
        if self.has_finished:
            return
        self.has_finished = True
        self.timer.stop()
        self._read()
        if not self.settled and not self.stopping:
            self._fail(tr("The worker exited unexpectedly. Resume saved tasks from Tasks."))
        if self.path:
            self.path.unlink(missing_ok=True)
            self.path = None
        self.finished.emit()
