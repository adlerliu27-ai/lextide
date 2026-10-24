# Third-party dependencies and source-release boundaries

v0.6.1 public source release (2026-10-24): the project owner has approved the
project license. Original project code, tests, scripts, configuration and
documentation prose are licensed under the [MIT License](LICENSE)
(Copyright (c) 2026 Mingjia Liu). Brand artwork is licensed separately under
[TRADEMARKS.md](TRADEMARKS.md) and [ASSETS_LICENSE.md](ASSETS_LICENSE.md).

MIT applies only to project-owned work. All third-party terms and resource
ownership remain separate and are not relicensed here. See the
[source publication checklist](docs/release/GITHUB_OPEN_SOURCE_CHECKLIST.md) and the
[file-level asset review](docs/release/ASSET_RELEASE_MANIFEST.md).

G6 engineering and user acceptance are complete. This candidate contains project
source, configuration, tests, reviewed documentation/assets, and selected unmodified
upstream license texts. It does not contain installed dependency packages, Python/Qt
runtimes, FFmpeg executables, model/voice binaries, or a macOS app. Dependencies are
installed separately under their own terms. MIT, if approved for project-owned work,
will not relicense any of those components or the separately reviewed artwork.
Project ownership for the MIT grant is declared in `LICENSE`; it is not inferred
from a Git author name or local username.

Dependencies use normal package installation without vendoring or modifying their source.
`uv.lock` records exact distributions and hashes. This is a development inventory, not a completed
application-distribution license review. G0–G2 did not make an app bundle or public release;
the later G5 local development bundle is covered separately by
[G5_LICENSE_AUDIT.md](docs/release/G5_LICENSE_AUDIT.md).

The G6 lock review found 34 entries in `uv.lock` (including Lextide) and 14 in
`offline-runtime/uv.lock` (including its environment project). A lock entry is not
proof that a component is copied into a source release or bundled app. Platform
conditional build dependencies, such as pefile and pywin32-ctypes, require their
own Windows inventory if that platform is distributed. No G6 Windows binary review
is claimed.

