"""Export a review-only source candidate, without Git history or publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT_FILES = {
    ".gitignore",
    ".python-version",
    "README.md",
    "CONTRIBUTING.md",
    "THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
    "uv.lock",
    "LICENSE",
}
SOURCE_ROOTS = {
    "src",
    "tests",
    "scripts",
    "docs",
    "examples",
    "offline-runtime",
    "packaging",
    ".github",
}
EXCLUDED_PARTS = {
    ".git",
    "handoffs",
    ".artifacts",
    ".venv",
    ".cache",
    ".tmp",
    ".models",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "outputs",
    "data",
    "results",
    "literature",
    "recordings",
    "captures",
    "screenshots",
    "private",
    "user-data",
    "node_modules",
}
SOURCE_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".toml",
    ".lock",
    ".json",
    ".yml",
    ".yaml",
    ".spec",
    ".svg",
    ".png",
    ".rst",
    ".sh",
}
REVIEWED_DOCUMENT_IMAGES = {"docs/images/g6-english.png", "docs/images/g6-chinese.png"}
EXCLUDED_SUFFIXES = {
    ".html",
    ".htm",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".wav",
    ".mp3",
    ".mp4",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".onnx",
    ".bin",
    ".dmg",
    ".exe",
    ".dll",
    ".dylib",
    ".so",
    ".pyc",
    ".pyo",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".7z",
    ".rar",
    ".log",
    ".pem",
    ".p12",
    ".key",
    ".crt",
    ".cer",
}
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MANIFEST_NAME = "SOURCE_SNAPSHOT_MANIFEST.json"
BLOCKERS = [
    "SOURCE_LICENSE_OWNER_APPROVAL_REQUIRED",
    "ARTWORK_PUBLIC_DISTRIBUTION_APPROVAL_REQUIRED",
    "FINAL_PAYLOAD_PRIVACY_AND_RELEASE_REVIEW_REQUIRED",
    "FINAL_OWNER_PUBLICATION_APPROVAL_REQUIRED",
]


class SnapshotError(Exception):
    """A safe, path-free error suitable for terminal output."""


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode:
        raise SnapshotError("A readable committed Git repository is required.")
    return result.stdout


def _excluded_reason(name: str) -> str | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SnapshotError("An unsafe tracked path was rejected.")
    if any(part in EXCLUDED_PARTS or part.startswith(".env") for part in path.parts):
        return "private_or_generated_directory"
    if len(path.parts) == 1:
        return None if name in ROOT_FILES else "outside_source_allowlist"
    if path.parts[0] not in SOURCE_ROOTS:
        return "outside_source_allowlist"
    if path.name == ".python-version":
        return None
    if any(suffix.lower() in EXCLUDED_SUFFIXES for suffix in path.suffixes):
        return "non_source_or_user_material"
    if name in REVIEWED_DOCUMENT_IMAGES:
        return None
    if path.suffix.lower() in {".png", ".svg"} and path.parts[:4] != (
        "src",
        "lextide",
        "desktop",
        "assets",
    ):
        return "non_source_or_user_material"
    # Preserve complete upstream license texts even when their suffix is unusual.
    license_text = path.parts[:3] == ("packaging", "macos", "licenses") and path.name.startswith(
        ("LICENSE", "COPYING", "NOTICE")
    )
    if path.suffix.lower() not in SOURCE_SUFFIXES and not license_text:
        return "non_source_or_user_material"
    return None


def _read_regular(repo: Path, name: str) -> bytes:
    source = repo / name
    if (
        source.is_symlink()
        or source.resolve() != source
        or not stat.S_ISREG(source.lstat().st_mode)
    ):
        raise SnapshotError("Tracked symlinks and non-regular files are not exportable.")
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise SnapshotError("A tracked file changed type during export.")
        body = stream.read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES:
        raise SnapshotError("A source file exceeds the 8 MiB review limit.")
    return body


def prepare_snapshot(repo: Path, output: Path, *, allow_dirty: bool = False) -> dict:
    """Copy allowlisted tracked files. The resulting candidate is always BLOCKED."""
    repo = repo.resolve()
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise SnapshotError("Output already exists; choose a new directory.")
    output = output.resolve()
    if output.is_relative_to(repo):
        relative = output.relative_to(repo).as_posix()
        ignored = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "--quiet", "--", relative],
            capture_output=True,
            check=False,
        )
        if not relative or ignored.returncode != 0:
            raise SnapshotError("Output must be outside the repository or in an ignored directory.")

    commit = _git(repo, "rev-parse", "--verify", "HEAD").decode().strip()
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=normal", "-z")
    dirty = bool(status)
    if dirty and not allow_dirty:
        raise SnapshotError(
            "Working tree is dirty; commit first or use --allow-dirty for local audit."
        )
    tracked = sorted(os.fsdecode(p) for p in _git(repo, "ls-files", "-z").split(b"\0") if p)
    contents = {}
    excluded = Counter()
    total = 0
    for name in tracked:
        reason = _excluded_reason(name)
        if reason:
            excluded[reason] += 1
            continue
        body = _read_regular(repo, name)
        total += len(body)
        if total > MAX_TOTAL_BYTES:
            raise SnapshotError("Selected source exceeds the 64 MiB review limit.")
        contents[name] = body
    if not contents:
        raise SnapshotError("No allowlisted source files were selected.")
    if _git(repo, "rev-parse", "HEAD").decode().strip() != commit:
        raise SnapshotError("HEAD changed during export; retry after work stops.")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=normal", "-z") != status:
        raise SnapshotError("Working tree status changed during export; retry after work stops.")

    manifest = {
        "schema_version": 1,
        "selection_policy": "g6-source-v2",
        "source_commit": commit,
        "working_tree_dirty": dirty,
        "allow_dirty": allow_dirty,
        "publication_status": "BLOCKED",
        "github_source_release_readiness": "BLOCKED",
        "macos_binary_distribution_readiness": "PENDING",
        "blockers": BLOCKERS + (["DIRTY_WORKTREE_LOCAL_AUDIT_ONLY"] if dirty else []),
        "license_note": (
            "The MIT candidate is not an effective project license. "
            "Third-party and artwork terms remain separate."
        ),
        "history_included": False,
        "untracked_files_included": False,
        "tracked_file_count": len(tracked),
        "selected_file_count": len(contents),
        "selected_bytes": total,
        "excluded_counts": dict(sorted(excluded.items())),
        "generated_files": [MANIFEST_NAME],
        "files": [
            {"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            for name, body in contents.items()
        ],
    }
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    # mkdir is exclusive even if another exporter races the initial existence check.
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    for name, body in contents.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        target.chmod(0o644)
    (output / "manifest.json").write_bytes(encoded)
    (source / MANIFEST_NAME).write_bytes(encoded)
    # Fixed order, timestamp and modes make equal inputs produce an equal ZIP.
    with zipfile.ZipFile(
        output / "lextide-source.zip", "x", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for name, body in sorted({**contents, MANIFEST_NAME: encoded}.items()):
            entry = zipfile.ZipInfo(f"lextide-source/{name}", (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, body)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New external or ignored output directory"
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Local audit only; still copies tracked files only",
    )
    args = parser.parse_args(argv)
    try:
        result = prepare_snapshot(
            Path(__file__).resolve().parents[1], args.output, allow_dirty=args.allow_dirty
        )
    except SnapshotError as exc:
        print(f"Snapshot refused: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print(
            "Snapshot failed during local file access; inspect any incomplete output.",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "source_commit",
                    "selected_file_count",
                    "selected_bytes",
                    "publication_status",
                    "working_tree_dirty",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
