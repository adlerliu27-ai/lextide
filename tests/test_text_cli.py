import json

import pytest

from lextide.cli import main
from lextide.errors import InputError
from lextide.text import prepare_text


def test_original_bytes_and_traceable_cleanup(tmp_path):
    source = tmp_path / "source.txt"
    raw = b"\xef\xbb\xbf  Hello.\r\nNext\tline.\r\n"
    source.write_bytes(raw)
    prepared = prepare_text(input_path=source)
    assert prepared.original_bytes == raw
    assert source.read_bytes() == raw
    assert prepared.tts_text == "Hello.\nNext line."
    reconstructed = prepared.original_text
    for change in reversed(prepared.changes):
        start, end = change["original_span"]
        assert reconstructed[start:end] == change["before"]
        reconstructed = reconstructed[:start] + change["after"] + reconstructed[end:]
    assert reconstructed == prepared.tts_text


def test_no_semantic_rewriting():
    text = 'R&D and/or Dr. Smith. 中文对照。 More English. "A&B"'
    assert prepare_text(text=text).tts_text == text


@pytest.mark.parametrize("text", ["", " \t\n", "123", "Hi\x00there", "Hi\u200bthere", "A" * 3001])
def test_invalid_text(text):
    with pytest.raises(InputError):
        prepare_text(text=text)


def test_bad_encoding(tmp_path):
    source = tmp_path / "bad.txt"
    source.write_bytes(b"\xffHello")
    with pytest.raises(InputError):
        prepare_text(input_path=source)


def test_preview_and_missing_consent_are_offline(tmp_path, capsys):
    assert main(["preview", "--text", "Hello.", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert len(preview["text_sha256"]) == 64
    out = tmp_path / "not-created"
    assert main(["synthesize", "--text", "Hello.", "--out-dir", str(out)]) == 3
    assert not out.exists()
    assert "ONLINE_NOT_AUTHORIZED" in capsys.readouterr().err


def test_stale_confirmation_cannot_send(tmp_path, capsys):
    assert (
        main(
            [
                "synthesize",
                "--text",
                "Changed.",
                "--out-dir",
                str(tmp_path),
                "--allow-online",
                "--confirm-text",
                "0" * 64,
            ]
        )
        == 3
    )
    assert "TEXT_NOT_CONFIRMED" in capsys.readouterr().err


def test_bad_rate_before_network(tmp_path, capsys):
    assert (
        main(
            [
                "synthesize",
                "--text",
                "Hello.",
                "--out-dir",
                str(tmp_path),
                "--allow-online",
                "--rate=-99%",
                "--confirm-text",
                "0" * 64,
            ]
        )
        == 2
    )
    assert "INVALID_INPUT" in capsys.readouterr().err
