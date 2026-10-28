"""G7 sidecars remain independent from original output and fail closed on mismatch."""

import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from lextide.desktop import bilingual
from lextide.desktop.bilingual import (
    SubtitleSource,
    TranslationStore,
    import_translation,
    validate_translation_text,
)
from lextide.desktop.service import parse_srt
from lextide.errors import StudioError, ValidationError
from lextide.longform import storage

ENGLISH = (
    "1\n00:00:00,123 --> 00:00:01,456\nFirst original sentence.\n\n"
    "2\n00:00:01,900 --> 00:00:03,007\nSecond sentence\nwith two lines.\n"
)
CHINESE = (
    "1\n00:00:00,123 --> 00:00:01,456\n第一句，保留 5′ RNA。\n\n"
    "2\n00:00:01,900 --> 00:00:03,007\n第二句\n分两行。\n"
)
TEXTS = ("第一句，保留 5′ RNA。", "第二句\n分两行。")


def sha(body):
    return hashlib.sha256(body).hexdigest()


@pytest.fixture
def originals(tmp_path):
    job = tmp_path / "old-job"
    job.mkdir()
    srt = job / "subtitles.srt"
    srt.write_bytes(ENGLISH.encode())
    (job / "audio.mp3").write_bytes(b"original immutable audio")
    (job / "manifest.json").write_bytes(b'{"old":true}')
    before = {path.name: path.read_bytes() for path in job.iterdir()}
    yield job, srt
    assert {path.name: path.read_bytes() for path in job.iterdir()} == before


@pytest.fixture
def source(originals):
    _, path = originals
    return SubtitleSource.from_verified({"srt_path": str(path), "cues": parse_srt(ENGLISH)})


@pytest.fixture
def store(tmp_path):
    return TranslationStore(tmp_path / "desktop")


def sidecar(store, source):
    return store.root / "translations" / f"{source.srt_sha256}.zh-CN.json"


def import_file(tmp_path, body=CHINESE, suffix=".srt", encoding="utf-8"):
    path = tmp_path / f"translation{suffix}"
    path.write_bytes(body.encode(encoding))
    return path


def test_verified_source_preserves_exact_identity_and_is_immutable(source):
    assert source.cues == tuple(parse_srt(ENGLISH))
    assert source.cues[0]["start_ms"] == 123
    assert source.cues[1]["start_ms"] == 1900
    assert source.srt_sha256 == sha(ENGLISH.encode())
    source.assert_current()
    with pytest.raises(TypeError):
        source.cues[0]["text"] = "modified"
    with pytest.raises(FrozenInstanceError):
        source.srt_sha256 = "a" * 64


def test_verified_source_copies_cues_from_ui(originals):
    _, path = originals
    cues = parse_srt(ENGLISH)
    source = SubtitleSource.from_verified({"srt_path": str(path), "cues": cues})
    cues[0]["text"] = "mutated UI state"
    assert source.cues[0]["text"] == "First original sentence."


@pytest.mark.parametrize(
    "field,value",
    [
        ("text", "different source"),
        ("start_ms", 124),
        ("index", True),
        ("end_ms", 100),
    ],
)
def test_verified_source_rejects_changed_snapshot(originals, field, value):
    _, path = originals
    cues = parse_srt(ENGLISH)
    cues[0][field] = value
    with pytest.raises(ValidationError):
        SubtitleSource.from_verified({"srt_path": str(path), "cues": cues})


def test_load_absence_has_no_filesystem_side_effects(store, source):
    assert store.load(source) is None
    assert store.current_revision(source) is None
    assert not store.root.exists()


