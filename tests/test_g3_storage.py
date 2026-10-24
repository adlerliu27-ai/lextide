import copy
import hashlib
import json
import os
import stat
import struct
import wave
from pathlib import Path

import pytest

from lextide.errors import StudioError, ValidationError
from lextide.longform import storage


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    """Use actual small WAVs without importing an engine, model or network client."""
    wav = tmp_path / "source.wav"
    with wave.open(str(wav), "wb") as writer:
        writer.setparams((1, 2, 24_000, 0, "NONE", "not compressed"))
        writer.writeframes(struct.pack("<h", 80) * 24_000)

    def pcm(path):
        try:
            with wave.open(str(path), "rb") as reader:
                samples = reader.getnframes()
                body = reader.readframes(samples + 1)
                if reader.getsampwidth() != 2 or len(body) != samples * 2:
                    raise ValidationError("Invalid PCM fixture.")
                return {
                    "sample_rate": reader.getframerate(),
                    "samples": samples,
                    "channels": reader.getnchannels(),
                }
        except (wave.Error, EOFError):
            raise ValidationError("Invalid WAV fixture.") from None

    monkeypatch.setattr(storage, "_validate_pcm", pcm)
    request = {"text": "你好。 Test.", "engine": {"version": "test-1", "voice": "fixture"}}
    metadata = {
        "sample_rate": 24_000,
        "samples": 24_000,
        "channels": 1,
        "boundaries": [{"offset_ticks": 0, "duration_ticks": 10_000_000, "text": request["text"]}],
        "timing_source": "native fixture",
        "model": None,
        "effective_settings": {"rate": 0},
    }
    cache = storage.Cache(tmp_path / "cache")
    return cache, storage.canonical_key(request), request, wav, metadata


def publish(fixture):
    cache, key, request, wav, metadata = fixture
    with cache.lock(key):
        return cache.publish(key, request, wav, metadata)


def test_canonical_key_is_utf8_sorted_request_identity():
    first = {"text": "你好", "config": {"voice": "v", "rate": 0}}
    second = {"config": {"rate": 0, "voice": "v"}, "text": "你好"}
    expected = hashlib.sha256(
        '{"config":{"rate":0,"voice":"v"},"text":"你好"}'.encode()
    ).hexdigest()
    assert storage.canonical_key(first) == storage.canonical_key(second) == expected
    assert storage.canonical_key(first) != storage.canonical_key({**first, "text": "你好 "})
    assert storage.canonical_key({"rate": True}) != storage.canonical_key({"rate": 1})


@pytest.mark.parametrize("value", [{"n": float("nan")}, {"n": float("inf")}, {1: "a"}, []])
def test_canonical_key_rejects_non_json_requests(value):
    with pytest.raises(ValidationError):
        storage.canonical_key(value)


def test_file_hash_streams_and_refuses_symlinks(tmp_path):
    body = b"x" * (storage._BLOCK * 2 + 1)
    path = tmp_path / "input.bin"
    path.write_bytes(body)
    assert storage.file_sha256(path) == hashlib.sha256(body).hexdigest()
    link = tmp_path / "linked.bin"
    link.symlink_to(path)
    with pytest.raises(StudioError):
        storage.file_sha256(link)


def test_atomic_json_private_file_and_parent_sync(tmp_path, monkeypatch):
    calls = []
    real_fsync = os.fsync

    def fsync(descriptor):
        calls.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
        real_fsync(descriptor)

    monkeypatch.setattr(storage.os, "fsync", fsync)
    path = tmp_path / "private" / "manifest.json"
    storage.atomic_json(path, {"text": "你好", "step": 1})
    assert storage.read_json(path) == {"text": "你好", "step": 1}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert calls == [False, True]
    storage.atomic_json(path, {"step": 2})
    assert storage.read_json(path) == {"step": 2}
    assert [p.name for p in path.parent.iterdir()] == ["manifest.json"]


