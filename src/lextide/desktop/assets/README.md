# Lextide desktop runtime assets

Selected user-provided artwork, used with the user's explicit G4 integration approval.
Public redistribution rights still require confirmation before publishing a repository or app.

Source: the owner’s read-only Lextide UI Design Pack (kept outside this repository).
The brand PNG is from `assets/brand`; original line icons are from `assets/icons`.
The supplied `developer/README-icons.md` identifies these as original icons, not SF Symbols.
Editable original artwork remains in that source pack and the separate TTS-Studio-Logo-Pack.

`design-tokens.json` is the original 1.0 reference. Final 1.1 semantic contrast colors in
`../theme.py` follow the user-approved sibling `Lextide-Interactive-Preview-v1.1.html` and
`Lextide-UI-Design-Guide-v1.1.pdf`, which take priority. Icons are tinted at runtime without
altering the original source files. Only required runtime resources are distributed here;
the HTML is not loaded or executed by the native app.

G6 adds `icons/font-size-chevron-light.svg` and `icons/font-size-chevron-dark.svg`:
two project-authored three-point chevrons for the transcript size dropdown. They are
not extracted from or modifications of the Logo design pack. Their public license,
like the project's own code, is confirmed in the repository's ASSETS_LICENSE.md.
