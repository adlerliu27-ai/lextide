# Apple Vision image worker (Python / PyObjC)

Local Apple Vision OCR runs through the Python worker module
`src/lextide/desktop/vision_ocr_worker.py` using PyObjC bindings
(`pyobjc-framework-Vision`, `pyobjc-framework-Quartz`, `pyobjc-framework-Cocoa`,
`pyobjc-core`, `pyobjc-framework-CoreML`). No project-specific Swift source file
or `xcrun swiftc` compilation step is required.

## Running from a source checkout (macOS)

```sh
python -m lextide.desktop.vision_ocr_worker --languages
python -m lextide.desktop.vision_ocr_worker /path/to/image.png en-US
```

## Running inside the frozen macOS app bundle

`packaging/macos/launcher.py` dispatches `--vision-ocr` before `QApplication` is
initialized, so `Lextide.app` executes Vision OCR inside an isolated child
process of the bundled Python runtime without opening a GUI window or requiring
a system Python installation:

```sh
Lextide.app/Contents/MacOS/Lextide --vision-ocr --languages
```

The macOS build (`scripts/build_macos.py`) runs this `--vision-ocr --languages`
probe against the newly built `Lextide.app` and records the result in
`build-record.json`.

## Interface and safety guarantees

- The `--languages` query returns UTF-8 JSON with `provider`, `level`,
  `revision`, `duration_ms`, `ok`, and `supported_languages`.
- Recognition accepts a local regular PNG or JPEG file (read-only,
  `O_NOFOLLOW`, up to 32 MiB, single frame, up to 24,000,000 pixels, header
  checked before decompression, EXIF orientation preserved) and an explicit
  comma-separated profile such as `en-US`, `zh-Hans`, or `zh-Hans,en-US`.
- The parent (`src/lextide/desktop/image_ocr.py`) runs the worker in a
  cancellable subprocess with an 8-second recognition deadline and enforces
  output budgets and process reaping.
- A Chinese request (`zh-Hans`) returning text with no Han characters fails with
  `LANGUAGE_MISMATCH`. The worker performs exactly one accurate recognition
  attempt with language correction enabled and never falls back to another
  language or OCR engine.
- `LEXTIDE_VISION_HELPER` remains supported as an explicit override for external
  test harnesses.
- On the G10/G10.2.3 test Mac, Vision accurate revision 3 lists both English and
  Simplified Chinese (`en-US`, `zh-Hans`). Local Tesseract remains the default
  OCR provider.
