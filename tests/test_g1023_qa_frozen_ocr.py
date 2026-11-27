"""Safety, boundary, activation, and AppleScript-free checks for scripts/qa_frozen_ocr.py."""

from __future__ import annotations

import hashlib
import importlib.util
import plistlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/qa_frozen_ocr.py"


def _load_qa_module():
    spec = importlib.util.spec_from_file_location("qa_frozen_ocr_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qa_script_contains_no_applescript_or_swift_execution():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "/usr/bin/osascript" not in source
    assert "System Events" not in source
    assert "tell application" not in source
    assert ".swift" not in source or "former scripts/qa_frozen_ocr.swift" in source
    assert "swiftc" not in source


def test_validate_bundle_rejects_installed_applications_and_invalid_bundles(tmp_path):
    qa = _load_qa_module()
    run_dir = tmp_path / "run-1"

    with pytest.raises(qa.QABlockedError, match="outside installed Applications"):
        qa.validate_bundle_and_run_dir(Path("/Applications/Lextide.app"), run_dir)

    with pytest.raises(qa.QABlockedError, match="outside installed Applications"):
        qa.validate_bundle_and_run_dir(Path.home() / "Applications/Lextide.app", run_dir)

    wrong = tmp_path / "Other.app"
    wrong.mkdir()
    with pytest.raises(qa.QABlockedError, match="development Lextide.app"):
        qa.validate_bundle_and_run_dir(wrong, run_dir)

    dev_app = tmp_path / "dist/Lextide.app"
    binary = dev_app / "Contents/MacOS/Lextide"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    plist_path = dev_app / "Contents/Info.plist"
    with plist_path.open("wb") as stream:
        plistlib.dump(
            {"CFBundleShortVersionString": "0.10.0", "LextideBuildCommit": "abc123"},
            stream,
        )
    bundle, out_dir, info = qa.validate_bundle_and_run_dir(dev_app, run_dir)
    assert bundle == dev_app.resolve()
    assert out_dir == run_dir.absolute()
    assert info["LextideBuildCommit"] == "abc123"


def test_render_synthetic_fixture_creates_png_and_refuses_overwrite(tmp_path, capsys):
    pytest.importorskip("PySide6.QtGui")
    qa = _load_qa_module()
    target = tmp_path / "english.png"
    info = qa.render_synthetic_fixture(target)
    raw = target.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert info["sha256"] == hashlib.sha256(raw).hexdigest()
    assert info["text"] == qa.FIXTURE_TEXT
    assert capsys.readouterr().out.strip()

    with pytest.raises(qa.QABlockedError, match="already exists"):
        qa.render_synthetic_fixture(target)


def test_verify_unchecked_review_gate_states(monkeypatch):
    qa = _load_qa_module()
    fake_as = SimpleNamespace(
        kAXCheckBoxRole="AXCheckBox",
        kAXButtonRole="AXButton",
        kAXTextAreaRole="AXTextArea",
        kAXValueAttribute="AXValue",
        kAXEnabledAttribute="AXEnabled",
    )
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake_as)

    box_sentinel = object()
    btn_sentinel = object()
    area_sentinel = object()
    fit_sentinel = object()

    def fake_find(_pid, _title, role, name=None):
        if role == "AXCheckBox":
            return box_sentinel
        if role == "AXButton" and name == qa.ACCEPT_BUTTON_LABEL:
            return btn_sentinel
        if role == "AXButton" and name == "Hide image":
            return fit_sentinel
        if role == "AXTextArea":
            return area_sentinel
        return None

    monkeypatch.setattr(qa, "find_control", fake_find)

    def attrs_ok(el, _k):
        if el is box_sentinel:
            return 0
        if el is btn_sentinel:
            return False
        if el is area_sentinel:
            return qa.FIXTURE_TEXT
        return None

    monkeypatch.setattr(qa, "ax_attr", attrs_ok)
    res = qa.verify_unchecked_review_gate(12345)
    assert res["gate"] == "UNCHECKED"
    assert res["use_checked_text"] == "DISABLED"
    assert res["preview_text_matches_fixture"] is True
    assert res["image_preview_pane_present"] is True

    monkeypatch.setattr(qa, "ax_attr", lambda el, _k: 1 if el is box_sentinel else False)
    with pytest.raises(qa.QABlockedError, match="unchecked"):
        qa.verify_unchecked_review_gate(12345)

    monkeypatch.setattr(qa, "ax_attr", lambda el, _k: 0 if el is box_sentinel else True)
    with pytest.raises(qa.QABlockedError, match="unchecked"):
        qa.verify_unchecked_review_gate(12345)