def test_atomic_json_failed_replace_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    storage.atomic_json(path, {"step": 1})

    def fail(*args):
        raise OSError("simulated interruption")

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(StudioError):
        storage.atomic_json(path, {"step": 2})
    assert storage.read_json(path) == {"step": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]


@pytest.mark.parametrize("body", [b"[]", b'{"n":NaN}', b'{"n":1,"n":2}', b"{", b"\xff"])
def test_read_json_rejects_invalid_values(tmp_path, body):
    path = tmp_path / "manifest.json"
    path.write_bytes(body)
    with pytest.raises(ValidationError):
        storage.read_json(path)


def test_json_bounds_and_symlink_guards(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"a":123}')
    with pytest.raises(ValidationError):
        storage.read_json(path, max_bytes=8)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    for operation in (lambda: storage.read_json(link), lambda: storage.atomic_json(link, {})):
        with pytest.raises(StudioError):
            operation()
    assert path.read_text() == '{"a":123}'


def test_safe_directory_rejects_ancestor_links_even_before_dotdot(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    for path in (linked, linked / "child", linked / ".." / "other"):
        with pytest.raises(StudioError):
            storage.safe_directory(path, create=True)
    missing = tmp_path / "missing" / "nested"
    assert storage.safe_directory(missing) == missing
    assert not missing.exists()
    assert storage.safe_directory(missing, create=True).is_dir()


def test_nonblocking_lock_releases_on_exception_and_refuses_symlink(tmp_path):
    lock = tmp_path / "locks" / "run.lock"
    with storage.exclusive_lock(lock):
        with pytest.raises(StudioError) as failure:
            with storage.exclusive_lock(lock):
                pytest.fail("competing lock was granted")
        assert failure.value.code == "TASK_BUSY"
        assert failure.value.exit_code == 6
    with pytest.raises(RuntimeError):
        with storage.exclusive_lock(lock):
            raise RuntimeError("fixture")
    with storage.exclusive_lock(lock):
        pass
    linked = tmp_path / "linked.lock"
    linked.symlink_to(lock)
    with pytest.raises(StudioError):
        with storage.exclusive_lock(linked):
            pytest.fail("symlink lock was granted")


def test_cache_round_trip_independent_copy_and_request_key(fixture):
    cache, key, request, wav, metadata = fixture
    before = wav.read_bytes(), wav.stat().st_mtime_ns
    wav.chmod(0o444)
    assert cache.load(key, request) is None
    result = publish(fixture)
    assert result["metadata"] == metadata
    assert result["cache_key"] == key
    assert result["artifact_sha256"] != key
    assert Path(result["path"]) != wav
    assert wav.stat().st_ino != Path(result["path"]).stat().st_ino
    assert (wav.read_bytes(), wav.stat().st_mtime_ns) == before
    assert cache.load(key, request) == result
    assert cache.load(key, {**request, "text": "different"}) is None
    assert stat.S_IMODE(cache.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(Path(result["path"]).stat().st_mode) == 0o600
    # A verified hit remains available after the original synthesis file is gone.
    wav.unlink()
    assert cache.load(key, request) == result


def test_cache_publication_requires_lock_and_matching_request(fixture):
    cache, key, request, wav, metadata = fixture
    with pytest.raises(StudioError, match="requires its lock"):
        cache.publish(key, request, wav, metadata)
    with cache.lock(key):
        with pytest.raises(ValidationError):
            cache.publish(key, {**request, "engine": "changed"}, wav, metadata)
        with pytest.raises(StudioError) as failure:
            with storage.Cache(cache.root).lock(key):
                pytest.fail("duplicate cache publisher acquired lock")
        assert failure.value.code == "TASK_BUSY"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(status="partial"),
        lambda m: m.update(schema_version=True),
        lambda m: m.update(cache_key="0" * 64),
        lambda m: m["request"].update(text="tampered"),
        lambda m: m.update(artifact_sha256="0" * 64),
        lambda m: m.update(bytes=1),
        lambda m: m["metadata"].update(samples=23_999),
        lambda m: m["metadata"].update(sample_rate=22_050),
        lambda m: m["metadata"].update(channels=True),
        lambda m: m["metadata"].update(boundaries=[]),
        lambda m: m["metadata"]["boundaries"][0].update(text="missing"),
        lambda m: m["metadata"]["boundaries"][0].update(offset_ticks=99_000_000),
        lambda m: m["metadata"]["boundaries"][0].update(duration_ticks=12_000_000),
    ],
)
def test_cache_revalidates_manifest_pcm_and_text(fixture, mutation):
    cache, key, request, _, _ = fixture
    publish(fixture)
    manifest = cache.root / key / "manifest.json"
    value = storage.read_json(manifest)
    mutation(value)
    storage.atomic_json(manifest, value)
    assert cache.load(key, request) is None


