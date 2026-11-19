// Local Lextide helper. The Python parent enforces the 8-second timeout
// and cancellation. No fallback or second recognition request is performed.
import Darwin
import Foundation
import ImageIO
import Vision

let start = ProcessInfo.processInfo.systemUptime
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true

func finish(_ result: [String: Any], status: Int32) -> Never {
    var payload = result
    payload["provider"] = "apple-vision"
    payload["level"] = "accurate"
    payload["revision"] = request.revision
    payload["duration_ms"] = Int((ProcessInfo.processInfo.systemUptime - start) * 1000)
    if let encoded = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) {
        FileHandle.standardOutput.write(encoded)
        FileHandle.standardOutput.write(Data([10]))
        exit(status)
    }
    exit(2)
}

func fail(_ code: String) -> Never {
    finish(["ok": false, "error_code": code], status: 1)
}

// Read a single bounded ordinary file through one descriptor. Header checks
// precede image decompression. Caller owns the input; this helper never writes it.
func inputImage(_ path: String) -> (CGImage, CGImagePropertyOrientation) {
    let descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK)
    guard descriptor >= 0 else { fail("INVALID_IMAGE") }
    let input = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    let maxBytes = 32 * 1024 * 1024
    var info = stat()
    guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
        info.st_size > 0, info.st_size <= maxBytes else { fail("INVALID_IMAGE") }
    guard let bytes = try? input.read(upToCount: maxBytes + 1),
        bytes.count == info.st_size,
        let source = CGImageSourceCreateWithData(bytes as CFData, nil),
        let kind = CGImageSourceGetType(source),
        ["public.png", "public.jpeg"].contains(kind as String),
        CGImageSourceGetCount(source) == 1,
        let fields = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any],
        let w = fields[kCGImagePropertyPixelWidth as String] as? Int,
        let h = fields[kCGImagePropertyPixelHeight as String] as? Int,
        w > 0, h > 0, w <= 24_000_000 / h,
        let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
    else { fail("INVALID_IMAGE") }
    let orientation = (fields[kCGImagePropertyOrientation as String] as? UInt32)
        .flatMap(CGImagePropertyOrientation.init(rawValue:)) ?? .up
    return (image, orientation)
}

let args = Array(CommandLine.arguments.dropFirst())
guard let supported = try? request.supportedRecognitionLanguages() else { fail("PROVIDER_UNAVAILABLE") }
if args == ["--languages"] {
    finish(["ok": true, "supported_languages": supported.sorted()], status: 0)
}
guard args.count == 2 else { fail("INVALID_ARGUMENTS") }
let languages = args[1].components(separatedBy: ",")
guard !languages.isEmpty, Set(languages).count == languages.count,
    languages.allSatisfy({ supported.contains($0) && ["en-US", "zh-Hans"].contains($0) })
else { fail("UNSUPPORTED_LANGUAGE") }
request.recognitionLanguages = languages
let (image, orientation) = inputImage(args[0])
do {
    try VNImageRequestHandler(cgImage: image, orientation: orientation).perform([request])
} catch {
    fail("RECOGNITION_FAILED")
}
// Simple horizontal single-column reading order. Columns need manual review.
let observations = (request.results ?? []).sorted {
    if $0.boundingBox.midY == $1.boundingBox.midY { return $0.boundingBox.minX < $1.boundingBox.minX }
    return $0.boundingBox.midY > $1.boundingBox.midY
}
guard observations.count <= 8192 else { fail("OUTPUT_LIMIT") }
var rows = [[String: Any]]()
var strings = [String]()
var confidenceSum = 0.0
var byteCount = 0
for item in observations {
    guard let best = item.topCandidates(1).first, !best.string.isEmpty else { continue }
    byteCount += best.string.utf8.count + 1
    guard byteCount <= 1_048_576 else { fail("OUTPUT_LIMIT") }
    let box = item.boundingBox
    rows.append(["text": best.string, "confidence": best.confidence,
                 "bbox": [box.minX, box.minY, box.width, box.height]])
    strings.append(best.string)
    confidenceSum += Double(best.confidence)
}
guard !rows.isEmpty else { fail("EMPTY_RESULT") }
let text = strings.joined(separator: "\n")
// A Han character is necessary for our explicit Chinese profile, never proof
// of full language or semantic coverage. English-only inputs use en-US.
if languages.contains("zh-Hans") && text.range(of: #"\p{script=Han}"#, options: .regularExpression) == nil {
    fail("LANGUAGE_MISMATCH")
}
finish(["ok": true, "text": text, "lines": rows, "requested_languages": languages,
        "effective_languages": request.recognitionLanguages,
        "confidence": confidenceSum / Double(rows.count)], status: 0)
