#!/usr/bin/env python3
"""Frozen macOS Cocoa QA automation for local OCR partial-review gating.

Pure Python replacement for the former scripts/qa_frozen_ocr.swift (and its
embedded AppleScript / osascript calls). Uses PySide6 for synthetic fixture
rendering and PyObjC (AppKit, Foundation, Quartz, ApplicationServices) for
PID-scoped window discovery, live NSRunLoop-backed activation, native
Accessibility (AXUIElement) interaction, and keyboard event dispatch without
touching the system clipboard.

Usage:
    python scripts/qa_frozen_ocr.py <development/Lextide.app> <new-run-dir> [--keep-open]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

FIXTURE_TEXT = "We review each lesson before reading aloud."
MAIN_WINDOW_TITLE = "Lextide — A Text-to-Speech and Listening Studio"
FILE_PANEL_TITLE = "Import a local document or image"
OPTIONS_WINDOW_TITLE = "Recognize image text"
REVIEW_WINDOW_TITLE = "Check partial recognition"
CHECKBOX_LABEL = "I checked this text against the image"
ACCEPT_BUTTON_LABEL = "Use checked text"
LOCAL_RECOGNIZE_LABEL = "Recognize locally"

# macOS virtual keycodes (Carbon / HIToolbox Events.h)
KEYCODE_O = 31
KEYCODE_G = 5
KEYCODE_RETURN = 36


class QABlockedError(RuntimeError):
    """Raised when an environment, permission, or UI precondition blocks QA."""

    def __init__(self, message: str, *, category: str = "QA_BLOCKED") -> None:
        super().__init__(message)
        self.category = category


def require(condition: bool, message: str, *, category: str = "QA_BLOCKED") -> None:
    if not condition:
        raise QABlockedError(message, category=category)


def record(fields: dict[str, Any]) -> None:
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(encoded.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def pump_runloop(seconds: float = 0.05) -> None:
    """Pump Foundation NSRunLoop so AppKit KVO states (isActive/frontmost) stay current."""
    try:
        import Foundation

        until = Foundation.NSDate.dateWithTimeIntervalSinceNow_(max(0.01, seconds))
        Foundation.NSRunLoop.currentRunLoop().runUntilDate_(until)
    except Exception:  # noqa: BLE001
        time.sleep(max(0.01, seconds))


def wait_for[T](
    condition: Callable[[], T | None],
    *,
    seconds: float,
    message: str,
    category: str = "QA_TIMEOUT",
    pump: bool = False,
) -> T:
    deadline = time.monotonic() + seconds
    while True:
        value = condition()
        if value is not None:
            return value
        if time.monotonic() >= deadline:
            raise QABlockedError(message, category=category)
        if pump:
            pump_runloop(0.1)
        else:
            time.sleep(0.1)


def check_permissions() -> dict[str, bool | None]:
    """Separately probe macOS Accessibility (AX) and Screen Recording permissions."""
    import ApplicationServices as AS
    import Quartz

    ax_trusted = bool(AS.AXIsProcessTrusted())
    screen_recording: bool | None = None
    if hasattr(Quartz, "CGPreflightScreenCaptureAccess"):
        screen_recording = bool(Quartz.CGPreflightScreenCaptureAccess())
    return {
        "accessibility_trusted": ax_trusted,
        "screen_recording_granted": screen_recording,
    }


def validate_bundle_and_run_dir(
    bundle_arg: Path, run_arg: Path
) -> tuple[Path, Path, dict[str, Any]]:
    """Enforce QA isolation boundaries: development bundle only, brand-new run dir."""
    bundle = bundle_arg.resolve()
    run_dir = run_arg.absolute()
    require(
        bundle.suffix == ".app" and bundle.name == "Lextide.app",
        "Select the development Lextide.app",
        category="INVALID_BUNDLE_OR_RUN_DIR",
    )
    user_apps = (Path.home() / "Applications").resolve()
    bundle_str = str(bundle)
    require(
        not bundle_str.startswith("/Applications/")
        and not bundle_str.startswith(str(user_apps) + "/"),
        "Select a development bundle outside installed Applications",
        category="INVALID_BUNDLE_OR_RUN_DIR",
    )
    binary = bundle / "Contents/MacOS/Lextide"
    require(
        binary.is_file() and os.access(binary, os.X_OK),
        "Frozen Lextide binary is missing",
        category="INVALID_BUNDLE_OR_RUN_DIR",
    )
    plist_path = bundle / "Contents/Info.plist"
    require(
        plist_path.is_file(),
        "Frozen Lextide Info.plist is missing",
        category="INVALID_BUNDLE_OR_RUN_DIR",
    )
    with plist_path.open("rb") as stream:
        info_plist = plistlib.load(stream)
    return bundle, run_dir, info_plist


def render_synthetic_fixture(target: Path, text: str = FIXTURE_TEXT) -> dict[str, Any]:
    """Render a deterministic 1200x180 PNG fixture using PySide6 QImage/QPainter.

    Runs inside an offscreen Qt helper subprocess so the QA runner process never
    initializes a Cocoa Qt application or steals foreground focus from the
    frozen Lextide.app under test.
    """
    if target.exists() or target.is_symlink():
        raise QABlockedError(
            "Synthetic fixture target already exists", category="INVALID_BUNDLE_OR_RUN_DIR"
        )
    helper_code = (
        "import sys; "
        "from PySide6.QtCore import QBuffer, QIODevice, QRect; "
        "from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter; "
        "app = QGuiApplication(['fixture']); "
        "img = QImage(1200, 180, QImage.Format.Format_ARGB32); "
        "img.fill(QColor(255, 255, 255)); "
        "p = QPainter(img); "
        "p.setPen(QColor(0, 0, 0)); "
        "f = QFont(); f.setPixelSize(42); p.setFont(f); "
        "p.drawText(QRect(48, 24, 1104, 132), 0x0081, sys.argv[1]); "
        "p.end(); "
        "buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly); "
        "ok = img.save(buf, 'PNG'); "
        "sys.stdout.buffer.write(bytes(buf.data()) if ok else b''); "
        "sys.exit(0 if ok else 1)"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    completed = subprocess.run(
        [sys.executable, "-c", helper_code, text],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise QABlockedError(
            "Could not render the synthetic fixture with PySide6",
            category="FIXTURE_RENDER_FAILED",
        )
    png_bytes = completed.stdout
    with open(target, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
        stream.write(png_bytes)
    digest = hashlib.sha256(png_bytes).hexdigest()
    info = {"fixture": str(target), "text": text, "sha256": digest}
    record(info)
    return info


def window_id(pid: int, title: str) -> int | None:
    """Return CGWindowNumber when Screen Recording exposes kCGWindowName, else None."""
    import Quartz

    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    )
    if not windows:
        return None
    for win in windows:
        if win.get(Quartz.kCGWindowOwnerPID) == pid and win.get(Quartz.kCGWindowName) == title:
            number = win.get(Quartz.kCGWindowNumber)
            return int(number) if number is not None else None
    return None


def ax_attr(element: Any, key: str) -> Any:
    import ApplicationServices as AS

    err, value = AS.AXUIElementCopyAttributeValue(element, key, None)
    return value if err == AS.kAXErrorSuccess else None


def ax_set_attr(element: Any, key: str, value: Any) -> bool:
    import ApplicationServices as AS

    err = AS.AXUIElementSetAttributeValue(element, key, value)
    return err == AS.kAXErrorSuccess


def ax_window(pid: int, title: str) -> Any:
    """Locate a PID-scoped window directly via AXUIElement (does not require Screen Recording)."""
    import ApplicationServices as AS

    app = AS.AXUIElementCreateApplication(pid)
    windows = ax_attr(app, AS.kAXWindowsAttribute)
    if not windows:
        return None
    for win in windows:
        if ax_attr(win, AS.kAXTitleAttribute) == title:
            return win
    return None


def window_present(pid: int, title: str) -> dict[str, Any] | None:
    """Confirm target PID owns a window with the exact title via AX and/or CGWindowList."""
    cg_id = window_id(pid, title)
    ax_win = ax_window(pid, title)
    if ax_win is None and cg_id is None:
        return None
    return {"cg_window_id": cg_id, "ax_found": ax_win is not None}


def ax_find_control(
    root: Any,
    *,
    role: str,
    name: str | None = None,
    max_nodes: int = 1200,
) -> Any:
    import ApplicationServices as AS

    queue = [root]
    visited = 0
    while queue and visited < max_nodes:
        element = queue.pop()
        visited += 1
        if ax_attr(element, AS.kAXRoleAttribute) == role:
            if name is None:
                return element
            if (
                ax_attr(element, AS.kAXTitleAttribute) == name
                or ax_attr(element, AS.kAXDescriptionAttribute) == name
            ):
                return element
        children = ax_attr(element, AS.kAXChildrenAttribute)
        if children:
            queue.extend(children)
    return None


def find_control(pid: int, title: str, role: str, name: str | None = None) -> Any:
    root = ax_window(pid, title)
    if root is None:
        return None
    return ax_find_control(root, role=role, name=name)


def is_qa_foreground(pid: int) -> dict[str, Any]:
    """Verify live foreground ownership for the exact QA PID via AppKit + AXUIElement."""
    import AppKit
    import ApplicationServices as AS

    pump_runloop(0.05)
    running = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    ws = AppKit.NSWorkspace.sharedWorkspace()
    frontmost = ws.frontmostApplication() if ws is not None else None
    ws_pid = int(frontmost.processIdentifier()) if frontmost is not None else None
    ax_app = AS.AXUIElementCreateApplication(pid)
    ax_frontmost = bool(ax_attr(ax_app, AS.kAXFrontmostAttribute))
    ra_active = bool(running.isActive()) if running is not None else False
    # Require both live AX frontmost confirmation and AppKit PID agreement.
    active = ax_frontmost and (ws_pid == pid or ra_active)
    return {
        "active": active,
        "ra_active": ra_active,
        "ws_frontmost_pid": ws_pid,
        "ax_frontmost": ax_frontmost,
    }


def activate_qa_instance(
    pid: int,
    binary: Path,
    main_title: str = MAIN_WINDOW_TITLE,
    *,
    timeout: float = 8.0,
    max_attempts: int = 4,
) -> dict[str, Any]:
    """Bring the verified QA-owned Lextide PID to foreground and confirm window focus.

    Fixes BUG-QA-ACTIVATION-01:
    - Uses NSApplicationActivateAllWindows | NSApplicationActivateIgnoringOtherApps
      instead of cooperative-only activateWithOptions_(0).
    - Combines NSRunningApplication activation with AXFrontmost + AXRaise on the
      exact PID-scoped main window.
    - Pumps NSRunLoop so AppKit KVO properties never stay stale, and verifies
      both live AX frontmost state and NSWorkspace frontmost PID before returning.
    """
    import AppKit
    import ApplicationServices as AS

    running = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    require(
        running is not None, "QA app exited before activation", category="APP_ACTIVATION_FAILED"
    )
    exe_url = running.executableURL()
    actual_exe = Path(str(exe_url.URLByResolvingSymlinksInPath().path())) if exe_url else None
    require(
        actual_exe == binary.resolve(),
        "Wrong executable for QA PID",
        category="APP_ACTIVATION_FAILED",
    )

    deadline = time.monotonic() + timeout
    ax_app = AS.AXUIElementCreateApplication(pid)
    options = int(getattr(AppKit, "NSApplicationActivateAllWindows", 1)) | int(
        getattr(AppKit, "NSApplicationActivateIgnoringOtherApps", 2)
    )
    attempts = 0
    last_state: dict[str, Any] = {}
    last_activate_ret: bool | None = None
    last_ax_front_ok: bool | None = None
    last_ax_raise_ok: bool | None = None

    while time.monotonic() < deadline and attempts < max_attempts:
        state = is_qa_foreground(pid)
        last_state = state
        main_win = ax_window(pid, main_title)
        if state["active"] and main_win is not None:
            AS.AXUIElementPerformAction(main_win, AS.kAXRaiseAction)
            focused_win = ax_attr(ax_app, AS.kAXFocusedWindowAttribute)
            focused_title = ax_attr(focused_win, AS.kAXTitleAttribute) if focused_win else None
            return {
                "activated": True,
                "attempts": attempts,
                "activate_options": options,
                "activate_ret": last_activate_ret,
                "ax_set_frontmost_ok": last_ax_front_ok,
                "ax_raise_ok": last_ax_raise_ok,
                "focused_window_title": str(focused_title) if focused_title else None,
                **state,
            }

        attempts += 1
        if hasattr(running, "unhide"):
            running.unhide()
        last_activate_ret = bool(running.activateWithOptions_(options))
        last_ax_front_ok = ax_set_attr(ax_app, AS.kAXFrontmostAttribute, True)
        if main_win is not None:
            raise_rc = AS.AXUIElementPerformAction(main_win, AS.kAXRaiseAction)
            last_ax_raise_ok = raise_rc == AS.kAXErrorSuccess
            ax_set_attr(ax_app, AS.kAXFocusedWindowAttribute, main_win)

        step_deadline = min(deadline, time.monotonic() + 1.5)
        while time.monotonic() < step_deadline:
            state = is_qa_foreground(pid)
            last_state = state
            main_win = ax_window(pid, main_title)
            if state["active"] and main_win is not None:
                focused_win = ax_attr(ax_app, AS.kAXFocusedWindowAttribute)
                focused_title = ax_attr(focused_win, AS.kAXTitleAttribute) if focused_win else None
                return {
                    "activated": True,
                    "attempts": attempts,
                    "activate_options": options,
                    "activate_ret": last_activate_ret,
                    "ax_set_frontmost_ok": last_ax_front_ok,
                    "ax_raise_ok": last_ax_raise_ok,
                    "focused_window_title": str(focused_title) if focused_title else None,
                    **state,
                }
            pump_runloop(0.1)

    raise QABlockedError(
        (
            f"Cannot activate QA instance pid={pid} after {attempts} attempts "
            f"(ws_frontmost_pid={last_state.get('ws_frontmost_pid')}, "
            f"ra_active={last_state.get('ra_active')}, "
            f"ax_frontmost={last_state.get('ax_frontmost')})"
        ),
        category="APP_ACTIVATION_FAILED",
    )


def _require_foreground_qa(pid: int, expected_window_title: str | None = None) -> None:
    state = is_qa_foreground(pid)
    require(
        state["active"],
        (
            f"QA instance pid={pid} lost foreground focus "
            f"(ws_frontmost_pid={state.get('ws_frontmost_pid')}, "
            f"ax_frontmost={state.get('ax_frontmost')})"
        ),
        category="APP_ACTIVATION_FAILED",
    )
    if expected_window_title is not None:
        require(
            window_present(pid, expected_window_title) is not None,
            f"Expected QA window missing: {expected_window_title}",
            category="WINDOW_NOT_FOUND",
        )


def _post_key_to_pid(pid: int, keycode: int, flags: int = 0) -> None:
    import Quartz

    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    for key_down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(source, keycode, key_down)
        require(
            event is not None,
            f"Could not create keyboard event for keycode {keycode}",
            category="FILE_PANEL_BLOCKED",
        )
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPostToPid(pid, event)


def _type_unicode_to_pid(pid: int, text: str) -> None:
    """Type Unicode text directly to a verified PID via Quartz without touching clipboard."""
    import Quartz

    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    for char in text:
        for key_down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(source, 0, key_down)
            require(
                event is not None,
                "Could not create Unicode keyboard event",
                category="FILE_PANEL_BLOCKED",
            )
            Quartz.CGEventSetFlags(event, 0)
            Quartz.CGEventKeyboardSetUnicodeString(event, len(char), char)
            Quartz.CGEventPostToPid(pid, event)
        time.sleep(0.01)


def _list_xpc_panel_pids() -> set[int]:
    """List current macOS openAndSavePanelService XPC PIDs (read-only)."""
    completed = subprocess.run(
        ["/bin/ps", "-axo", "pid=,command="],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    pids: set[int] = set()
    if completed.returncode != 0:
        return pids
    for line in completed.stdout.splitlines():
        if "openAndSavePanelService" in line:
            parts = line.strip().split(None, 1)
            if parts and parts[0].isdigit():
                pids.add(int(parts[0]))
    return pids


def _find_sheet_text_field(pid: int, panel_title: str) -> Any:
    """Locate the Go-to-Folder text field in the native file panel via AXUIElement."""
    import ApplicationServices as AS

    panel = ax_window(pid, panel_title)
    if panel is None:
        return None
    sheets = ax_attr(panel, getattr(AS, "kAXSheetsAttribute", "AXSheets")) or []
    for sheet in sheets:
        field = ax_find_control(sheet, role=AS.kAXTextFieldRole)
        if field is not None:
            return field
    sheet = ax_find_control(panel, role=AS.kAXSheetRole)
    if sheet is not None:
        return ax_find_control(sheet, role=AS.kAXTextFieldRole)
    return None


def drive_native_file_panel(
    pid: int, fixture_path: str, *, xpc_panel_pid: int | None = None
) -> str:
    """Drive the macOS native Open panel in pure Python (no AppleScript/osascript).

    On modern macOS, NSOpenPanel is hosted out-of-process by Apple's
    openAndSavePanelService XPC helper. Targeting the newly spawned XPC PID
    (when observed) opens the Go-to-Folder AXSheet directly without global HID
    broadcasts, AppleScript, or clipboard writes.
    """
    import ApplicationServices as AS
    import Quartz

    require(
        bool(AS.AXIsProcessTrusted()),
        "Accessibility (AX) permission is required for QA",
        category="ACCESSIBILITY_PERMISSION_DENIED",
    )
    _require_foreground_qa(pid, FILE_PANEL_TITLE)

    # Target the QA-owned OpenPanel XPC PID if observed, otherwise the QA app PID.
    event_target_pid = xpc_panel_pid if xpc_panel_pid is not None else pid
    _post_key_to_pid(
        event_target_pid,
        KEYCODE_G,
        Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskShift,
    )

    deadline = time.monotonic() + 5.0
    text_field = None
    while time.monotonic() < deadline:
        text_field = _find_sheet_text_field(pid, FILE_PANEL_TITLE)
        if text_field is not None:
            break
        pump_runloop(0.1)

    resolved_fixture = str(Path(fixture_path).resolve())
    path_entry_mode = "ax_set_value"
    if text_field is not None and ax_set_attr(text_field, AS.kAXValueAttribute, resolved_fixture):
        pump_runloop(0.5)
        _require_foreground_qa(pid, FILE_PANEL_TITLE)
        _post_key_to_pid(event_target_pid, KEYCODE_RETURN, 0)
        pump_runloop(0.7)
    else:
        path_entry_mode = "quartz_unicode_fallback"
        pump_runloop(0.5)
        _require_foreground_qa(pid, FILE_PANEL_TITLE)
        _type_unicode_to_pid(event_target_pid, resolved_fixture)
        pump_runloop(0.5)
        _require_foreground_qa(pid, FILE_PANEL_TITLE)
        _post_key_to_pid(event_target_pid, KEYCODE_RETURN, 0)
        pump_runloop(0.7)

    # Confirm Open in the panel via AX Open/打开 button or Return to the XPC panel PID.
    panel_after = ax_window(pid, FILE_PANEL_TITLE)
    if panel_after is not None:
        _require_foreground_qa(pid, FILE_PANEL_TITLE)
        open_btn = None
        for label in ("Open", "打开"):
            open_btn = find_control(pid, FILE_PANEL_TITLE, AS.kAXButtonRole, label)
            if open_btn is not None:
                break
        if open_btn is not None:
            deadline_btn = time.monotonic() + 3.0
            while time.monotonic() < deadline_btn and not bool(
                ax_attr(open_btn, AS.kAXEnabledAttribute)
            ):
                pump_runloop(0.1)
            if bool(ax_attr(open_btn, AS.kAXEnabledAttribute)):
                AS.AXUIElementPerformAction(open_btn, AS.kAXPressAction)
                pump_runloop(0.6)
        if window_present(pid, FILE_PANEL_TITLE) is not None:
            _require_foreground_qa(pid, FILE_PANEL_TITLE)
            _post_key_to_pid(event_target_pid, KEYCODE_RETURN, 0)

    wait_for(
        lambda: window_present(pid, OPTIONS_WINDOW_TITLE),
        seconds=8.0,
        message="Native file panel or AX not accessible. Use the printed fixture manually.",
        category="FILE_PANEL_BLOCKED",
        pump=True,
    )
    return path_entry_mode


def verify_unchecked_review_gate(pid: int) -> dict[str, Any]:
    """Verify Partial Review checkbox is unchecked, accept is disabled, and preview exists."""
    import ApplicationServices as AS

    box = find_control(pid, REVIEW_WINDOW_TITLE, AS.kAXCheckBoxRole, CHECKBOX_LABEL)
    accept = find_control(pid, REVIEW_WINDOW_TITLE, AS.kAXButtonRole, ACCEPT_BUTTON_LABEL)
    if box is None or accept is None:
        raise QABlockedError(
            "Review is visible but AX gate is unavailable", category="REVIEW_GATE_VIOLATION"
        )
    checked = ax_attr(box, AS.kAXValueAttribute)
    enabled = ax_attr(accept, AS.kAXEnabledAttribute)
    if checked is None or enabled is None:
        raise QABlockedError(
            "Review is visible but AX gate state could not be read",
            category="REVIEW_GATE_VIOLATION",
        )
    require(
        not bool(checked) and not bool(enabled),
        "Review did not start unchecked with adoption disabled",
        category="REVIEW_GATE_VIOLATION",
    )
    text_area = find_control(pid, REVIEW_WINDOW_TITLE, getattr(AS, "kAXTextAreaRole", "AXTextArea"))
    preview_text = str(ax_attr(text_area, AS.kAXValueAttribute) or "").strip() if text_area else ""
    hide_img_btn = find_control(pid, REVIEW_WINDOW_TITLE, AS.kAXButtonRole, "Hide image")
    return {
        "gate": "UNCHECKED",
        "use_checked_text": "DISABLED",
        "preview_text_nonempty": bool(preview_text),
        "preview_text_matches_fixture": FIXTURE_TEXT in preview_text,
        "image_preview_pane_present": hide_img_btn is not None,
    }


def terminate_qa_instance(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


def run_qa(bundle_arg: Path, run_arg: Path, *, keep_open: bool = False) -> int:
    import ApplicationServices as AS
    import Quartz

    perms = check_permissions()
    record({"event": "permissions", **perms})
    require(
        bool(perms["accessibility_trusted"]),
        (
            "Accessibility (AX) permission is required for QA automation. "
            "Grant permission in System Settings -> Privacy & Security -> Accessibility."
        ),
        category="ACCESSIBILITY_PERMISSION_DENIED",
    )

    bundle, run_dir, info_plist = validate_bundle_and_run_dir(bundle_arg, run_arg)
    try:
        os.mkdir(run_dir, 0o700)
    except OSError as exc:
        raise QABlockedError(
            "Run directory must be new and its parent must exist",
            category="INVALID_BUNDLE_OR_RUN_DIR",
        ) from exc

    data_dir = run_dir / "data"
    os.mkdir(data_dir, 0o700)
    prefs_path = data_dir / "preferences.json"
    with open(prefs_path, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
        stream.write(b'{"language":"en","theme":"light"}')

    fixture = run_dir / "english.png"
    render_synthetic_fixture(fixture)

    binary = bundle / "Contents/MacOS/Lextide"
    build_commit = str(info_plist.get("LextideBuildCommit", "UNKNOWN"))
    app_version = str(info_plist.get("CFBundleShortVersionString", "UNKNOWN"))

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "cocoa"
    env["LEXTIDE_CACHE_DIR"] = str(data_dir / "cache")
    env["LEXTIDE_MODEL_DIR"] = str(data_dir / "models")

    log_path = run_dir / "cocoa.log"
    process: subprocess.Popen[Any] | None = None
    try:
        with open(
            log_path, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)
        ) as log_stream:
            process = subprocess.Popen(
                [str(binary), "--data-dir", str(data_dir)],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
            )
        pid = process.pid
        record(
            {
                "pid": pid,
                "binary": str(binary),
                "data": str(data_dir),
                "build_commit": build_commit,
                "version": app_version,
            }
        )

        main_info = wait_for(
            lambda: window_present(pid, MAIN_WINDOW_TITLE),
            seconds=25.0,
            message="No main window of launched PID (verify Accessibility permission)",
            category="WINDOW_NOT_FOUND",
            pump=True,
        )
        record(
            {
                "pid": pid,
                "main_window_id": main_info["cg_window_id"],
                "main_window_ax_found": main_info["ax_found"],
            }
        )

        activation = activate_qa_instance(pid, binary, MAIN_WINDOW_TITLE)
        record({"event": "activation", "pid": pid, **activation})

        # Snapshot existing XPC OpenPanel helpers so we can address only the newly spawned one.
        before_xpc = _list_xpc_panel_pids()
        _post_key_to_pid(pid, KEYCODE_O, Quartz.kCGEventFlagMaskCommand)
        panel_info = wait_for(
            lambda: window_present(pid, FILE_PANEL_TITLE),
            seconds=25.0,
            message="QA-owned file panel was not presented",
            category="FILE_PANEL_BLOCKED",
            pump=True,
        )
        pump_runloop(0.3)
        new_xpc = sorted(_list_xpc_panel_pids() - before_xpc)
        xpc_panel_pid = new_xpc[0] if len(new_xpc) == 1 else None
        record(
            {
                "pid": pid,
                "file_panel_window_id": panel_info["cg_window_id"],
                "file_panel_ax_found": panel_info["ax_found"],
                "xpc_panel_pid": xpc_panel_pid,
            }
        )

        entry_mode = drive_native_file_panel(pid, str(fixture), xpc_panel_pid=xpc_panel_pid)
        options_info = wait_for(
            lambda: window_present(pid, OPTIONS_WINDOW_TITLE),
            seconds=8.0,
            message="QA options window unavailable",
            category="OCR_OPTIONS_UNAVAILABLE",
            pump=True,
        )
        record(
            {
                "pid": pid,
                "options_window_id": options_info["cg_window_id"],
                "options_ax_found": options_info["ax_found"],
                "panel_entry_mode": entry_mode,
            }
        )

        local_btn = wait_for(
            lambda: find_control(
                pid, OPTIONS_WINDOW_TITLE, AS.kAXButtonRole, LOCAL_RECOGNIZE_LABEL
            ),
            seconds=7.0,
            message="Could not locate local recognition button",
            category="OCR_OPTIONS_UNAVAILABLE",
            pump=True,
        )
        require(
            AS.AXUIElementPerformAction(local_btn, AS.kAXPressAction) == AS.kAXErrorSuccess,
            "Unable to invoke local recognition",
            category="OCR_OPTIONS_UNAVAILABLE",
        )

        review_info = wait_for(
            lambda: window_present(pid, REVIEW_WINDOW_TITLE),
            seconds=50.0,
            message="Partial review not observed. Check Tesseract eng+chi_sim and QA app.",
            category="PARTIAL_REVIEW_NOT_OBSERVED",
            pump=True,
        )
        review_id = review_info["cg_window_id"]
        record(
            {
                "pid": pid,
                "review_window_id": review_id,
                "review_ax_found": review_info["ax_found"],
                "state": "REVIEW_OPEN_USER_CHECK_PENDING",
            }
        )

        if review_id is not None and perms["screen_recording_granted"] is not False:
            capture = subprocess.run(
                ["/usr/sbin/screencapture", "-x", f"-l{review_id}", str(run_dir / "partial.png")],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
            shot_ok = capture.returncode == 0 and (run_dir / "partial.png").is_file()
            record(
                {
                    "screenshot_status": "CAPTURED" if shot_ok else "SCREENSHOT_CAPTURE_BLOCKED",
                    "window_screenshot_exit": capture.returncode,
                }
            )
        else:
            record(
                {
                    "screenshot_status": "SCREENSHOT_CAPTURE_BLOCKED",
                    "window_screenshot_exit": None,
                    "reason": (
                        "Screen Recording permission unavailable; GUI AX verification continues."
                    ),
                }
            )

        gate_result = verify_unchecked_review_gate(pid)
        record({"input": "SYNTHETIC_LOCAL_ONLY", **gate_result})
        return 0
    finally:
        if not keep_open:
            terminate_qa_instance(process)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="Path to development Lextide.app")
    parser.add_argument("run_dir", type=Path, help="New isolated QA output directory")
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Leave the QA-owned Lextide instance open at the unchecked review gate",
    )
    args = parser.parse_args(argv)
    try:
        return run_qa(args.bundle, args.run_dir, keep_open=args.keep_open)
    except QABlockedError as exc:
        sys.stderr.write(f"BLOCKED [{exc.category}]: {exc}\n")
        return 1
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"BLOCKED [UNEXPECTED_ERROR]: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
