"""Pinned, explicitly installed model assets; no download happens during discovery."""

import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import certifi

from .errors import StudioError, ValidationError
from .model_catalog import MODEL

_CHUNK = 1024 * 1024
_TIMEOUT = 30
_LOCK_TIMEOUT = 10


def default_model_dir() -> Path:
    value = os.environ.get("LEXTIDE_MODEL_DIR")
    return (
        Path(value).expanduser()
        if value
        else Path.home() / "Library/Application Support/Lextide/models"
    )


def _io_error() -> StudioError:
    return StudioError(
        "MODEL_IO", "Cannot safely access model storage or insufficient disk space.", 6
    )


def _missing() -> StudioError:
    return StudioError(
        "MODEL_INVALID", "Model is missing or invalid. Check model status and reinstall.", 7
    )


def _download_error() -> StudioError:
    return StudioError(
        "MODEL_DOWNLOAD", "Model download failed or returned an invalid response.", 4
    )


def _identity() -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", MODEL.model_id):
        raise _missing()
    filenames = set()
    assets = []
    for asset in MODEL.assets:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", asset.filename)
            or asset.filename in {"manifest.json"}
            or asset.filename in filenames
            or asset.size <= 0
            or not re.fullmatch(r"[a-f0-9]{64}", asset.sha256)
        ):
            raise _missing()
        filenames.add(asset.filename)
        assets.append(
            {
                "filename": asset.filename,
                "size": asset.size,
                "sha256": asset.sha256,
                "url": asset.url,
            }
        )
    return {"model_id": MODEL.model_id, "version": MODEL.version, "assets": assets}


def _paths(root: Path) -> tuple[Path, Path, Path]:
    _identity()
    root = Path(os.path.abspath(Path(root).expanduser()))
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink():
            raise _io_error()
    return root, root / MODEL.model_id, root / f".{MODEL.model_id}.staging"


def _safe_path(path: Path, *, directory: bool = False) -> None:
    if path.is_symlink():
        raise _io_error()
    if path.exists() and (not path.is_dir() if directory else not path.is_file()):
        raise _io_error()


def _safe_tree(path: Path) -> None:
    _safe_path(path, directory=True)
    if path.exists():
        for parent, directories, files in os.walk(path, followlinks=False):
            for name in directories:
                _safe_path(Path(parent) / name, directory=True)
            for name in files:
                _safe_path(Path(parent) / name)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches(path: Path, asset) -> bool:
    _safe_path(path)
    return path.exists() and path.stat().st_size == asset.size and _hash(path) == asset.sha256


def _read_identity(path: Path) -> bool:
    _safe_path(path)
    try:
        if path.stat().st_size > 65536:
            return False
        return json.loads(path.read_text(encoding="utf-8")) == _identity()
    except (FileNotFoundError, ValueError, UnicodeError):
        return False


def _write_json(path: Path, value: dict) -> None:
    _safe_path(path)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextlib.contextmanager
def _locked(root: Path, *, exclusive: bool):
    _safe_path(root, directory=True)
    if exclusive:
        root.mkdir(parents=True, exist_ok=True)
    elif not root.is_dir():
        raise _missing()
    lock = root / f".{MODEL.model_id}.lock"
    _safe_path(lock)
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    try:
        descriptor = os.open(lock, flags, 0o600)
    except OSError:
        raise _io_error() from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _io_error()
        operation = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(descriptor, operation)
                break
            except BlockingIOError:
                if not exclusive:
                    raise StudioError(
                        "MODEL_BUSY", "Model is being changed. Retry when it is idle.", 7
                    ) from None
                if time.monotonic() >= deadline:
                    raise StudioError(
                        "MODEL_BUSY", "Model is in use. Retry when it is idle.", 6
                    ) from None
                time.sleep(0.05)
            except OSError:
                raise _io_error() from None
        yield
    finally:
        os.close(descriptor)  # flock is released even after exceptions or process termination.


def _require(root: Path) -> dict:
    root, destination, _ = _paths(root)
    _safe_path(root, directory=True)
    _safe_path(destination, directory=True)
    if not _read_identity(destination / "manifest.json"):
        raise _missing()
    assets = {}
    for asset in MODEL.assets:
        path = destination / asset.filename
        if not _matches(path, asset):
            raise _missing()
        assets[asset.filename] = {"path": str(path), "sha256": asset.sha256, "size": asset.size}
    return {"model_id": MODEL.model_id, "version": MODEL.version, "assets": assets}


