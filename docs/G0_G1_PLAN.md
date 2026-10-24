# G0 + G1 Implementation Plan

Goal: implement the user's approved G0/G1 scope, then hand real-environment acceptance to DeepSeek.
Spec: user kickoff V2 and Notion's 2026-10-14 latest kickoff entry.
Architecture: argparse CLI → conservative text preparation → explicit per-run consent →
replaceable async engine → structural/decoder/subtitle validation → atomic local job directory.
Tech stack: Python 3.12–3.14, uv, edge-tts 7.3.1, system ffmpeg/ffprobe, pytest, ruff.

The user explicitly requested implementation now, no new specification/reapproval cycle and
only lightweight self-checks. This short plan replaces the skills' full spec/approval/test process.

- [x] G0: src layout, pinned direct dependencies, lock, CLI/config/errors, isolated venv.
- [x] G1 input/consent: UTF-8 TXT or text, original bytes, minimal change spans, preview hash;
      no TTS request without --allow-online plus text confirmation.
- [x] G1 engine/output: explicit SentenceBoundary, bounded timeout, sanitized failures,
      sentence metadata, strict MP3 end-frame + ffprobe + PCM decode, SRT monotonicity.
- [x] Integration: unique staged job, hashes/manifest, atomic publish, no overwrite;
      errors and cancellation remove own temporary work only.
- [x] Small offline checks; code review; README and one DeepSeek handoff; stop before G2.

Review focus: denied consent, changed input after preview, empty/truncated media, duplicate or
overlapping subtitles, output failure/cancellation. Full network/fault/playback testing belongs to DeepSeek.

Execution record: 94 offline tests passed; ruff, compileall, imports, help, preview and denied/invalid
CLI checks passed. Independent review found double HTML unescaping; three regressions failed before
the fix and passed after preserving public metadata literally. No real TTS/decoder/playback executed.

Conservative decisions: original UTF-8 <=64 KiB; escaped synthesis text <=3000 bytes; rate -50%..+100%;
whole-job network timeout 90 seconds (configurable 1..300); output <=32 MiB/10 minutes. No semantic
text rewriting. Missing V3/M0_V2/fixed-validator source in the limited reference inventory did not
block original implementation. Existing Logo formats: PNG, SVG, AI, EPS, PDF, ICNS, ICO, JSON; untouched.
Atomic publish covers visibility; SIGKILL/power-loss recovery and stronger durability remain outside G1.
