import hashlib
import io
import json
import threading
import urllib.error
from types import SimpleNamespace

import pytest

from lextide import models
from lextide.errors import StudioError, ValidationError


@pytest.fixture(autouse=True)
def offline_catalog(monkeypatch):
    data = {"model.onnx": b"tiny model fixture", "voices.bin": b"tiny voice fixture"}
    assets = tuple(
        SimpleNamespace(
            filename=name,
            size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            url=f"https://example.invalid/{name}",
        )
        for name, body in data.items()
    )
    monkeypatch.setattr(
        models, "MODEL", SimpleNamespace(model_id="test-model", version="1", assets=assets)
    )

    def no_network(*args, **kwargs):
        pytest.fail("Default tests must never perform network I/O")

    monkeypatch.setattr(models, "_open_url", no_network)
    return data


@pytest.fixture
def source(tmp_path, offline_catalog):
    directory = tmp_path / "source"
    directory.mkdir()
    for name, body in offline_catalog.items():
        (directory / name).write_bytes(body)
    return directory


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(body))}


def test_status_and_default_directory_are_read_only(tmp_path, monkeypatch):
    root = tmp_path / "does-not-exist"
    monkeypatch.setenv("LEXTIDE_MODEL_DIR", str(root))
    assert models.default_model_dir() == root
    result = models.model_status(root, verify=True)
    assert result["installed"] is result["valid"] is result["ready"] is False
    assert result["disk_usage_bytes"] == 0
    assert not root.exists()
    with pytest.raises(StudioError) as failure:
        models.require_installed(root)
    assert failure.value.exit_code == 7
    assert not root.exists()


def test_download_and_removal_require_explicit_consent(tmp_path):
    root = tmp_path / "models"
    with pytest.raises(StudioError, match="consent"):
        models.install_model(root)
    with pytest.raises(StudioError, match="confirmation"):
        models.uninstall_model(root)
    assert not root.exists()


def test_local_install_verifies_and_preserves_readonly_sources(tmp_path, source, offline_catalog):
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()}
    for path in source.iterdir():
        path.chmod(0o444)
    root = tmp_path / "models"
    result = models.install_model(root, source_dir=source)
    assert result["ready"] and result["valid"] and result["installed"]
    assert result["total_bytes"] == sum(map(len, offline_catalog.values()))
    assert result["disk_usage_bytes"] >= result["total_bytes"]
    assert not result["partial"]
    assert models.model_status(root)["valid"] is None
    assert models.require_installed(root)["assets"]["model.onnx"]["sha256"] == (
        hashlib.sha256(offline_catalog["model.onnx"]).hexdigest()
    )
    assert models.install_model(root)["ready"]  # Existing valid release needs no consent.
    assert before == {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()
    }


@pytest.mark.parametrize("same_size", [True, False])
def test_invalid_local_source_never_publishes(tmp_path, source, same_size):
    original = source / "model.onnx"
    original.write_bytes(b"X" * original.stat().st_size if same_size else b"bad")
    root = tmp_path / "models"
    with pytest.raises(ValidationError):
        models.install_model(root, source_dir=source)
    assert not models.model_status(root, verify=True)["ready"]
    assert not (root / "test-model").exists()


def test_invalid_existing_release_is_never_overwritten(tmp_path, source):
    root = tmp_path / "models"
    models.install_model(root, source_dir=source)
    path = root / "test-model/model.onnx"
    path.write_bytes(b"broken")
    assert models.model_status(root, verify=True)["valid"] is False
    with pytest.raises(StudioError) as failure:
        models.install_model(root, source_dir=source)
    assert failure.value.exit_code == 7
    assert path.read_bytes() == b"broken"


def test_manifest_cannot_replace_trusted_catalog_hash(tmp_path, source):
    root = tmp_path / "models"
    models.install_model(root, source_dir=source)
    manifest = root / "test-model/manifest.json"
    contents = json.loads(manifest.read_text())
    contents["assets"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(contents))
    with pytest.raises(StudioError) as failure:
        models.require_installed(root)
    assert failure.value.exit_code == 7


def test_uninstall_leaves_parent_unrelated_files_and_input(tmp_path, source):
    root = tmp_path / "models"
    models.install_model(root, source_dir=source)
    unrelated = root / "another-model.bin"
    unrelated.write_bytes(b"untouched")
    assert models.uninstall_model(root, confirmed=True)["removed"]
    assert not (root / "test-model").exists()
    assert root.exists() and unrelated.read_bytes() == b"untouched"
    assert (source / "model.onnx").exists()
    assert not models.uninstall_model(root, confirmed=True)["removed"]


