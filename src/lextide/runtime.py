"""Process and resource paths shared by source runs and the macOS app bundle."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_source_root() -> Path:
    """Source available to a separate Python, which cannot import the frozen PYZ."""
    if is_frozen():
        return (Path(sys._MEIPASS) / "worker-source").resolve()
    return Path(__file__).resolve().parents[1]


def desktop_worker_command(request_path: Path) -> list[str]:
    arguments = ["--desktop-worker"] if is_frozen() else ["-m", "lextide.desktop.worker"]
    return [sys.executable, *arguments, str(request_path)]


def external_runtime_environment() -> dict[str, str]:
    """Copy the environment without handing bundle library paths to external Python.

    macOS PyInstaller hooks can add DYLD paths. Remove only bundle entries,
    preserving the user's external runtime paths and the app's own environment.
    https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html
    """
    environment = os.environ.copy()
    if not is_frozen():
        return environment
    for key in tuple(environment):
        if key.startswith("_PYI_") or key in ("_MEIPASS2", "PYTHONHOME"):
            environment.pop(key)
    root = Path(sys._MEIPASS).resolve()
    if root.parent.name == "Contents":
        # macOS .app resources and frameworks can symlink to each other.
        root = root.parent
    for key in (
        "PATH",
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "DYLD_FALLBACK_FRAMEWORK_PATH",
    ):
        if key not in environment:
            continue
        entries = [
            entry
            for entry in environment[key].split(os.pathsep)
            if not entry or not Path(entry).resolve().is_relative_to(root)
        ]
        if entries:
            environment[key] = os.pathsep.join(entries)
        else:
            environment.pop(key)
    return environment