def test_save_load_records_hashes_timing_and_partial_manual_edit(store, source):
    first = store.save(source, (TEXTS[0], None), expected_revision=None, origin="manual")
    path = sidecar(store, source)
    assert first.texts == (TEXTS[0], None)
    assert first.revision == sha(path.read_bytes()) == store.current_revision(source)
    assert store.load(source) == first
    saved = json.loads(path.read_bytes())
    assert saved["source_srt_sha256"] == source.srt_sha256
    assert saved["cue_digest"] == source.cue_digest
    assert saved["cues"][1] == {
        "index": 2,
        "start_ms": 1900,
        "end_ms": 3007,
        "source_text_sha256": sha(source.cues[1]["text"].encode()),
        "text": None,
    }
    updated = store.save(source, TEXTS, expected_revision=first.revision, origin="manual")
    assert updated.texts == TEXTS
    assert updated.revision != first.revision
    assert store.load(source) == updated
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_srt_import_exact_cues_and_bom(tmp_path, source, encoding):
    path = import_file(tmp_path, encoding=encoding)
    assert import_translation(path, source) == TEXTS


@pytest.mark.parametrize(
    "body",
    [
        CHINESE.replace("00:00:00,123", "00:00:00,124"),
        CHINESE.replace("00:00:03,007", "00:00:03,008"),
        CHINESE.replace("\n2\n", "\n1\n"),
        CHINESE.replace("\n2\n", "\n3\n"),
        CHINESE.replace("00:00:01,900", "00:00:01,000"),
        CHINESE.replace("00:00:00,123", "00:60:00,123"),
        CHINESE.replace("00:00:00,123", "00:00:60,123"),
        CHINESE.split("\n\n")[0],
        CHINESE + "\n3\n00:00:04,000 --> 00:00:05,000\n多余。\n",
        "\n\n".join(reversed(CHINESE.strip().split("\n\n"))),
        CHINESE.replace("第一句", "第一\x00句"),
    ],
)
def test_srt_import_rejects_mismatches(tmp_path, source, body):
    with pytest.raises(ValidationError):
        import_translation(import_file(tmp_path, body), source)


@pytest.mark.parametrize("control", ["\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029"])
def test_srt_import_rejects_controls_before_parser_can_normalize_them(tmp_path, source, control):
    body = CHINESE.replace("第一句", f"第一{control}句")
    with pytest.raises(ValidationError):
        import_translation(import_file(tmp_path, body), source)


@pytest.mark.parametrize(
    "body", ["第一句。\n第二句。", "第一句。\n第二句。\n", "第一句。\r\n第二句。\r\n"]
)
def test_txt_import_preserves_one_physical_line_per_cue(tmp_path, source, body):
    assert import_translation(import_file(tmp_path, body, ".txt", "utf-8-sig"), source) == (
        "第一句。",
        "第二句。",
    )


@pytest.mark.parametrize(
    "body",
    [
        "",
        "第一句。",
        "第一句。\n第二句。\n第三句。",
        "\n第一句。\n第二句。",
        "第一句。\n\n第二句。",
        "第一句。\n第二句。\n\n",
        "第一句。\n \n第二句。",
        "第一句。\n   ",
        "第一句。\u2028第二句。",
        "第一句。\x85第二句。",
        "第一句。\n第\x00二句。",
    ],
)
def test_txt_rejects_empty_records_and_nonphysical_separators(tmp_path, source, body):
    with pytest.raises(ValidationError):
        import_translation(import_file(tmp_path, body, ".txt"), source)


def test_txt_semantic_order_is_not_claimed_to_be_verified(tmp_path, source):
    # Mechanical validation intentionally cannot guess semantic order or language.
    path = import_file(tmp_path, "Second meaning\nFirst meaning\n", ".txt")
    assert import_translation(path, source) == ("Second meaning", "First meaning")


@pytest.mark.parametrize(
    "text", ["", " \n ", "a\x00b", "a\tb", "a\rb", "a\x1bb", "a\u202eb", "a\ud800b"]
)
def test_edit_rejects_blank_and_control_text(store, source, text):
    with pytest.raises(ValidationError):
        validate_translation_text(text)
    with pytest.raises(ValidationError):
        store.save(source, (text, None), expected_revision=None, origin="manual")
    assert not store.root.exists()


def test_text_validation_allows_punctuation_names_technical_terms():
    for text in ("—（？）", "Marie Curie", "5′ RNA-seq 的 QC", "第一行\n第二行", "Douglas Adams"):
        assert validate_translation_text(text) == text


