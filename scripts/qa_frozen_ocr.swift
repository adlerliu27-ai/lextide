#!/usr/bin/swift
// Frozen Cocoa QA only. Run: swift scripts/qa_frozen_ocr.swift <development.app> <new-run-dir>
// Launches an exclusively owned instance. It stops at the unchecked review gate.
import AppKit
import CryptoKit
import Foundation
import CoreGraphics

enum QAError: Error { case blocked(String) }
func require(_ value: Bool, _ message: String) throws {
    if !value { throw QAError.blocked(message) }
}
func record(_ fields: [String: Any]) throws {
    let json = try JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
    FileHandle.standardOutput.write(json + Data([10]))
}
func waitFor<T>(_ condition: () -> T?, seconds: TimeInterval, message: String) throws -> T {
    let deadline = Date().addingTimeInterval(seconds)
    repeat {
        if let value = condition() { return value }
        Thread.sleep(forTimeInterval: 0.1)
    } while Date() < deadline
    throw QAError.blocked(message)
}
func windowID(_ pid: pid_t, _ title: String) -> Int? {
    let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly], 0) as? [[String: Any]]
    return windows?.first {
        ($0[kCGWindowOwnerPID as String] as? Int == Int(pid)) &&
        ($0[kCGWindowName as String] as? String == title)
    }?[kCGWindowNumber as String] as? Int
}
func prepareFixture(_ url: URL) throws {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 1200, pixelsHigh: 180,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
        bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    NSColor.white.setFill()
    NSBezierPath(rect: NSRect(x: 0, y: 0, width: 1200, height: 180)).fill()
    let words: NSString = "We review each lesson before reading aloud."
    words.draw(at: NSPoint(x: 48, y: 64), withAttributes: [
        .font: NSFont.systemFont(ofSize: 42), .foregroundColor: NSColor.black
    ])
    NSGraphicsContext.restoreGraphicsState()
    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        throw QAError.blocked("Could not render the synthetic fixture")
    }
    try png.write(to: url, options: .withoutOverwriting)
    try record(["fixture": url.path, "text": words as String,
                "sha256": SHA256.hash(data: png).map { String(format: "%02x", $0) }.joined()])
}

