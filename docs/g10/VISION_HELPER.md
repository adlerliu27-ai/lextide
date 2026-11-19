# Apple Vision image helper

The macOS build compiles `packaging/macos/VisionOCR.swift` with Xcode Command Line Tools. It packages the arm64 executable at `Lextide.app/Contents/Resources/ocr/lextide-vision-ocr`. A build runs its language query and saves the result in `build-record.json`. The query verifies loading the executable and framework on the build host. Recognition requires a separate image test.

For a source checkout on an Apple Silicon Mac, run from the project root:

```sh
mkdir -p .artifacts/local-ocr
/usr/bin/xcrun swiftc -O -target arm64-apple-macosx13.0 \
  -o .artifacts/local-ocr/lextide-vision-ocr packaging/macos/VisionOCR.swift
export LEXTIDE_VISION_HELPER="$PWD/.artifacts/local-ocr/lextide-vision-ocr"
"$LEXTIDE_VISION_HELPER" --languages
```

The `--languages` query returns UTF-8 JSON with the provider, accurate recognition level, active revision and supported languages. Recognition accepts a local PNG or JPEG path and an explicit comma-separated profile such as `en-US`, `zh-Hans` or `en-US,zh-Hans`. The caller runs the helper in a cancellable child process with an eight-second recognition deadline. A successful result contains text, requested and effective languages, duration in milliseconds, mean line confidence, and ordered lines. Each line has a confidence and a normalized bounding box in Vision's bottom-left coordinate system. Inspect the reading order, especially for columns.

A Chinese request returning text with no Han characters fails with `LANGUAGE_MISMATCH`. The presence of a Han character does not establish full transcription accuracy. English-only material should use `en-US`. Nonzero exit codes indicate failure and stdout provides a short error code where available. A timeout is reported by the parent and the process is reaped. The helper performs exactly one accurate recognition attempt.

On the G10 test Mac, accurate revision 3 listed both English and Simplified Chinese, but the separate English, Chinese and mixed smoke fixtures each exceeded the eight-second deadline. A local Tesseract installation is the measured baseline for this release. Build-time tools, Tesseract binaries and tessdata are distinct from the system Vision frameworks.
