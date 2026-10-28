# Lextide 0.9.0 — stable release candidate

**Status: `G9_STABLE_RELEASE_CANDIDATE`.** These notes describe the candidate, not a completed independent acceptance or GitHub release. The first public release remains v0.6.1 until the reviewed update is actually published.

## Text preparation and submission

- Studio offers **Keep Original** and **English Only**. Selecting English Only prepares an editable local preview with the existing enhanced translation-removal rules; users no longer need to choose a cleaning strength.
- The original text remains available. Replacing manual edits requires confirmation and can be undone. Ambiguous passages are retained for review rather than silently discarded.
- The GUI and submission boundary check the final reading text before synthesis. Empty input and retained Chinese in an English Only/English-voice request are rejected before text upload. This addresses the old path in which an unsuitable bilingual script reached Edge before failing subtitle validation; independent retesting is still required.
- The check does not rewrite text. Explicit online approval, per-job SHA-256, edit invalidation and stale-callback checks remain in force. Non-English voices must not inherit an indiscriminate global Chinese-text prohibition.

## Existing core features included since v0.6.1

- One explicit **Send to Edge & Generate** action, or **Generate Audio** for a ready offline Kokoro engine.
- Auto engine choice based on a text-free connectivity check and actual local readiness. It never silently switches engines after a synthesis failure or changes an incompatible voice on the user's behalf.
- A compact Listen header and an expanding, full-width transcript. The unloaded-session message lives inside the transcript, with an **Open Library** action. English/Chinese text stays aligned, with the existing 12–40 px size menu and collapsible session details.
- **Focus view** gives the transcript more room by hiding secondary interface elements while keeping playback, seeking, subtitle mode, text size and a visible way to leave focus mode. Toggling it does not reload the audio or change the selected session or Chinese sidecar.
- Manual Chinese TXT/SRT import, cue-by-cue review/editing and independent sidecars bound to the English SRT identity. Missing or damaged Chinese attachments do not alter English playback.
- Persistent settings, immediate English/简体中文 interface changes, light/dark themes and a fixed settings save bar.
- Existing task management, pause/resume, PCM cache, atomic MP3/SRT exports, historical schema 1/2/3 verification and Kokoro v3 PCM filtering.

G7/G8 features were developed locally; these notes do not imply separate public v0.7.0 or v0.8.0 releases.

## Scope and limits

English extraction is deterministic and local, not a general language detector or a guarantee that every mixed passage can be safely reduced automatically. Review names, technical terms and uncertain OCR text. Removed Chinese is not automatically matched to subtitle cues.

**Not included:** automatic translation, translation APIs/models, OCR, Windows packaging or a notarized public macOS installer. Existing Chinese subtitles still require user-supplied text and manual confirmation.

The release target is **source code**. Local macOS arm64 development Apps are ad-hoc bundles with external FFmpeg and optional Kokoro runtime/models. Developer ID signing, notarization and applicable binary-distribution review remain pending; they are separate from source publication.

## Validation and publication gate

Run the current [offline CI](../../.github/workflows/ci.yml) and [contribution checks](../../CONTRIBUTING.md). Offline mocks do not establish real network synthesis, GUI behavior or user acceptance. Exact local test counts, build hashes and independent results belong to the corresponding candidate records.

DeepSeek may publish the reviewed v0.9.0 source update only after engineering tests pass, the user actually accepts the new version, and the final privacy/rights audit is ready. Preserve existing public history and v0.6.1; do not push private development history. No GitHub publication is performed by the local candidate build.