def require_installed(root: Path) -> dict:
    """Rehash every asset against the built-in catalog, without downloading or creating storage."""
    try:
        return _require(root)
    except OSError:
        raise _io_error() from None


@contextlib.contextmanager
def model_lease(root: Path):
    """Hold a shared lock throughout synthesis, preventing install/uninstall races."""
    root, _, _ = _paths(root)
    with _locked(root, exclusive=False):
        yield require_installed(root)


def _disk_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for parent, _, files in os.walk(root, followlinks=False):
        for filename in files:
            info = (Path(parent) / filename).lstat()
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def model_status(root: Path, *, verify: bool = False) -> dict:
    """Report model files only. Runtime readiness is the engine's separate responsibility."""
    root, destination, staging = _paths(root)
    try:
        _safe_path(root, directory=True)
        installed = destination.exists() or destination.is_symlink()
        valid = None if installed else False
        error = None
        if installed and verify:
            try:
                _require(root)
                valid = True
            except StudioError as exc:
                valid, error = False, exc.code
        assets = []
        for asset in MODEL.assets:
            path = destination / asset.filename
            regular = not destination.is_symlink() and not path.is_symlink() and path.is_file()
            assets.append(
                {
                    "filename": asset.filename,
                    "source": asset.url,
                    "expected_size": asset.size,
                    "sha256": asset.sha256,
                    "path": str(path),
                    "present": regular,
                    "actual_size": path.stat().st_size if regular else 0,
                }
            )
        return {
            "model_id": MODEL.model_id,
            "version": MODEL.version,
            "root": str(root),
            "installed": installed,
            "valid": valid,
            "ready": valid is True,
            "verified": bool(verify and installed),
            "error": error,
            "total_bytes": sum(asset.size for asset in MODEL.assets),
            "disk_usage_bytes": _disk_bytes(root),
            "partial": staging.exists(),
            "assets": assets,
        }
    except OSError:
        raise _io_error() from None


def _prepare_staging(staging: Path) -> None:
    _safe_tree(staging)
    if staging.exists() and not _read_identity(staging / ".identity.json"):
        # Stale data cannot be mixed with a different pinned model release.
        shutil.rmtree(staging)
    staging.mkdir(exist_ok=True)
    if not (staging / ".identity.json").exists():
        _write_json(staging / ".identity.json", _identity())


class _HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise _download_error()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(request):
    context = ssl.create_default_context(cafile=certifi.where())
    https = urllib.request.HTTPSHandler(context=context)
    return urllib.request.build_opener(_HTTPSRedirect, https).open(request, timeout=_TIMEOUT)


def _response_layout(response, offset: int, asset) -> int:
    status = response.status
    encoding = response.headers.get("Content-Encoding", "identity")
    if encoding.lower() != "identity":
        raise _download_error()
    if status == 206:
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
        if not match or tuple(map(int, match.groups())) != (offset, asset.size - 1, asset.size):
            raise _download_error()
    elif status == 200:
        offset = 0  # A server ignoring Range must replace, never append to, the partial.
    else:
        raise _download_error()
    length = response.headers.get("Content-Length")
    if length is not None and (not length.isdigit() or int(length) != asset.size - offset):
        raise _download_error()
    return offset


