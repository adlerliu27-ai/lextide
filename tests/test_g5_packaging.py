"""Entrypoint routing and build safety, without claiming a packaged GUI test."""

import importlib.util
from pathlib import Path

import pytest


def load_file(relative):
    path = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location("g5_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], ["gui"]),
        (["--data-dir", "/safe path"], ["gui", "--data-dir", "/safe path"]),
        (["--cli", "--version"], ["--version"]),
    ],
)
def test_launcher_routes_without_working_directory_dependency(monkeypatch, arguments, expected):
    from lextide import cli

    calls = []
    monkeypatch.setattr(cli, "main", lambda args: calls.append(args) or 0)
    assert load_file("packaging/macos/launcher.py").main(arguments) == 0
    assert calls == [expected]


def test_worker_dispatch_precedes_gui(monkeypatch):
    from lextide import cli
    from lextide.desktop import worker

    calls = []
    monkeypatch.setattr(cli, "main", lambda _: pytest.fail("GUI initialized for worker"))
    monkeypatch.setattr(worker, "main", lambda args: calls.append(args) or 7)
    assert load_file("packaging/macos/launcher.py").main(["--desktop-worker", "/request"]) == 7
    assert calls == [["/request"]]


def test_supervisor_dispatch_precedes_gui(monkeypatch):
    from lextide import cli
    from lextide.longform import process

    calls = []
    monkeypatch.setattr(cli, "main", lambda _: pytest.fail("GUI initialized for supervisor"))
    monkeypatch.setattr(process, "main", lambda args: calls.append(args) or 0)
    assert (
        load_file("packaging/macos/launcher.py").main(["--supervise", "42", "/usr/bin/true"]) == 0
    )
    assert calls == [["42", "/usr/bin/true"]]


def test_inventory_rejects_links_outside_bundle(tmp_path):
    pytest.importorskip("packaging.requirements")
    build = load_file("scripts/build_macos.py")
    app = tmp_path / "App.app"
    app.mkdir()
    (app / "bad-link").symlink_to(tmp_path / "private-source")
    with pytest.raises(SystemExit, match="escapes"):
        build.file_inventory(app)


def test_inventory_hashes_content_and_preserves_internal_links(tmp_path):
    pytest.importorskip("packaging.requirements")
    build = load_file("scripts/build_macos.py")
    app = tmp_path / "App.app"
    app.mkdir()
    (app / "data").write_bytes(b"abc")
    (app / "link").symlink_to("data")
    rows = build.file_inventory(app)
    assert rows[0]["sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert rows[1] == {"path": "link", "symlink": "data"}
