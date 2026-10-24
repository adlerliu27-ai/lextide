"""Reproducible local development bundle; never signs with a developer identity."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(*args: str, **kwargs) -> None:
    subprocess.run(args, check=True, **kwargs)


def runtime_distributions() -> list[str]:
    """Installed desktop dependency closure, not the entire build environment."""
    pending = ["lextide"]
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        normalized = name.lower().replace("_", "-")
        if normalized in seen:
            continue
        seen.add(normalized)
        for entry in metadata.requires(name) or []:
            dependency = Requirement(entry)
            extra = "desktop" if normalized == "lextide" else ""
            if dependency.marker is None or dependency.marker.evaluate({"extra": extra}):
                pending.append(dependency.name)
    return sorted(seen)


def prepare_licenses(staging: Path) -> list[dict]:
    destination = staging / "licenses"
    destination.mkdir()
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", destination)
    supplied = ROOT / "packaging/macos/licenses"
    if supplied.exists():
        shutil.copytree(supplied, destination / "supplemental")
    rows = []
    for name in sorted(set(runtime_distributions() + ["pyinstaller", "pyinstaller-hooks-contrib"])):
        dist = metadata.distribution(name)
        files = []
        for entry in dist.files or []:
            if any(
                word in entry.name.lower() for word in ("license", "licence", "copying", "notice")
            ):
                source = Path(dist.locate_file(entry))
                if source.is_file() and source.suffix not in {".py", ".pyc", ".so"}:
                    target = destination / name / Path(str(entry))
                    # Distribution RECORD entries can contain ../; do not allow path escape.
                    if ".." in entry.parts:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    files.append(str(target.relative_to(destination)))
        rows.append(
            {
                "name": name,
                "version": dist.version,
                "license_expression": dist.metadata.get("License-Expression"),
                "license_metadata": dist.metadata.get("License"),
                "license_files": files,
                "role": "build-tool" if name.startswith("pyinstaller") else "runtime",
            }
        )
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        raise SystemExit("Python runtime LICENSE.txt is missing; supply it before building.")
    shutil.copy2(python_license, destination / "PYTHON-LICENSE.txt")
    (destination / "components.json").write_text(json.dumps(rows, indent=2) + "\n")
    return rows


def make_icon(staging: Path) -> None:
    # This is the already approved G4 raster, not a new design or an edit to originals.
    original = ROOT / "src/lextide/desktop/assets/brand/lextide-app-icon-1024.png"
    iconset = staging / "Lextide.iconset"
    iconset.mkdir()
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            pixels = str(points * scale)
            run(
                "/usr/bin/sips",
                "-z",
                pixels,
                pixels,
                str(original),
                "--out",
                str(iconset / f"icon_{points}x{points}{suffix}.png"),
                stdout=subprocess.DEVNULL,
            )
    run("/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(staging / "Lextide.icns"))


def file_inventory(app: Path) -> list[dict]:
    rows = []
    for path in sorted(app.rglob("*")):
        relative = str(path.relative_to(app))
        if path.is_symlink():
            target = os.readlink(path)
            if not path.resolve().is_relative_to(app.resolve()):
                raise SystemExit(f"Bundle symlink escapes application: {relative}")
            rows.append({"path": relative, "symlink": target})
        elif path.is_file():
            rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="New build directory; existing paths are refused"
    )
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise SystemExit("This development target requires macOS arm64; no universal2 claim.")
    destination = args.output or ROOT / ".artifacts/codex-g5/builds" / time.strftime(
        "%Y%m%d-%H%M%S"
    )
    destination = destination.expanduser().absolute()
    destination.mkdir(parents=True, exist_ok=False)
    staging = destination / "staging"
    staging.mkdir()
    version = metadata.version("lextide")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    inputs = [
        ROOT / "README.md",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / "offline-runtime/pyproject.toml",
        ROOT / "offline-runtime/uv.lock",
        ROOT / "THIRD_PARTY_NOTICES.md",
    ]
    for folder in ("src/lextide", "packaging/macos", "scripts", "docs/release"):
        inputs.extend(
            p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts
        )
    source_hashes = {str(p.relative_to(ROOT)): sha256(p) for p in sorted(inputs)}
    info = {
        "version": version,
        "commit": commit,
        "worktree_dirty": dirty,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "pyinstaller": metadata.version("pyinstaller"),
        "hooks": metadata.version("pyinstaller-hooks-contrib"),
        "source_sha256": source_hashes,
        "bundle_identifier": "local.lextide.development",
        "signing": "AD_HOC_DEVELOPMENT_ONLY",
        "notarization": "NOTARIZATION_PENDING",
        "external_dependencies": ["ffmpeg", "ffprobe", "optional Kokoro runtime and models"],
    }
    info["components"] = prepare_licenses(staging)
    (staging / "build-info.json").write_text(json.dumps(info, indent=2) + "\n")
    make_icon(staging)
    environment = dict(
        os.environ,
        LEXTIDE_BUILD_STAGING=str(staging),
        LEXTIDE_BUILD_VERSION=version,
        LEXTIDE_BUILD_COMMIT=commit,
    )
    with (destination / "build.log").open("w") as log:
        run(
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            str(destination / "dist"),
            "--workpath",
            str(destination / "work"),
            str(ROOT / "packaging/macos/Lextide.spec"),
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    app = destination / "dist/Lextide.app"
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
    inventory = file_inventory(app)
    encoded = (json.dumps(inventory, indent=2) + "\n").encode()
    (destination / "bundle-inventory.json").write_bytes(encoded)
    archive = destination / f"Lextide-{version}-macOS-arm64-development.zip"
    run("/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive))
    info.update(
        {
            "app": str(app),
            "archive": str(archive),
            "archive_sha256": sha256(archive),
            "inventory_sha256": hashlib.sha256(encoded).hexdigest(),
            "bundle_file_bytes": sum(row.get("bytes", 0) for row in inventory),
        }
    )
    (destination / "build-record.json").write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps({key: info[key] for key in ("app", "archive", "archive_sha256")}, indent=2))


if __name__ == "__main__":
    main()
