import hashlib
import html

import pytest

from lextide.errors import InputError
from lextide.text import prepare_text


def test_document_cleanup_preserves_original_and_reversible_character_mapping(tmp_path):
    from lextide.longform.document import prepare_document

    original = "\ufeff \r\nDr. Smith\tread 3.14.\r\nNext 中文 line.\r  "
    path = tmp_path / "document.txt"
    path.write_bytes(original.encode("utf-8"))
    prepared = prepare_document(input_path=path)
    assert prepared.original_text == original
    assert prepared.original_bytes == path.read_bytes()
    assert prepared.tts_text == "Dr. Smith read 3.14.\nNext 中文 line."
    assert prepared.tts_text == prepare_text(text=original).tts_text
    reconstructed = original
    last_original_end = last_new_end = 0
    for change in prepared.changes:
        start, end = change["original_span"]
        new_start, new_end = change["new_span"]
        assert start >= last_original_end and new_start >= last_new_end
        assert original[start:end] == change["before"]
        assert prepared.tts_text[new_start:new_end] == change["after"]
        assert original[last_original_end:start] == prepared.tts_text[last_new_end:new_start]
        last_original_end, last_new_end = end, new_end
    assert original[last_original_end:] == prepared.tts_text[last_new_end:]
    for change in reversed(prepared.changes):
        start, end = change["original_span"]
        reconstructed = reconstructed[:start] + change["after"] + reconstructed[end:]
    assert reconstructed == prepared.tts_text


def _assert_lossless_plan(prepared, plan):
    text = prepared.tts_text
    assert "".join(segment["text"] for segment in plan["segments"]) == text
    assert "".join(chunk["text"] for chunk in plan["chunks"]) == text
    segments = {segment["segment_id"]: segment for segment in plan["segments"]}
    assert len(segments) == len(plan["segments"])
    position = 0
    for index, chunk in enumerate(plan["chunks"]):
        assert chunk["index"] == index
        assert chunk["start"] == position
        assert chunk["text"] == text[chunk["start"] : chunk["end"]]
        assert chunk["text_sha256"] == hashlib.sha256(chunk["text"].encode()).hexdigest()
        assert any(char.isalnum() for char in chunk["text"])
        escaped = (
            html.escape(chunk["text"], quote=False)
            if plan["engine_id"] == "edge-tts"
            else chunk["text"]
        )
        assert len(escaped.encode()) <= plan["max_bytes"]
        piece_position = 0
        assert chunk["segment_ids"] == [piece["segment_id"] for piece in chunk["pieces"]]
        for piece in chunk["pieces"]:
            segment = segments[piece["segment_id"]]
            assert segment["start"] <= piece["start"] < piece["end"] <= segment["end"]
            assert piece["chunk_start"] == piece_position
            assert piece["start"] - chunk["start"] == piece["chunk_start"]
            assert piece["end"] - chunk["start"] == piece["chunk_end"]
            assert (
                text[piece["start"] : piece["end"]]
                == chunk["text"][piece["chunk_start"] : piece["chunk_end"]]
            )
            piece_position = piece["chunk_end"]
        assert piece_position == len(chunk["text"])
        position = chunk["end"]
    assert position == len(text)


def test_paragraph_sentence_slices_and_chunk_piece_mapping_are_deterministic():
    from lextide.longform.document import RULE_VERSION, chunk_document, prepare_document

    prepared = prepare_document(
        text="Dr. J. R. Smith paid 3.14 in the U.S. office. Next sentence!\n\n"
        "Prof. Jane arrived, e.g. at noon. Last line."
    )
    plan = chunk_document(prepared, "edge-tts", max_bytes=65)
    assert plan["rule_version"] == RULE_VERSION
    assert [segment["text"] for segment in plan["segments"]] == [
        "Dr. J. R. Smith paid 3.14 in the U.S. office. ",
        "Next sentence!\n\n",
        "Prof. Jane arrived, e.g. at noon. ",
        "Last line.",
    ]
    assert [segment["paragraph_index"] for segment in plan["segments"]] == [0, 0, 1, 1]
    assert [chunk["text"] for chunk in plan["chunks"]] == [
        "Dr. J. R. Smith paid 3.14 in the U.S. office. Next sentence!\n\n",
        "Prof. Jane arrived, e.g. at noon. Last line.",
    ]
    _assert_lossless_plan(prepared, plan)
    assert chunk_document(prepared, "edge-tts", max_bytes=65) == plan