def test_cache_revalidates_actual_wav_even_if_byte_hash_is_updated(fixture):
    cache, key, request, _, _ = fixture
    result = publish(fixture)
    wav = Path(result["path"])
    body = bytearray(wav.read_bytes())
    body[24:28] = struct.pack("<I", 22_050)
    wav.write_bytes(body)
    manifest = cache.root / key / "manifest.json"
    value = storage.read_json(manifest)
    value["artifact_sha256"] = storage.file_sha256(wav)
    storage.atomic_json(manifest, value)
    assert cache.load(key, request) is None


def test_cache_bad_audio_is_not_published(fixture):
    cache, key, request, wav, metadata = fixture
    wav.write_bytes(b"not a WAV")
    with cache.lock(key):
        with pytest.raises(ValidationError):
            cache.publish(key, request, wav, metadata)
    assert not (cache.root / key).exists()
    assert cache.load(key, request) is None


def test_cache_rejects_large_source_before_copying(fixture):
    cache, key, request, wav, metadata = fixture
    with wav.open("wb") as stream:
        stream.truncate(storage._MAX_PCM_BYTES + 1)
    with cache.lock(key):
        with pytest.raises(ValidationError, match="size limit"):
            cache.publish(key, request, wav, metadata)
    assert not (cache.root / ".staging" / key / "audio.wav").exists()
    assert cache.load(key, request) is None


def test_cache_replaces_corrupt_entry_preserving_unrelated_files(fixture):
    cache, key, request, _, _ = fixture
    result = publish(fixture)
    Path(result["path"]).write_bytes(b"broken")
    keep = cache.root / "user-note.txt"
    keep.write_text("retain")
    refreshed = publish(fixture)
    assert refreshed == result
    assert cache.load(key, request) == result
    assert keep.read_text() == "retain"


def test_staging_is_never_a_hit_and_only_its_key_lock_reconciles_it(fixture):
    cache, key, request, _, metadata = fixture
    staging = cache.root / ".staging" / key
    staging.mkdir()
    (staging / "audio.wav").write_bytes(b"interrupted copy")
    storage.atomic_json(staging / "manifest.json", {"status": "partial", "metadata": metadata})
    unrelated = cache.root / ".staging" / "user-owned"
    unrelated.mkdir()
    (unrelated / "keep").write_text("keep")
    assert cache.load(key, request) is None
    assert staging.exists()
    with cache.lock(key):
        assert not staging.exists()
    assert (unrelated / "keep").read_text() == "keep"
    assert publish(fixture)["cache_key"] == key


def test_interrupted_directory_publication_never_produces_partial_hit(fixture, monkeypatch):
    cache, key, request, wav, metadata = fixture
    real_replace = storage.os.replace

    def fail_directory(source, destination):
        if Path(source).is_dir():
            raise OSError("interrupted cache publication")
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_directory)
        with cache.lock(key):
            with pytest.raises(StudioError):
                cache.publish(key, request, wav, metadata)
    assert cache.load(key, request) is None
    assert (cache.root / ".staging" / key / "manifest.json").exists()
    assert publish(fixture)["cache_key"] == key


