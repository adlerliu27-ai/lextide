# Contributing to Lextide

Lextide 0.6.1 is the first public source release. The original project code is
licensed under the [MIT License](LICENSE) (Copyright (c) 2026 Mingjia Liu). Brand
artwork is reserved under [TRADEMARKS.md](TRADEMARKS.md), and the asset scope is
defined in [ASSETS_LICENSE.md](ASSETS_LICENSE.md). This repository is source only;
it does not include or authorize a public `.app` distribution.

By submitting a contribution you agree that your contribution is licensed under
the same MIT terms as the surrounding project code. Do not contribute third-party
artwork or code you do not have the right to license. If you contribute artwork,
say so in the pull request so its terms can be recorded separately.

## Local checks

The current core workflow targets macOS. Python 3.13 and uv 0.12.5 reproduce the
minimal CI environment. Run from the repository root:

```sh
uv sync --locked --no-default-groups --group dev --python 3.13
uv run --no-sync --offline --python 3.13 ruff check src tests scripts packaging/macos/launcher.py
uv run --no-sync --offline --python 3.13 ruff format --check src tests scripts packaging/macos/launcher.py
uv run --no-sync --offline --python 3.13 python -m compileall -q src
mkdir -p .artifacts/ci
uv run --no-sync --offline --python 3.13 python -m pytest \
  --basetemp=.artifacts/ci/pytest \
  tests/test_text_cli.py tests/test_edge.py tests/test_jobs.py \
  tests/test_models.py tests/test_subtitles.py tests/test_subtitles_edge_prefixes.py \
  tests/test_g61_quotes.py \
  tests/test_g3_document.py tests/test_g3_storage.py tests/test_g5_packaging.py \
  tests/test_cleaning.py tests/test_g61_cleaning.py tests/test_source_snapshot.py
```

Dependency installation uses the package registry. These tests then run without
network access, GUI, FFmpeg or downloaded models. `tests/conftest.py` rejects DNS
and socket connections; Edge and model-download tests use local fakes. Passing this
subset does not demonstrate real speech generation, audio quality, desktop behavior
or package acceptance. Run the relevant existing tests and real application checks
for a change affecting those areas, and report what was actually exercised.

Do not update lockfiles merely to make an unrelated change pass. Explain a dependency
change and preserve exact versions, hashes and notices. The optional offline runtime
has its own Python 3.13 environment and `offline-runtime/uv.lock`.

## Changes and evidence

Describe the user-visible problem, the resulting behavior and the validation result.
Keep changes focused. Preserve explicit online consent, original inputs, model hash
verification, atomic output publication and existing job/cache compatibility.
Use synthetic or licensed fixtures. Include tests when they protect a meaningful
behavior, especially consent, resume, integrity or data preservation.

## Privacy and distribution

- Never commit credentials, cookies, private text, research documents, user audio,
  local absolute paths, environment directories or internal handoff records.
- Review staged paths and the full publication payload. A clean working tree scan
  does not establish that earlier Git commits are safe to publish.
- Report a suspected secret privately to the maintainer without pasting its value
  into an issue or PR. Do not upload scan reports containing matched content.
- Preserve third-party license and attribution files. Project license approval
  does not relicense dependencies, models, voices, Logo or icons.
- Keep source publication and binary distribution checks separate; see
  [the release checklist](docs/release/GITHUB_OPEN_SOURCE_CHECKLIST.md) and
  [G5 binary audit](docs/release/G5_LICENSE_AUDIT.md).