def _download(part: Path, asset, retries: int) -> None:
    url = urllib.parse.urlsplit(asset.url)
    if url.scheme != "https" or not url.netloc or url.username or url.password:
        raise _download_error()
    for attempt in range(retries + 1):
        offset = part.stat().st_size if part.exists() else 0
        if offset >= asset.size:
            if _matches(part, asset):
                return
            part.unlink()
            offset = 0
        headers = {"Accept-Encoding": "identity", "User-Agent": "Lextide/0.2"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(asset.url, headers=headers)
        try:
            with _open_url(request) as response:
                offset = _response_layout(response, offset, asset)
                with part.open("ab" if offset else "wb") as stream:
                    received = offset
                    while True:
                        chunk = response.read(min(_CHUNK, asset.size - received + 1))
                        if not chunk:
                            break
                        if received + len(chunk) > asset.size:
                            raise _download_error()
                        stream.write(chunk)
                        received += len(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            if received != asset.size:
                if attempt < retries:
                    continue
                raise _download_error()
            if _hash(part) != asset.sha256:
                part.unlink()
                raise ValidationError("Downloaded model failed its pinned SHA256 check.")
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            if attempt == retries:
                raise _download_error() from None


def _copy_source(source: Path, part: Path, asset) -> None:
    _safe_path(source)
    if not source.exists() or source.stat().st_size != asset.size:
        raise ValidationError("Local model source has a missing file or incorrect size.")
    with source.open("rb") as incoming, part.open("wb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, _CHUNK)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if not _matches(part, asset):
        part.unlink()
        raise ValidationError("Local model source failed its pinned SHA256 check.")


def install_model(
    root: Path, *, allow_download: bool = False, source_dir: Path | None = None, retries: int = 2
) -> dict:
    """Publish a completely verified release, retaining resumable partial downloads on failure."""
    root, destination, staging = _paths(root)
    if not isinstance(retries, int) or not 0 <= retries <= 5:
        raise StudioError("INVALID_INPUT", "Download retries must be between 0 and 5.", 2)
    try:
        _safe_path(root, directory=True)
        if not destination.exists() and source_dir is None and not allow_download:
            raise StudioError(
                "DOWNLOAD_CONSENT_REQUIRED", "Model download requires explicit consent.", 3
            )
        with _locked(root, exclusive=True):
            _safe_path(destination, directory=True)
            if destination.exists():
                _require(root)  # Invalid existing installations are never overwritten.
                return model_status(root, verify=True)
            if source_dir is not None:
                source_dir = Path(source_dir).expanduser().absolute()
                _safe_path(source_dir, directory=True)
                source_dir = source_dir.resolve()
                if source_dir.is_relative_to(root) or root.is_relative_to(source_dir):
                    raise StudioError(
                        "INVALID_INPUT", "Model source and storage directories must not overlap.", 2
                    )
            _prepare_staging(staging)
            verified = {
                asset.filename
                for asset in MODEL.assets
                if _matches(staging / asset.filename, asset)
            }
            needed = sum(asset.size for asset in MODEL.assets if asset.filename not in verified)
            # Verified staging files occupy disk already; only the remaining bytes are needed.
            already = sum(
                min(asset.size, (staging / (asset.filename + ".part")).stat().st_size)
                for asset in MODEL.assets
                if asset.filename not in verified
                and (staging / (asset.filename + ".part")).exists()
            )
            if shutil.disk_usage(root).free < needed - already + 1024 * 1024:
                raise _io_error()
            for asset in MODEL.assets:
                final, part = staging / asset.filename, staging / (asset.filename + ".part")
                _safe_path(final)
                _safe_path(part)
                if asset.filename not in verified:
                    if final.exists():
                        final.unlink()
                    if source_dir is not None:
                        _copy_source(source_dir / asset.filename, part, asset)
                    else:
                        _download(part, asset, retries)
                    os.replace(part, final)
                elif part.exists():
                    part.unlink()
            _write_json(staging / "manifest.json", _identity())
            descriptor = os.open(staging, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(staging, destination)
            with contextlib.ExitStack() as stack:
                descriptor = os.open(root, os.O_RDONLY)
                stack.callback(os.close, descriptor)
                os.fsync(descriptor)
            return model_status(root, verify=True)
    except OSError:
        raise _io_error() from None


def uninstall_model(root: Path, *, confirmed: bool = False) -> dict:
    """Remove only this catalog's release/staging; leave storage and other models in place."""
    if not confirmed:
        raise StudioError(
            "CONFIRMATION_REQUIRED", "Model removal requires explicit confirmation.", 2
        )
    root, destination, staging = _paths(root)
    try:
        _safe_path(root, directory=True)
        if not root.exists():
            return {"model_id": MODEL.model_id, "removed": False, "root": str(root)}
        with _locked(root, exclusive=True):
            for path in (destination, staging):
                _safe_tree(path)
            removed = destination.exists() or staging.exists()
            for path in (destination, staging):
                if path.exists():
                    shutil.rmtree(path)
            return {"model_id": MODEL.model_id, "removed": removed, "root": str(root)}
    except OSError:
        raise _io_error() from None
