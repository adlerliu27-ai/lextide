# Lextide asset license and scope

Copyright © 2026 Mingjia Liu.

This document defines the license terms for the non-code assets shipped with the
Lextide source tree. The Python source code, tests, scripts, configuration and
documentation prose are licensed under the MIT License in `LICENSE`.

The scope boundary is:

| Scope | Terms |
|---|---|
| Original project source code, tests, scripts, configuration, documentation prose | **MIT** (`LICENSE`) |
| Brand Assets: app icon PNG, brand symbol SVG (`src/lextide/desktop/assets/brand/**`) | **Brand reserved** — see `TRADEMARKS.md` |
| Functional UI icons and design tokens (`src/lextide/desktop/assets/icons/**`, `src/lextide/desktop/assets/design-tokens.json`) | **MIT-compatible** — see below |
| Documentation screenshots (`docs/images/**`) | **Documentation use** — see below |
| Third-party code, models, voices, fonts and license texts | **Their own terms** — see `THIRD_PARTY_NOTICES.md` |

## 1. Brand Assets — reserved

`src/lextide/desktop/assets/brand/lextide-app-icon-1024.png` and
`src/lextide/desktop/assets/brand/lextide-symbol-editable.svg` are Brand Assets.
They are **not** MIT-licensed. Their use is governed by `TRADEMARKS.md`:
personal and educational use is permitted, commercial use as another product's
brand requires prior written permission.

## 2. Functional UI icons and design tokens — MIT-compatible

The files in `src/lextide/desktop/assets/icons/` and
`src/lextide/desktop/assets/design-tokens.json` are the project's own functional
interface glyphs and design metadata. They are made available under the same
terms as the MIT License, so that the interface remains reusable together with
the code.

You may copy, modify and redistribute these files, including in commercial work,
provided that you do not present them as Lextide brand marks or as indicating
endorsement by Mingjia Liu. If you redistribute them, keep this notice or the
`LICENSE` file with them.

Source evidence: these glyphs come from the Lextide UI design pack created for
this project, whose own notes state that the glyphs were purpose-drawn and that
no Lucide or SF Symbols artwork was redistributed. This is a provenance record,
not an independent legal audit. If any third-party right is ever established for
one of these files, that right prevails and the file will be corrected or
removed.

## 3. Documentation screenshots — documentation use

`docs/images/g6-english.png` and `docs/images/g6-chinese.png` are official
Lextide interface screenshots. They contain Brand Assets and interface design.

Permitted: display in the README, in personal study notes, and in non-commercial
tutorials or project introductions with attribution to Lextide and Mingjia Liu.

Not permitted without separate written permission: use of these screenshots in
independent commercial promotion, advertising or merchandise.

These screenshots show a self-authored four-sentence English example only. They
contain no personal documents, task content, account details or local paths.

## 4. Files covered by this document

The complete inventory of 36 asset files, with byte sizes, SHA-256 digests,
source evidence and per-file classification, is recorded in
`docs/release/ASSET_RELEASE_MANIFEST.md`.

## 5. Third-party material

Third-party dependencies keep their original licenses and notices. This document
does not relicense them, and it does not grant any right in third-party models,
voices, fonts or binaries. See `THIRD_PARTY_NOTICES.md`.

## 6. No warranty

These asset terms are provided without warranty of any kind. They are a
statement of the project owner's permissions, not legal advice, and not a
guarantee that no third party holds a claim in any particular file.