def test_failure_after_rename_leaves_only_complete_reusable_cache(fixture, monkeypatch):
    cache, key, request, wav, metadata = fixture
    real_sync = storage._sync_directory

    def fail_after_publish(path):
        if path == cache.root:
            raise OSError("simulated interruption after rename")
        return real_sync(path)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_sync_directory", fail_after_publish)
        with cache.lock(key):
            with pytest.raises(StudioError):
                cache.publish(key, request, wav, metadata)
    cached = cache.load(key, request)
    assert cached is not None
    assert cached["metadata"] == metadata
    assert publish(fixture) == cached


@pytest.mark.parametrize("location", ["entry", "staging"])
def test_symlink_trees_are_never_reused_or_removed(fixture, tmp_path, location):
    cache, key, request, wav, metadata = fixture
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("important")
    target = cache.root / key if location == "entry" else cache.root / ".staging" / key
    target.mkdir()
    (target / "audio.wav").symlink_to(outside, target_is_directory=True)
    assert cache.load(key, request) is None
    with pytest.raises(StudioError):
        with cache.lock(key):
            cache.publish(key, request, wav, metadata)
    assert (outside / "keep").read_text() == "important"
    assert (target / "audio.wav").is_symlink()


def test_unknown_files_inside_entry_are_preserved(fixture):
    cache, key, _, _, _ = fixture
    directory = cache.root / key
    directory.mkdir()
    (directory / "user-note").write_text("retain")
    with pytest.raises(StudioError):
        publish(fixture)
    assert (directory / "user-note").read_text() == "retain"


def test_boundaries_cannot_be_fabricated_by_metadata_changes(fixture):
    cache, key, request, wav, metadata = fixture
    broken = copy.deepcopy(metadata)
    broken["boundaries"][0]["duration_ticks"] = True
    with cache.lock(key):
        with pytest.raises(ValidationError):
            cache.publish(key, request, wav, broken)
    assert cache.load(key, request) is None


def test_canonical_request_types_must_match_even_when_python_equality_does(fixture):
    cache, _, request, wav, metadata = fixture
    request["enabled"] = 1
    key = storage.canonical_key(request)
    with cache.lock(key):
        cache.publish(key, request, wav, metadata)
    manifest = cache.root / key / "manifest.json"
    value = json.loads(manifest.read_text())
    value["request"]["enabled"] = True
    storage.atomic_json(manifest, value)
    assert cache.load(key, request) is None


@pytest.mark.parametrize("model", [None, {"id": "fixture", "version": "1", "sha256": "a" * 64}])
def test_public_metadata_validator_matches_explicit_model_identity(fixture, model):
    cache, _, request, wav, metadata = fixture
    request["engine"]["model"] = model
    metadata["model"] = copy.deepcopy(model)
    storage.validate_chunk_metadata(metadata, request, wav)
    key = storage.canonical_key(request)
    with cache.lock(key):
        assert cache.publish(key, request, wav, metadata)["metadata"]["model"] == model
    manifest_path = cache.root / key / "manifest.json"
    manifest = storage.read_json(manifest_path)
    manifest["metadata"]["model"] = {"id": "different", "version": "2"}
    storage.atomic_json(manifest_path, manifest)
    assert cache.load(key, request) is None


def test_public_metadata_validator_rejects_wrong_model_before_publication(fixture):
    cache, _, request, wav, metadata = fixture
    request["engine"]["model"] = {"id": "fixture", "version": "1"}
    metadata["model"] = {"id": "fixture", "version": "2"}
    key = storage.canonical_key(request)
    with pytest.raises(ValidationError, match="model metadata"):
        storage.validate_chunk_metadata(metadata, request, wav)
    with cache.lock(key):
        with pytest.raises(ValidationError, match="model metadata"):
            cache.publish(key, request, wav, metadata)
    assert not (cache.root / key).exists()
