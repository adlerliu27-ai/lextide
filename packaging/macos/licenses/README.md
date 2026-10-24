# Offline license and attribution references

Status: **DEVELOPMENT_RELEASE_PENDING**. These files support local bundle inspection;
they are not a claim that a public distribution has completed its license obligations.

`SOURCE_MANIFEST.json` records source URLs or original installed-file paths, retrieval
timestamps, byte counts, and SHA256 values for **90 unmodified source files** collected
on 2026-10-18 and 2026-10-20 (2,571,567 bytes before this README and the manifest).
Payload hashes are checked against this manifest. There were no failed downloads.

The collection contains:

- Qt/PySide 6.12.0 license texts from the official Qt source repository, including
  LGPL 2.1, LGPL 3, GPL 2, GPL 3, the Qt GPL exception, and the GFDL covering the
  copied Qt documentation. An exception applies only to upstream files which
  expressly carry it; it does not relicense Qt or application dependencies generally.
- Official Qt documentation snapshots, including 60 third-party attribution pages
  for relevant module families. These are a reference **superset**, not a binary-level
  bill of materials. A listed third party may be absent on this platform or build.
  The HTML is unmodified and contains upstream style/script links; its notice text
  is saved locally, but no complete offline documentation website is claimed.
- PySide's original README and one unmodified source file carrying its copyright
  and SPDX header. That file is a source reference, not all PySide copyrights or
  complete corresponding source.
- FFmpeg 9.0.1's original `LICENSE.md` and `COPYING.LGPLv2.1`, and Qt's FFmpeg
  attribution page. The actual wheel libraries report LGPL 2.1 or later; their
  binary configuration evidence is recorded separately in the G5 audit.
- Installed CPython 3.14.6's original license and incorporated-software documentation.
  Python's documentation explicitly describes its incorporated-software list as
  incomplete. OpenSSL 3.5.7's original license is included separately.
- Native Zstandard 1.5.7's original LICENSE (BSD-3-Clause option) and COPYING
  (alternative GPLv2 option). The local bundle records the BSD option; Python's
  separate pyzstd binding notice does not replace Meta's native library notice.

The build also copies LICENSE/COPYING/NOTICE files from installed distribution
metadata, preserving package-relative paths. Required files such as aiohttp's
vendor/llhttp license, markdown-it's extra license, and yarl/propcache NOTICE must
not be lost through basename deduplication.

Remaining work before any public distribution includes actual binary/SBOM matching,
complete applicable notices, matching LGPL corresponding source/build materials,
verified replacement or recombination instructions, project/artwork permissions,
and the separately authorized signing/notarization process. See
`docs/release/G5_LICENSE_AUDIT.md` for evidence and precise limitations.

The external Homebrew FFmpeg 9.0.2 GPL-enabled binary, optional Kokoro runtime,
eSpeak/phonemizer dependency chain, and model/voice assets are not licensed for
redistribution by this collection and are outside the intended application payload.