def test_activate_qa_instance_uses_ignoring_other_apps_and_ax_raise(monkeypatch, tmp_path):
    qa = _load_qa_module()
    binary = tmp_path / "Lextide"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    state = {
        "options_seen": [],
        "active": False,
        "raised": False,
        "ax_front_set": False,
        "frontmost_pid": 999,
    }

    class FakeURL:
        def URLByResolvingSymlinksInPath(self):
            return self

        def path(self):
            return str(binary.resolve())

    class FakeRunningApp:
        def executableURL(self):
            return FakeURL()

        def unhide(self):
            pass

        def activateWithOptions_(self, opts):
            state["options_seen"].append(opts)
            state["active"] = True
            state["frontmost_pid"] = 4242
            return True

        def isActive(self):
            return state["active"]

    class FakeWorkspace:
        def frontmostApplication(self):
            return SimpleNamespace(processIdentifier=lambda: state["frontmost_pid"])

    fake_appkit = SimpleNamespace(
        NSApplicationActivateAllWindows=1,
        NSApplicationActivateIgnoringOtherApps=2,
        NSRunningApplication=SimpleNamespace(
            runningApplicationWithProcessIdentifier_=lambda _pid: FakeRunningApp()
        ),
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: FakeWorkspace()),
    )
    win_sentinel = object()
    fake_as = SimpleNamespace(
        kAXErrorSuccess=0,
        kAXFrontmostAttribute="AXFrontmost",
        kAXFocusedWindowAttribute="AXFocusedWindow",
        kAXTitleAttribute="AXTitle",
        kAXRaiseAction="AXRaise",
        AXUIElementCreateApplication=lambda _pid: object(),
        AXUIElementPerformAction=lambda el, action: (
            state.__setitem__("raised", True) or 0
            if el is win_sentinel and action == "AXRaise"
            else -1
        ),
    )
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake_as)
    monkeypatch.setattr(qa, "pump_runloop", lambda _s=0.05: None)
    monkeypatch.setattr(qa, "ax_window", lambda _pid, _t: win_sentinel)
    monkeypatch.setattr(
        qa,
        "ax_set_attr",
        lambda _el, key, val: (
            state.__setitem__("ax_front_set", bool(val)) or True if key == "AXFrontmost" else True
        ),
    )
    monkeypatch.setattr(
        qa,
        "ax_attr",
        lambda el, key: (
            state["ax_front_set"]
            if key == "AXFrontmost"
            else win_sentinel
            if key == "AXFocusedWindow"
            else qa.MAIN_WINDOW_TITLE
            if el is win_sentinel and key == "AXTitle"
            else None
        ),
    )

    result = qa.activate_qa_instance(4242, binary, timeout=2.0)
    assert result["activated"] is True
    assert state["options_seen"] == [3]
    assert state["ax_front_set"] is True
    assert state["raised"] is True
    assert result["ws_frontmost_pid"] == 4242
    assert result["ax_frontmost"] is True


def test_activate_qa_instance_rejects_wrong_executable_and_timeout(monkeypatch, tmp_path):
    qa = _load_qa_module()
    expected = tmp_path / "Lextide"
    other = tmp_path / "OtherApp"
    expected.write_text("#!/bin/sh\n")
    other.write_text("#!/bin/sh\n")

    class WrongURL:
        def URLByResolvingSymlinksInPath(self):
            return self

        def path(self):
            return str(other.resolve())

    fake_appkit = SimpleNamespace(
        NSApplicationActivateAllWindows=1,
        NSApplicationActivateIgnoringOtherApps=2,
        NSRunningApplication=SimpleNamespace(
            runningApplicationWithProcessIdentifier_=lambda _pid: SimpleNamespace(
                executableURL=lambda: WrongURL(),
                unhide=lambda: None,
                activateWithOptions_=lambda _o: False,
                isActive=lambda: False,
            )
        ),
        NSWorkspace=SimpleNamespace(
            sharedWorkspace=lambda: SimpleNamespace(
                frontmostApplication=lambda: SimpleNamespace(processIdentifier=lambda: 111)
            )
        ),
    )
    fake_as = SimpleNamespace(
        kAXErrorSuccess=0,
        kAXFrontmostAttribute="AXFrontmost",
        kAXFocusedWindowAttribute="AXFocusedWindow",
        kAXTitleAttribute="AXTitle",
        kAXRaiseAction="AXRaise",
        AXUIElementCreateApplication=lambda _pid: object(),
        AXUIElementPerformAction=lambda _e, _a: -1,
    )
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake_as)
    monkeypatch.setattr(qa, "pump_runloop", lambda _s=0.05: None)

    with pytest.raises(qa.QABlockedError, match="Wrong executable") as caught:
        qa.activate_qa_instance(4242, expected, timeout=0.3)
    assert caught.value.category == "APP_ACTIVATION_FAILED"

    # Matching executable that never becomes foreground must fail with APP_ACTIVATION_FAILED
    class ExpectedURL:
        def URLByResolvingSymlinksInPath(self):
            return self

        def path(self):
            return str(expected.resolve())

    fake_appkit.NSRunningApplication = SimpleNamespace(
        runningApplicationWithProcessIdentifier_=lambda _pid: SimpleNamespace(
            executableURL=lambda: ExpectedURL(),
            unhide=lambda: None,
            activateWithOptions_=lambda _o: True,
            isActive=lambda: False,
        )
    )
    monkeypatch.setattr(qa, "ax_window", lambda _p, _t: object())
    monkeypatch.setattr(qa, "ax_set_attr", lambda *_a: False)
    monkeypatch.setattr(qa, "ax_attr", lambda *_a: False)
    with pytest.raises(qa.QABlockedError, match="Cannot activate QA instance") as caught:
        qa.activate_qa_instance(4242, expected, timeout=0.2, max_attempts=1)
    assert caught.value.category == "APP_ACTIVATION_FAILED"


def test_run_qa_stops_immediately_when_accessibility_denied(monkeypatch, tmp_path):
    qa = _load_qa_module()
    monkeypatch.setattr(
        qa,
        "check_permissions",
        lambda: {"accessibility_trusted": False, "screen_recording_granted": False},
    )
    fake_as = SimpleNamespace()
    fake_quartz = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake_as)
    monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)
    with pytest.raises(qa.QABlockedError, match="Accessibility") as caught:
        qa.run_qa(tmp_path / "Lextide.app", tmp_path / "run-denied")
    assert caught.value.category == "ACCESSIBILITY_PERMISSION_DENIED"


def test_terminate_qa_instance_reaps_only_owned_process():
    qa = _load_qa_module()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    assert proc.poll() is None
    qa.terminate_qa_instance(proc)
    assert proc.poll() is not None