| Direct dependency | Version | License/source |
|---|---|---|
| edge-tts | 7.3.1 | [Upstream](https://github.com/rany2/edge-tts), [PyPI](https://pypi.org/project/edge-tts/7.3.1/); LGPL-3.0; upstream SRT composer carries a separate MIT notice |
| aiohttp | 3.14.5 | Installed metadata: Apache-2.0 AND MIT; [project](https://github.com/aio-libs/aiohttp) |
| certifi | 2026.7.22 | Installed metadata: MPL-2.0; [project](https://github.com/certifi/python-certifi) |
| pytest (development) | 8.4.2 | MIT; [project](https://github.com/pytest-dev/pytest) |
| ruff (development) | 0.14.0 | [Project/license](https://github.com/astral-sh/ruff/blob/main/LICENSE) |
| hatchling (build) | 1.27.0 | [Project](https://github.com/pypa/hatch) |

ffmpeg/ffprobe are external executables from the local environment, not copied into the project.
Their redistribution terms depend on build configuration; no binary distribution is performed here.

Before public release, review all locked runtime dependencies and any bundled executable, retain
required notices. The project's own license and Logo distribution scope are now
recorded in `LICENSE`, `TRADEMARKS.md` and `ASSETS_LICENSE.md`.
No historical captures, private tokens, proprietary articles, or unlicensed browser-extension source
are project inputs or test fixtures.

## G2 optional offline runtime (Lextide 0.2.0)

`offline-runtime/uv.lock` pins a separate Python 3.13 environment. The source CLI supports the Python range declared in `pyproject.toml`
(Python 3.13 is the tested core CI target); `kokoro-onnx` 0.6.1 declares Python >=3.10,<3.14. The following inventory records the
actual lock on 2026-10-15, not an assertion that the runtime has been installed or inference tested.

| Locked package | Version | License / primary source |
|---|---|---|
| kokoro-onnx | 0.6.1 | MIT; [upstream LICENSE](https://github.com/thewh1teagle/kokoro-onnx/blob/main/LICENSE) |
| onnxruntime | 1.31.0 | MIT; [PyPI metadata](https://pypi.org/pypi/onnxruntime/1.31.0/json) |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0; [metadata](https://pypi.org/pypi/numpy/2.5.4/json) |
| phonemizer | 3.4.0 | GPL-3.0-or-later; [source license header](https://github.com/bootphon/phonemizer/blob/master/phonemizer/__init__.py) |
| espeakng-loader | 0.2.4 | Wrapper MIT; bundled eSpeak NG GPL-3.0-or-later; see below |
| attrs | 26.1.0 | MIT; [metadata](https://pypi.org/pypi/attrs/26.1.0/json) |
| cloudpickle | 3.1.2 | BSD-3-Clause; [metadata](https://pypi.org/pypi/cloudpickle/3.1.2/json) |
| dlinfo | 2.0.0 | MIT; [metadata](https://pypi.org/pypi/dlinfo/2.0.0/json) |
| flatbuffers | 25.12.19 | Apache-2.0; [metadata](https://pypi.org/pypi/flatbuffers/25.12.19/json) |
| joblib | 1.6.0 | BSD-3-Clause; [metadata](https://pypi.org/pypi/joblib/1.6.0/json) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause; [metadata](https://pypi.org/pypi/packaging/26.3/json) |
| protobuf | 7.36.2 | BSD-3-Clause; [metadata](https://pypi.org/pypi/protobuf/7.36.2/json) |
| typing-extensions | 4.16.0 | PSF-2.0; [metadata](https://pypi.org/pypi/typing-extensions/4.16.0/json) |

The [espeakng-loader wrapper](https://github.com/thewh1teagle/espeakng-loader/blob/main/LICENSE)
is MIT. Its [build workflow](https://github.com/thewh1teagle/espeakng-loader/blob/main/.github/workflows/build.yml)
selects eSpeak NG 1.52.0; [eSpeak NG](https://github.com/espeak-ng/espeak-ng#license-information)
is GPL version 3 or later. A separate environment or subprocess does not remove license obligations.
Do not describe the complete offline dependency chain as MIT/Apache-only. No runtime or model is
vendored or redistributed by this G2 implementation; application distribution remains unreviewed.

The optional Kokoro v1.0 int8 model and voices bundle use the
[original model's Apache-2.0 license](https://huggingface.co/hexgrad/Kokoro-82M).
`src/lextide/model_catalog.py` pins their URLs, exact byte sizes and SHA256 digests from the
[publisher release API](https://api.github.com/repos/thewh1teagle/kokoro-onnx/releases/tags/model-files-v1.1),
queried 2026-10-15 (assets updated 2026-08-18). Total asset size is 142333725 bytes.
Model installation is explicit; the source resource and its required notices must be retained when
planning any later redistribution. This inventory is not a completed distribution license audit.

## G4 optional desktop inventory (2026-10-18)

| Package / resource | Version | Recorded terms / source |
|---|---|---|
| PySide6-Essentials, PySide6-Addons, shiboken6 | 6.12.0 | Installed wheel metadata: LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; [Qt for Python licensing](https://doc.qt.io/qtforpython-6/commercial/index.html) |
| pypdf | 6.21.1 | BSD-3-Clause; installed LICENSE and [publisher metadata](https://pypi.org/project/pypdf/6.21.1/) |
| markdown-it-py | 4.2.0 | MIT; installed LICENSE and upstream markdown-it notice |
| mdurl | 0.1.2 | MIT; installed LICENSE |
| Logo and original line icons | User design pack | User authorized local integration; retain ownership and confirm public distribution permission before release |

The app uses Qt Widgets / SVG / Multimedia. PySide6's optional WebEngine/Pdf wheels are not
dependencies and are not installed. The local Qt Multimedia runtime reports bundled FFmpeg
9.0.1, LGPL 2.1 or later; the existing synthesis tools remain the separately installed FFmpeg
9.0.2. Any future app distribution must inventory the actual Qt modules, multimedia codecs,
dynamic libraries and their notices, including corresponding source/relinking obligations where
applicable. Optional installation does not remove those obligations. No packaging, distribution
license clearance or legal conclusion is claimed here; the earlier Kokoro/eSpeak review remains open.

Selected runtime artwork/tokens are in `src/lextide/desktop/assets/`; exact current
paths, hashes and rights status are listed in `ASSET_RELEASE_MANIFEST.md` above.
Original AI/SVG research assets and full design HTML/PDF were not edited or vendored wholesale.