// Every action below targets an AX process selected by its Unix PID. Every keystroke
// also verifies the foreground window. Never click coordinates or overwrite the clipboard.
func drive(_ pid: pid_t, _ filename: String) throws {
    let script = #"""
    on run argv
      set qaPid to item 1 of argv as integer
      tell application "System Events" to tell (first application process whose unix id is qaPid)
        repeat 80 times
          if exists window "Import a local document or image" then exit repeat
          delay 0.1
        end repeat
        set panel to window "Import a local document or image"
        if not frontmost or name of window 1 is not name of panel then error "QA file panel inaccessible"
        keystroke "g" using {command down, shift down}
        repeat 50 times
          if exists text field 1 of sheet 1 of panel then exit repeat
          delay 0.1
        end repeat
        set value of text field 1 of sheet 1 of panel to item 2 of argv
        delay 0.5
        if not frontmost or not (exists sheet 1 of panel) then error "QA Go-to-Folder unavailable"
        key code 36
        delay 0.6
        if frontmost and name of window 1 is "Import a local document or image" then key code 36
        repeat 80 times
          if exists window "Recognize image text" then exit repeat
          delay 0.1
        end repeat
        if not frontmost or name of window 1 is not "Recognize image text" then error "Missing recognition options"
        return
      end tell
    end run
    """#
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
    p.arguments = ["-e", script, String(pid), filename]
    try p.run()
    let deadline = Date().addingTimeInterval(35)
    while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
    if p.isRunning { p.terminate(); throw QAError.blocked("Native file panel timeout") }
    try require(p.terminationStatus == 0, "Native file panel or AX not accessible. Use the printed fixture manually.")
}
// Qt nests its controls under AX groups. System Events can fail coercing the
// flattened list of elements, so read the native AX tree of this exact PID/window.
func attr(_ e: AXUIElement, _ key: String) -> CFTypeRef? {
    var v: CFTypeRef?
    return AXUIElementCopyAttributeValue(e, key as CFString, &v) == .success ? v : nil
}
func control(_ pid: pid_t, _ title: String, _ role: String, _ name: String) -> AXUIElement? {
    let app = AXUIElementCreateApplication(pid)
    guard windowID(pid, title) != nil,
          let windows = attr(app, kAXWindowsAttribute) as? [AXUIElement],
          let root = windows.first(where: { attr($0, kAXTitleAttribute) as? String == title }) else { return nil }
    var queue = [root]
    var count = 0
    while let e = queue.popLast(), count < 1200 {
        count += 1
        if attr(e, kAXRoleAttribute) as? String == role &&
            [kAXTitleAttribute, kAXDescriptionAttribute].contains(where: { attr(e, $0) as? String == name }) {
            return e
        }
        queue += (attr(e, kAXChildrenAttribute) as? [AXUIElement]) ?? []
    }
    return nil
}
func uncheckedGate(_ pid: pid_t) throws {
    let title = "Check partial recognition"
    guard let box = control(pid, title, kAXCheckBoxRole, "I checked this text against the image"),
          let accept = control(pid, title, kAXButtonRole, "Use checked text"),
          let checked = attr(box, kAXValueAttribute) as? NSNumber,
          let enabled = attr(accept, kAXEnabledAttribute) as? NSNumber else {
        throw QAError.blocked("Review is visible but AX gate is unavailable")
    }
    try require(!checked.boolValue && !enabled.boolValue, "Review did not start unchecked with adoption disabled")
}
func run() throws {
    try require(CommandLine.arguments.count == 3, "Usage: swift qa_frozen_ocr.swift development.app NEW-run-dir")
    let bundle = URL(fileURLWithPath: CommandLine.arguments[1]).resolvingSymlinksInPath().standardizedFileURL
    let run = URL(fileURLWithPath: CommandLine.arguments[2]).standardizedFileURL
    let fm = FileManager.default
    try require(bundle.pathExtension == "app" && bundle.lastPathComponent == "Lextide.app",
                "Select the development Lextide.app")
    let userApps = fm.homeDirectoryForCurrentUser.appendingPathComponent("Applications")
    try require(!bundle.path.hasPrefix("/Applications/") && !bundle.path.hasPrefix(userApps.path + "/"),
                "Select a development bundle outside installed Applications")
    try require(mkdir(run.path, 0o700) == 0, "Run directory must be new and its parent must exist")
    let data = run.appendingPathComponent("data")
    try fm.createDirectory(at: data, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
    try Data(#"{"language":"en","theme":"light"}"#.utf8).write(to: data.appendingPathComponent("preferences.json"), options: .withoutOverwriting)
    let fixture = run.appendingPathComponent("english.png")
    try prepareFixture(fixture)
    let binary = bundle.appendingPathComponent("Contents/MacOS/Lextide")
    let process = Process()
    process.executableURL = binary
    process.arguments = ["--data-dir", data.path]
    var env = ProcessInfo.processInfo.environment
    env["QT_QPA_PLATFORM"] = "cocoa"
    env["LEXTIDE_CACHE_DIR"] = data.appendingPathComponent("cache").path
    env["LEXTIDE_MODEL_DIR"] = data.appendingPathComponent("models").path
    process.environment = env
    let log = run.appendingPathComponent("cocoa.log")
    try Data().write(to: log, options: .withoutOverwriting)
    let output = try FileHandle(forWritingTo: log)
    process.standardOutput = output
    process.standardError = output
    try process.run()
    let pid = process.processIdentifier
    try record(["pid": pid, "binary": binary.path, "data": data.path])
    let main = "Lextide — A Text-to-Speech and Listening Studio"
    let mainID = try waitFor({ windowID(pid, main) }, seconds: 25,
                            message: "No main window of launched PID (check Screen Recording permission)")
    guard let app = NSRunningApplication(processIdentifier: pid) else { throw QAError.blocked("QA app exited") }
    try require(app.executableURL?.resolvingSymlinksInPath().path == binary.path, "Wrong executable")
    try record(["pid": pid, "main_window_id": mainID])
    _ = app.activate(options: [])
    _ = try waitFor({ app.isActive ? true : nil }, seconds: 5, message: "Cannot activate QA instance")
    // Qt presents an AppKit file panel asynchronously. Scope the shortcut to our
    // receiver and wait for the actual CG window before addressing its AX sheet.
    for down in [true, false] {
        guard let e = CGEvent(keyboardEventSource: nil, virtualKey: 31, keyDown: down) else {
            throw QAError.blocked("Could not create the import shortcut")
        }
        e.flags = .maskCommand
        e.postToPid(pid)
    }
    let panelID = try waitFor({ windowID(pid, "Import a local document or image") },
                              seconds: 25, message: "QA-owned file panel was not presented")
    try record(["pid": pid, "file_panel_window_id": panelID])
    try drive(pid, fixture.path)
    let optionsTitle = "Recognize image text"
    let optionsID = try waitFor({ windowID(pid, optionsTitle) }, seconds: 8, message: "QA options window unavailable")
    try record(["pid": pid, "options_window_id": optionsID])
    let local = try waitFor({ control(pid, optionsTitle, kAXButtonRole, "Recognize locally") },
                            seconds: 7, message: "Could not locate local recognition button")
    try require(AXUIElementPerformAction(local, kAXPressAction as CFString) == .success, "Unable to invoke local recognition")
    let reviewID = try waitFor({ windowID(pid, "Check partial recognition") }, seconds: 50,
                              message: "Partial review not observed. Check Tesseract eng+chi_sim and QA app.")
    try record(["pid": pid, "review_window_id": reviewID, "state": "REVIEW_OPEN_USER_CHECK_PENDING"])
    let capture = Process()
    capture.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    capture.arguments = ["-x", "-l" + String(reviewID), run.appendingPathComponent("partial.png").path]
    try capture.run()
    capture.waitUntilExit()
    try record(["window_screenshot_exit": capture.terminationStatus])
    try uncheckedGate(pid)
    try record(["gate": "UNCHECKED", "use_checked_text": "DISABLED", "input": "SYNTHETIC_LOCAL_ONLY"])
}
do { try run() } catch {
    FileHandle.standardError.write(Data("BLOCKED: \(error)\n".utf8))
    exit(1)
}