@pytest.mark.parametrize(
    "texts", ["不是列表", (), (None,), (None, None, None), (3, None), (False, None)]
)
def test_save_rejects_bad_count_and_types(store, source, texts):
    with pytest.raises(ValidationError):
        store.save(source, texts, expected_revision=None, origin="manual")


def test_stale_revision_cannot_overwrite_current_translation(store, source):
    first = store.save(source, TEXTS, expected_revision=None, origin="import:srt")
    second = store.save(
        source, ("第二次保存", None), expected_revision=first.revision, origin="manual"
    )
    before = sidecar(store, source).read_bytes()
    for stale in (None, first.revision):
        with pytest.raises(StudioError) as exc:
            store.save(source, TEXTS, expected_revision=stale, origin="manual")
        assert exc.value.code == "TRANSLATION_CONFLICT"
    assert sidecar(store, source).read_bytes() == before
    assert store.load(source) == second


def test_source_byte_change_rejects_load_import_and_save(tmp_path, store):
    path = import_file(tmp_path, ENGLISH)
    source = SubtitleSource.from_verified({"srt_path": str(path), "cues": parse_srt(ENGLISH)})
    document = store.save(source, TEXTS, expected_revision=None, origin="manual")
    before = sidecar(store, source).read_bytes()
    path.write_bytes((ENGLISH + "\n").encode())
    for operation in (
        source.assert_current,
        lambda: store.load(source),
        lambda: import_translation(path, source),
        lambda: store.save(source, TEXTS, expected_revision=document.revision, origin="manual"),
    ):
        with pytest.raises(StudioError) as exc:
            operation()
        assert exc.value.code == "SUBTITLE_SOURCE_CHANGED"
    assert sidecar(store, source).read_bytes() == before


def test_source_is_reread_immediately_before_atomic_write(tmp_path, store, monkeypatch):
    path = import_file(tmp_path, ENGLISH)
    source = SubtitleSource.from_verified({"srt_path": str(path), "cues": parse_srt(ENGLISH)})
    original = store.current_revision

    def change_after_revision(snapshot):
        revision = original(snapshot)
        path.write_bytes((ENGLISH + "\n").encode())
        return revision

    monkeypatch.setattr(store, "current_revision", change_after_revision)
    with pytest.raises(StudioError) as exc:
        store.save(source, TEXTS, expected_revision=None, origin="manual")
    assert exc.value.code == "SUBTITLE_SOURCE_CHANGED"
    assert not sidecar(store, source).exists()


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"a":1,"a":2}', b'{"a":NaN}', b"\xff"])
def test_corrupt_json_can_only_be_replaced_with_exact_confirmed_revision(store, source, body):
    path = sidecar(store, source)
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    with pytest.raises(ValidationError):
        store.load(source)
    assert path.read_bytes() == body
    revision = store.current_revision(source)
    assert revision == sha(body)
    with pytest.raises(StudioError) as exc:
        store.save(source, TEXTS, expected_revision=None, origin="manual")
    assert exc.value.code == "TRANSLATION_CONFLICT"
    saved = store.save(source, TEXTS, expected_revision=revision, origin="manual")
    assert store.load(source) == saved


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema_version=True),
        lambda value: value.update(language="en"),
        lambda value: value.update(source_srt_sha256="a" * 64),
        lambda value: value.update(cue_digest="b" * 64),
        lambda value: value.update(unexpected="field"),
        lambda value: value["cues"].reverse(),
        lambda value: value["cues"][0].update(index=True),
        lambda value: value["cues"][0].update(start_ms=124),
        lambda value: value["cues"][0].update(source_text_sha256="c" * 64),
        lambda value: value["cues"][0].update(text=""),
        lambda value: value["cues"][0].update(text="bad\x00text"),
        lambda value: value["cues"].pop(),
        lambda value: value.update(origin=""),
    ],
)
def test_sidecar_schema_and_cue_identity_are_strict(store, source, mutation):
    store.save(source, TEXTS, expected_revision=None, origin="manual")
    path = sidecar(store, source)
    value = json.loads(path.read_bytes())
    mutation(value)
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        store.load(source)
    assert path.read_bytes() == before