@pytest.mark.parametrize("location", ["root", "staging", "installed", "manifest", "asset"])
def test_symlinks_are_rejected_without_touching_target(tmp_path, source, location):
    root = tmp_path / "models"
    target = tmp_path / "unrelated"
    target.mkdir()
    marker = target / "important.txt"
    marker.write_text("preserve me")
    if location == "root":
        root.symlink_to(target, target_is_directory=True)
    else:
        root.mkdir()
        if location == "staging":
            (root / ".test-model.staging").symlink_to(target, target_is_directory=True)
        elif location == "installed":
            (root / "test-model").symlink_to(target, target_is_directory=True)
        else:
            models.install_model(root, source_dir=source)
            filename = "manifest.json" if location == "manifest" else "model.onnx"
            path = root / "test-model" / filename
            path.unlink()
            path.symlink_to(marker)
    with pytest.raises(StudioError) as failure:
        models.install_model(root, source_dir=source)
    assert failure.value.exit_code == 6
    with pytest.raises(StudioError):
        models.uninstall_model(root, confirmed=True)
    assert marker.read_text() == "preserve me"


def test_disk_space_checked_before_copy(tmp_path, source, monkeypatch):
    monkeypatch.setattr(models.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(StudioError) as failure:
        models.install_model(tmp_path / "models", source_dir=source)
    assert failure.value.exit_code == 6
    assert not (tmp_path / "models/test-model").exists()


def test_space_preflight_excludes_verified_staged_assets(
    tmp_path, source, monkeypatch, offline_catalog
):
    root = tmp_path / "models"
    staging = root / ".test-model.staging"
    staging.mkdir(parents=True)
    models._prepare_staging(staging)
    (staging / "model.onnx").write_bytes(offline_catalog["model.onnx"])
    free = 1024 * 1024 + len(offline_catalog["voices.bin"])
    monkeypatch.setattr(models.shutil, "disk_usage", lambda _: SimpleNamespace(free=free))
    assert models.install_model(root, source_dir=source)["ready"]


def test_partial_bytes_are_visible_but_never_ready(tmp_path):
    root = tmp_path / "models"
    staging = root / ".test-model.staging"
    staging.mkdir(parents=True)
    (staging / "model.onnx.part").write_bytes(b"abc")
    status = models.model_status(root, verify=True)
    assert status["partial"] and status["disk_usage_bytes"] == 3
    assert not status["ready"]


def test_download_resume_uses_strict_range(tmp_path, monkeypatch, offline_catalog):
    root = tmp_path / "models"
    staging = root / ".test-model.staging"
    staging.mkdir(parents=True)
    models._prepare_staging(staging)
    first_name = "model.onnx"
    first = offline_catalog[first_name]
    (staging / (first_name + ".part")).write_bytes(first[:4])
    ranges = []

    def respond(request):
        name = request.full_url.rsplit("/", 1)[1]
        body = offline_catalog[name]
        ranges.append(request.get_header("Range"))
        if name == first_name:
            return Response(
                body[4:],
                206,
                {
                    "Content-Range": f"bytes 4-{len(body) - 1}/{len(body)}",
                    "Content-Length": str(len(body) - 4),
                },
            )
        return Response(body)

    monkeypatch.setattr(models, "_open_url", respond)
    assert models.install_model(root, allow_download=True)["ready"]
    assert ranges == ["bytes=4-", None]


def test_range_ignored_restarts_instead_of_appending(tmp_path, monkeypatch, offline_catalog):
    root = tmp_path / "models"
    staging = root / ".test-model.staging"
    staging.mkdir(parents=True)
    models._prepare_staging(staging)
    (staging / "model.onnx.part").write_bytes(b"wrong partial")
    monkeypatch.setattr(
        models, "_open_url", lambda req: Response(offline_catalog[req.full_url.rsplit("/", 1)[1]])
    )
    assert models.install_model(root, allow_download=True)["ready"]


@pytest.mark.parametrize(
    "response",
    [
        lambda body: Response(body, 206, {"Content-Range": "bytes 1-5/6"}),
        lambda body: Response(body, 200, {"Content-Length": "9999999999999"}),
        lambda body: Response(body + b"extra", 200, {"X-Fixture": "no-content-length"}),
        lambda body: Response(body, 200, {"Content-Encoding": "gzip"}),
    ],
)
def test_invalid_download_response_is_not_published(
    tmp_path, monkeypatch, offline_catalog, response
):
    monkeypatch.setattr(models, "_open_url", lambda _: response(offline_catalog["model.onnx"]))
    root = tmp_path / "models"
    with pytest.raises(StudioError) as failure:
        models.install_model(root, allow_download=True)
    assert failure.value.exit_code == 4
    assert not models.model_status(root, verify=True)["ready"]


def test_download_hash_failure_discards_bad_partial(tmp_path, monkeypatch, offline_catalog):
    body = offline_catalog["model.onnx"]
    monkeypatch.setattr(models, "_open_url", lambda _: Response(b"X" * len(body)))
    root = tmp_path / "models"
    with pytest.raises(ValidationError):
        models.install_model(root, allow_download=True)
    assert not (root / ".test-model.staging/model.onnx.part").exists()
    assert not (root / "test-model").exists()


def test_download_retries_are_bounded_and_errors_do_not_leak_urls(tmp_path, monkeypatch):
    attempts = []

    def failure(request):
        attempts.append(request)
        raise urllib.error.URLError("https://example.invalid/?token=secret-value")

    monkeypatch.setattr(models, "_open_url", failure)
    with pytest.raises(StudioError) as failure:
        models.install_model(tmp_path / "models", allow_download=True, retries=2)
    assert len(attempts) == 3
    assert failure.value.exit_code == 4
    assert "secret-value" not in str(failure.value)


def test_interrupted_download_retries_from_saved_partial(tmp_path, monkeypatch, offline_catalog):
    ranges = []
    first = offline_catalog["model.onnx"]

    class Interrupted(Response):
        def read(self, amount=-1):
            if self.tell():
                raise urllib.error.URLError("interrupted transfer")
            return super().read(4)

    def respond(request):
        ranges.append(request.get_header("Range"))
        if len(ranges) == 1:
            return Interrupted(first)
        if len(ranges) == 2:
            return Response(
                first[4:],
                206,
                {
                    "Content-Range": f"bytes 4-{len(first) - 1}/{len(first)}",
                    "Content-Length": str(len(first) - 4),
                },
            )
        return Response(offline_catalog["voices.bin"])

    monkeypatch.setattr(models, "_open_url", respond)
    assert models.install_model(tmp_path / "models", allow_download=True)["ready"]
    assert ranges == [None, "bytes=4-", None]


def test_http_416_is_a_safe_download_failure(tmp_path, monkeypatch):
    def reject(request):
        raise urllib.error.HTTPError(request.full_url, 416, "private remote message", {}, None)

    monkeypatch.setattr(models, "_open_url", reject)
    with pytest.raises(StudioError) as failure:
        models.install_model(tmp_path / "models", allow_download=True, retries=0)
    assert failure.value.exit_code == 4
    assert "private" not in str(failure.value)


def test_https_redirect_cannot_downgrade_transport():
    with pytest.raises(StudioError) as failure:
        models._HTTPSRedirect().redirect_request(None, None, 302, "", {}, "http://example.invalid")
    assert failure.value.exit_code == 4


def test_source_inside_staging_is_never_deleted(tmp_path, offline_catalog):
    root = tmp_path / "models"
    source = root / ".test-model.staging"
    source.mkdir(parents=True)
    original = source / "model.onnx"
    original.write_bytes(offline_catalog["model.onnx"])
    with pytest.raises(StudioError) as failure:
        models.install_model(root, source_dir=source)
    assert failure.value.exit_code == 2
    assert original.read_bytes() == offline_catalog["model.onnx"]


def test_model_lock_timeout_is_bounded(tmp_path, source, monkeypatch):
    root = tmp_path / "models"
    models.install_model(root, source_dir=source)
    monkeypatch.setattr(models, "_LOCK_TIMEOUT", 0.01)
    with models.model_lease(root):
        with pytest.raises(StudioError) as failure:
            models.uninstall_model(root, confirmed=True)
    assert failure.value.code == "MODEL_BUSY" and failure.value.exit_code == 6
    assert models.model_status(root, verify=True)["ready"]


def test_synthesis_lease_is_immediate_when_installer_holds_lock(tmp_path, source, monkeypatch):
    root = tmp_path / "models"
    models.install_model(root, source_dir=source)

    def must_not_wait(_):
        pytest.fail("Synthesis must not block its asyncio event loop on a model lock")

    monkeypatch.setattr(models.time, "sleep", must_not_wait)
    with models._locked(root, exclusive=True):
        with pytest.raises(StudioError) as failure:
            with models.model_lease(root):
                pytest.fail("Shared lease entered while installer holds exclusive lock")
    assert failure.value.code == "MODEL_BUSY" and failure.value.exit_code == 7


def test_stale_partial_identity_is_not_reused(tmp_path, monkeypatch, offline_catalog):
    root = tmp_path / "models"
    staging = root / ".test-model.staging"
    staging.mkdir(parents=True)
    (staging / ".identity.json").write_text('{"version":"wrong"}')
    (staging / "model.onnx.part").write_bytes(b"old")
    ranges = []

    def response(request):
        ranges.append(request.get_header("Range"))
        return Response(offline_catalog[request.full_url.rsplit("/", 1)[1]])

    monkeypatch.setattr(models, "_open_url", response)
    assert models.install_model(root, allow_download=True)["ready"]
    assert ranges == [None, None]


def test_synthesis_lease_blocks_uninstall_until_released(tmp_path, source):
    root = tmp_path / "models"
    models.install_model(root, source_dir=source)
    entered = threading.Event()
    completed = threading.Event()
    failures = []

    def uninstall():
        entered.set()
        try:
            models.uninstall_model(root, confirmed=True)
        except Exception as exc:
            failures.append(exc)
        finally:
            completed.set()

    with models.model_lease(root) as info:
        assert info["model_id"] == "test-model"
        worker = threading.Thread(target=uninstall, daemon=True)
        worker.start()
        assert entered.wait(1)
        assert not completed.wait(0.1)
        assert (root / "test-model/model.onnx").exists()
    worker.join(timeout=2)
    assert completed.is_set() and not failures
    assert not (root / "test-model").exists()
