"""Frozen entry point; worker dispatch must happen before QApplication exists."""

from __future__ import annotations

import multiprocessing
import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--supervise"]:
        from lextide.longform.process import main as supervisor_main

        return supervisor_main(args[1:])
    if args[:1] == ["--desktop-worker"]:
        from lextide.desktop.worker import main as worker_main

        return worker_main(args[1:])
    from lextide.cli import main as cli_main

    if args[:1] == ["--cli"]:
        return cli_main(args[1:])
    try:
        return cli_main(["gui", *args])
    except Exception:
        # Never present raw tracebacks (which may contain private text) in Finder launches.
        from PySide6.QtWidgets import QApplication, QMessageBox

        from lextide.desktop.i18n import tr

        app = QApplication.instance() or QApplication(["Lextide"])
        QMessageBox.critical(
            None,
            tr("Lextide could not start"),
            tr(
                "Cannot initialize Lextide. Check that your user data folder is writable "
                "and the application was copied completely."
            ),
        )
        app.quit()
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