def test_failed_atomic_replace_preserves_previous_file(store, source, monkeypatch):
    first = store.save(source, TEXTS, expected_revision=None, origin="manual")
    path = sidecar(store, source)
    before = path.read_bytes()

    def fail_replace(*_args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(StudioError) as exc:
        store.save(source, ("新内容", None), expected_revision=first.revision, origin="manual")
    assert exc.value.code == "STORAGE_IO"
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))
    assert store.load(source) == first


def test_competing_lock_rejects_save(store, source):
    lock = sidecar(store, source).parent / ".locks" / f"{source.srt_sha256}.lock"
    with storage.exclusive_lock(lock):
        with pytest.raises(StudioError) as exc:
            store.save(source, TEXTS, expected_revision=None, origin="manual")
    assert exc.value.code == "TASK_BUSY"
    assert not sidecar(store, source).exists()


@pytest.mark.parametrize("location", ["root", "translations", "sidecar", "lock"])
def test_store_refuses_symlinks_without_touching_target(tmp_path, source, location):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "precious.txt"
    victim.write_bytes(b"immutable external data")
    root = tmp_path / "desktop"
    if location == "root":
        root.symlink_to(outside, target_is_directory=True)
        with pytest.raises(StudioError):
            TranslationStore(root)
    else:
        root.mkdir()
        translations = root / "translations"
        if location == "translations":
            translations.symlink_to(outside, target_is_directory=True)
        else:
            translations.mkdir()
            if location == "sidecar":
                (translations / f"{source.srt_sha256}.zh-CN.json").symlink_to(victim)
            else:
                (translations / ".locks").mkdir()
                (translations / ".locks" / f"{source.srt_sha256}.lock").symlink_to(victim)
        store = TranslationStore(root)
        with pytest.raises(StudioError):
            store.save(source, TEXTS, expected_revision=None, origin="manual")
        if location != "lock":
            with pytest.raises(StudioError):
                store.load(source)
            with pytest.raises(StudioError):
                store.current_revision(source)
    assert victim.read_bytes() == b"immutable external data"
    assert list(outside.iterdir()) == [victim]


def test_import_and_source_refuse_symlink_files_and_ancestors(tmp_path, source):
    path = import_file(tmp_path)
    link = tmp_path / "link.srt"
    link.symlink_to(path)
    directory = tmp_path / "linked-directory"
    directory.symlink_to(source.path.parent, target_is_directory=True)
    for candidate in (link, directory / source.path.name):
        with pytest.raises(StudioError):
            import_translation(candidate, source)
        with pytest.raises(StudioError):
            SubtitleSource.from_verified({"srt_path": str(candidate), "cues": source.cues})


def test_oversize_inputs_and_sidecars_are_bounded(tmp_path, source, store, monkeypatch):
    monkeypatch.setattr(bilingual, "MAX_TRANSLATION_TEXT_BYTES", 8)
    with pytest.raises(ValidationError):
        validate_translation_text("超过八字节")
    monkeypatch.setattr(bilingual, "MAX_TRANSLATION_TEXT_BYTES", 16 * 1024)
    monkeypatch.setattr(bilingual, "MAX_SUBTITLE_BYTES", len(ENGLISH.encode()))
    path = import_file(tmp_path, "超" * 200, ".txt")
    with pytest.raises(ValidationError):
        import_translation(path, source)
    monkeypatch.setattr(bilingual, "MAX_TRANSLATION_BYTES", 20)
    with pytest.raises(ValidationError):
        store.save(source, TEXTS, expected_revision=None, origin="manual")
    path = sidecar(store, source)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"a" * 21)
    with pytest.raises(ValidationError):
        store.load(source)
    with pytest.raises(ValidationError):
        store.current_revision(source)


def test_invalid_utf8_and_unsupported_extension_rejected(tmp_path, source):
    path = import_file(tmp_path, "第一句\n第二句", ".txt", "gb18030")
    with pytest.raises(ValidationError):
        import_translation(path, source)
    with pytest.raises(ValidationError):
        import_translation(import_file(tmp_path, suffix=".json"), source)