@pytest.mark.parametrize("engine", ["edge-tts", "kokoro"])
def test_oversized_sentence_and_unicode_token_keep_every_character(engine):
    from lextide.longform.document import chunk_document, prepare_document

    prepared = prepare_document(
        text="Long sentence with words & literal &amp; <tags> " * 30 + "café中文🙂" * 80 + ". Tail."
    )
    plan = chunk_document(prepared, engine, max_bytes=73)
    assert len(plan["chunks"]) > 20
    assert len(
        {piece["segment_id"] for chunk in plan["chunks"] for piece in chunk["pieces"]}
    ) == len(plan["segments"])
    _assert_lossless_plan(prepared, plan)
    assert plan == chunk_document(prepared, engine, max_bytes=73)


def test_edge_counts_escaped_bytes_without_decoding_literal_entities():
    from lextide.longform.document import chunk_document, prepare_document

    prepared = prepare_document(text="A&B C&D E&F G&H")
    edge = chunk_document(prepared, "edge-tts", max_bytes=10)
    kokoro = chunk_document(prepared, "kokoro", max_bytes=10)
    assert [chunk["text"] for chunk in edge["chunks"]] == ["A&B ", "C&D ", "E&F ", "G&H"]
    assert [chunk["text"] for chunk in kokoro["chunks"]] == ["A&B C&D ", "E&F G&H"]
    _assert_lossless_plan(prepared, edge)
    _assert_lossless_plan(prepared, kokoro)


def test_numeric_standalone_and_long_whitespace_do_not_make_empty_synthesis_requests():
    from lextide.longform.document import chunk_document, prepare_document

    prepared = prepare_document(text="Alpha.\n\n12345.\n\nBeta." + " " * 40 + "Next.")
    plan = chunk_document(prepared, "kokoro", max_bytes=30)
    _assert_lossless_plan(prepared, plan)
    tail = prepare_document(text="Ab" + "." * 8)
    tail_plan = chunk_document(tail, "kokoro", max_bytes=9)
    assert [chunk["text"] for chunk in tail_plan["chunks"]] == ["A", "b........"]
    _assert_lossless_plan(tail, tail_plan)
    with pytest.raises(InputError, match="有效文本"):
        chunk_document(prepare_document(text="A" + " " * 30 + "B"), "kokoro", max_bytes=10)


def test_document_bounds_and_engine_defaults():
    from lextide.longform.document import chunk_document, prepare_document

    prepared = prepare_document(text="A" * (1024 * 1024))
    assert len(prepared.original_bytes) == 1024 * 1024
    assert prepared.changes == []
    edge = chunk_document(prepared, "edge-tts")
    assert edge["max_bytes"] == 2800
    assert len(edge["chunks"]) == 375
    _assert_lossless_plan(prepared, edge)
    assert chunk_document(prepare_document(text="Hello."), "kokoro")["max_bytes"] == 1200
    with pytest.raises(InputError, match="1 MiB"):
        prepare_document(text="A" * (1024 * 1024 + 1))
    with pytest.raises(InputError, match="2000"):
        chunk_document(prepare_document(text="A" * 2001), "kokoro", max_bytes=1)
    assert (
        len(chunk_document(prepare_document(text="A" * 2000), "kokoro", max_bytes=1)["chunks"])
        == 2000
    )


def test_extreme_nonterminal_punctuation_is_rejected_without_regex_backtracking():
    import signal

    from lextide.longform.document import chunk_document, prepare_document

    def timeout(*_args):
        raise AssertionError("Bounded 200 KiB text exceeded the 5-second scan guard")

    # The supported host is macOS. This guards against accidental quadratic
    # regex backtracking, not ordinary speed variation (normal runtime < 0.1s).
    previous = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 5)
    try:
        with pytest.raises(InputError, match="有效文本"):
            chunk_document(prepare_document(text="A" + "." * 200_000 + "B"), "kokoro")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize("value", [0, -1, 3001, True, 1.5, "100"])
def test_invalid_chunk_byte_limits_are_input_errors(value):
    from lextide.longform.document import chunk_document, prepare_document

    with pytest.raises(InputError):
        chunk_document(prepare_document(text="Hello."), "edge-tts", max_bytes=value)


@pytest.mark.parametrize(
    "text", ["", "\t\r\n", "123", "Hi\x00there", "Hi\u200bthere", "Hi\ufeffthere", "Hi\ud800there"]
)
def test_invalid_document_text(text):
    from lextide.longform.document import prepare_document

    with pytest.raises(InputError):
        prepare_document(text=text)


def test_invalid_document_sources_are_input_errors(tmp_path):
    from lextide.longform.document import chunk_document, prepare_document

    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xffHello")
    with pytest.raises(InputError, match="UTF-8"):
        prepare_document(input_path=path)
    for kwargs in (
        {},
        {"text": "A", "input_path": path},
        {"input_path": tmp_path},
        {"input_path": tmp_path / "missing.txt"},
        {"text": 123},
    ):
        with pytest.raises(InputError):
            prepare_document(**kwargs)
    with pytest.raises(InputError):
        chunk_document(prepare_document(text="Hello."), "unknown")
