"""Synthetic local Git fixtures; no remote, user content or network access."""

import hashlib
import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_source_snapshot.py"
SPEC = importlib.util.spec_from_file_location("prepare_source_snapshot", SCRIPT)
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "private-source-location"
    repo.mkdir()
    git(repo, "init", "-q")
    files = {
        ".gitignore": ".artifacts/\n",
        "README.md": "Synthetic source fixture.\n",
        "src/example.py": "VALUE = 1\n",
        "packaging/macos/licenses/ffmpeg/COPYING.LGPLv2.1": "Synthetic notice fixture.\n",
        "packaging/macos/licenses/qt-doc/qtcore-index.html": "Archived website fixture.\n",
        "packaging/macos/licenses/LICENSE.html": "Archived license webpage fixture.\n",
        "packaging/macos/licenses/LICENSE.txt": "Original license text fixture.\n",
        "docs/release/LICENSE_MIT_CANDIDATE.txt": "Review candidate only.\n",
        "handoffs/private.md": "Do not publish this fixture.\n",
        ".artifacts/build.log": "Generated fixture.\n",
        "src/__pycache__/example.pyc": "Compiled fixture.\n",
        "tests/recordings/input.wav": "Audio fixture.\n",
        "docs/private-source.pdf": "Document fixture.\n",
        "docs/screenshot.png": "Image fixture.\n",
        "docs/images/g6-english.png": "Explicitly allowlisted image fixture.\n",
        "packaging/macos/licenses/LICENSE.exe": "Disguised binary fixture.\n",
        "offline-runtime/model.onnx": "Model fixture.\n",
        "src/.env": "Synthetic environment fixture.\n",
        "build/app.exe": "Binary fixture.\n",
    }
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    git(repo, "add", "--force", ".")
    git(
        repo,
        "-c",
        "user.name=Snapshot Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return repo


def test_clean_export_excludes_material_and_is_reproducible(repository, tmp_path):
    first, second = tmp_path / "new-parent/first", tmp_path / "second"
    record = snapshot.prepare_snapshot(repository, first)
    again = snapshot.prepare_snapshot(repository, second)
    assert record == again
    assert record["publication_status"] == "BLOCKED"
    assert record["github_source_release_readiness"] == "BLOCKED"
    assert record["macos_binary_distribution_readiness"] == "PENDING"
    assert record["working_tree_dirty"] is False
    assert record["history_included"] is False
    assert git(repository, "rev-parse", "HEAD").stdout.decode().strip() == record["source_commit"]
    assert git(repository, "status", "--porcelain").stdout == b""
    assert {row["path"] for row in record["files"]} == {
        ".gitignore",
        "README.md",
        "src/example.py",
        "packaging/macos/licenses/ffmpeg/COPYING.LGPLv2.1",
        "packaging/macos/licenses/LICENSE.txt",
        "docs/release/LICENSE_MIT_CANDIDATE.txt",
        "docs/images/g6-english.png",
    }
    encoded = (first / "manifest.json").read_text()
    assert str(repository) not in encoded
    assert "private-source-location" not in encoded
    assert "handoffs/private.md" not in encoded
    assert not (first / "source/LICENSE").exists()
    assert (first / "lextide-source.zip").read_bytes() == (
        second / "lextide-source.zip"
    ).read_bytes()
    with zipfile.ZipFile(first / "lextide-source.zip") as archive:
        for row in record["files"]:
            body = archive.read("lextide-source/" + row["path"])
            assert hashlib.sha256(body).hexdigest() == row["sha256"]
            assert body == (first / "source" / row["path"]).read_bytes()
        archived = json.loads(archive.read("lextide-source/" + snapshot.MANIFEST_NAME))
        assert archived == record
        assert len(archive.namelist()) == record["selected_file_count"] + 1


def test_existing_output_is_never_overwritten(repository, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(snapshot.SnapshotError, match="already exists"):
        snapshot.prepare_snapshot(repository, output)
    assert marker.read_text() == "keep"
    link = tmp_path / "broken-output-link"
    link.symlink_to(tmp_path / "missing")
    with pytest.raises(snapshot.SnapshotError, match="already exists"):
        snapshot.prepare_snapshot(repository, link)


def test_dirty_is_rejected_and_audit_never_adds_untracked(repository, tmp_path):
    (repository / "src/example.py").write_text("VALUE = 2\n")
    (repository / "src/untracked.py").write_text("LOCAL = True\n")
    with pytest.raises(snapshot.SnapshotError, match="dirty"):
        snapshot.prepare_snapshot(repository, tmp_path / "refused")
    assert not (tmp_path / "refused").exists()
    output = tmp_path / "audit"
    record = snapshot.prepare_snapshot(repository, output, allow_dirty=True)
    assert record["working_tree_dirty"] is True
    assert "DIRTY_WORKTREE_LOCAL_AUDIT_ONLY" in record["blockers"]
    assert (output / "source/src/example.py").read_text() == "VALUE = 2\n"
    assert not (output / "source/src/untracked.py").exists()


def test_internal_output_requires_ignored_location(repository):
    with pytest.raises(snapshot.SnapshotError, match="outside"):
        snapshot.prepare_snapshot(repository, repository / "public-candidate")
    output = repository / ".artifacts/new-candidate"
    record = snapshot.prepare_snapshot(repository, output)
    assert record["publication_status"] == "BLOCKED"
    assert (output / "lextide-source.zip").is_file()


def test_source_symlink_is_rejected_without_reading_target(repository, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("Synthetic private fixture.\n")
    (repository / "src/link.py").symlink_to(outside)
    git(repository, "add", "src/link.py")
    output = tmp_path / "refused-link"
    with pytest.raises(snapshot.SnapshotError, match="symlink"):
        snapshot.prepare_snapshot(repository, output, allow_dirty=True)
    assert not output.exists()
    assert outside.read_text() == "Synthetic private fixture.\n"
