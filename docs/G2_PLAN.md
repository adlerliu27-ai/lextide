# Lextide G2 implementation

Scope: user's approved G2 instruction; preserve accepted G1 (73 PASS, 0 FAIL, 2 SKIP),
OBS-1 accepted. No change to edge subtitle offsets/scaling. No G3, GUI or model experiments.

- [x] Preserve G1 tree b15368123f780a71789b06d181430c2c8f5b5e1f in baseline commit;
  record protected G1 report/evidence hashes locally. Use authorized repository-local Git identity.
- [x] Rename package/CLI/product to lextide; retain tts-studio CLI alias, root directory and jobs.
- [x] Extend simple engine capability/result metadata; keep schema 1 reading, write schema 2.
- [x] Implement trusted catalog and explicit install/verify/status/uninstall with partial downloads,
  SHA256, bounded retry, read-only source reuse and atomic model publication.
- [x] Implement Kokoro worker in optional isolated Python runtime; sentence PCM sample offsets,
  one final MP3 encode; no online fallback. Base edge environment remains lightweight.
- [x] Focused offline tests, one historical job compatibility check on a copy, independent review;
  single G2 DeepSeek handoff and results template. Real model lifecycle/inference belongs to DeepSeek.

Interfaces: Settings gains engine selection; EngineResult retains G1 fields and adds timing source,
model identity/hashes and effective settings. Models use one pinned release and separate storage.
Model readiness requires full integrity verification before synthesis; no silent downloads at import.
Kokoro 0.6.1 supports Python <3.14, so its dependencies use a separate Python 3.13 uv environment.
Runtime/model commands never rewrite G1 records. No speculative general plugin framework.

Verification: 119 focused unit tests passed; 10 local CLI checks passed; lint, format, compilation
and both offline lock checks passed. A copied G1 job decoded/verified at 11280 ms, and 147 protected
G1 files retain their hashes. Synthetic PCM (not Kokoro inference) retained all 36002 samples through
one MP3 encode/decode and passed two-cue SRT validation. Independent review found no blocking defect.
Real model installation, native runtime loading, disconnected inference, voice quality and performance
remain DeepSeek acceptance work. Python network guards are not an OS sandbox; GPL review is open.
