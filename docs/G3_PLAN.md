# G3 implementation plan

Goal: user's approved G3 long-text generation, durable chunk cache and task recovery, no G4.
Baseline: 7f7ab1f, G2_PASS / BUG-1 CLOSED. Existing short CLI/Edge/Kokoro filter behavior stays accepted.

- [x] document.py: bounded local UTF-8 preparation; stable Segment/Chunk slices/mapping, engine-specific
  request limits, deterministic oversized-sentence splits. No LLM/NLP/network or lost text.
- [x] storage.py: canonical request key independent of audio hash, atomic JSON and flock, validated
  PCM cache entries, no half-file hits and no shared final-output paths.
- [x] media.py: Edge uses accepted adapter then decoded PCM; Kokoro uses accepted local worker and
  model lease with no intermediate MP3. Stream concatenate PCM, offset native boundaries using
  actual sample counts, optional inter-chunk silence, accepted filter at final single encoding.
- [x] tasks.py + CLI: immutable request/plan with hashes, versioned task.json, legal state transitions,
  exclusive task/cache-runner locks, durable per-chunk progress, cancel/pause/failure recovery,
  atomic export; fresh online authorization on every resume. Schema 1/2 readers preserved.
- [x] Focused unit/fake-recovery and short synthetic PCM checks, old-job copy compatibility, independent
  review, README and one G3_TEST_HANDOFF. Real long inference/SIGKILL/performance belongs to DeepSeek.

Contracts: job_id is independent UUID. cache_key = canonical JSON SHA256 of raw synthesis request,
including engine/version, actual text, voice/rate, model/voices hashes, rules and raw PCM profile.
artifact_sha256 validates actual bytes, never determines job/cache identity. Raw PCM cache excludes
final filter/pause settings; export_key includes raw keys and final settings. No final audio cache.
Task directory and cache root must not overlap; final exports are copies independent of the cache.

Bounds: input <=1 MiB, document <=2000 chunks, chunks <=600 seconds, final <=2 hours/128 MiB MP3.
PCM fixed mono s16le at 24000 Hz; streaming file I/O. Serial runner per cache root. Persist one task
manifest atomically after each transition; no unbounded log file. SIGKILL leaves recognizable staging
which is reconciled only under locks on next invocation. No global pytest temp directory reuse.

Review focus: abbreviation/long-token loss; interrupted publication vs manifest update; corrupt cache
or local block; stale consent on resume; duplicate launch/child process lifetime; global SRT offset
at non-integer millisecond chunk boundaries; input/config tampering. Test these at their owning layer.

Delivery verification: 260 related offline/unit/integration checks passed in 10.93s; Ruff check,
format, compileall and CLI/import checks passed. Real short FFmpeg/PCM and copied historical schema
1/2 exports validated. No real long-form TTS, full-task SIGKILL, disk-full experiment or listening
performed. Independent review findings (early duration bound and schema 3 model identity validation)
fixed and re-reviewed. Final evidence and exact code commit are in the local G3_TEST_HANDOFF.md.
