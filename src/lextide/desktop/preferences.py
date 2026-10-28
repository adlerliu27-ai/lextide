"""Small local preferences; approval and online consent are deliberately not saved."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

FONT_SIZES = (12, 14, 16, 18, 20, 22, 24, 28, 32, 36, 40)


def default_data_dir() -> Path:
    return Path.home() / "Library/Application Support/Lextide/desktop"


def load_preferences(root: Path) -> dict:
    defaults = {
        "language": "en",
        "subtitle_mode": "english",
        "transcript_font_size": 18,
        "theme": "system",
        "out_dir": str(root / "projects"),
        "roots": [str(root / "projects")],
        "paths": [],
        "model_dir": "",
        "offline_python": "",
        "ffmpeg": "",
        "ffprobe": "",
    }
    path = root / "preferences.json"
    if path.is_file() and not path.is_symlink() and path.stat().st_size <= 256 * 1024:
        try:
            value = json.loads(path.read_text("utf-8"))
            if isinstance(value, dict):
                for key in defaults:
                    item = value.get(key)
                    if key in {"roots", "paths"}:
                        if isinstance(item, list) and all(isinstance(x, str) for x in item):
                            defaults[key] = item[:200]
                    elif key == "transcript_font_size":
                        if type(item) is int and item in FONT_SIZES:
                            defaults[key] = item
                    elif isinstance(item, str):
                        defaults[key] = item
        except (ValueError, OSError):
            pass
    if defaults["theme"] not in {"system", "light", "dark"}:
        defaults["theme"] = "system"
    if defaults["language"] not in {"en", "zh_CN"}:
        defaults["language"] = "en"
    if defaults["subtitle_mode"] not in {"english", "bilingual"}:
        defaults["subtitle_mode"] = "english"
    return defaults


def save_preferences(root: Path, value: dict):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix="preferences-", suffix=".tmp", dir=root)
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, root / "preferences.json")
    finally:
        path.unlink(missing_ok=True)
