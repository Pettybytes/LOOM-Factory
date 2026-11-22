# Native text template

`capcut-native-text.json` is a `capcut-cli save-template` export of a native
CapCut 9.2 text object. It retains the native background capability flag and
companion material structure that the CLI's blank text factory does not clone.

Source: the visually inspected `LOOM render-1.5 20261121-1946` text segment,
which was built from Bryan's native `1121 (1)` reference structure. The app
verified its editable Noto Serif text and rounded white card; its size and
placement were not accepted.

The export was applied to a scratch project using `apply-template`, set to
neutral text `Text`, zero position, empty font path, and re-exported using
`save-template`. No live reference project was modified. The resulting template
contains no clip copy, media path, or user font path.

At export time it had unit scale. LOOM replaces its text, timing, font, colors,
size, wrapping, background and position for each clip using the CLI's template
and text-style interfaces. It is a native object scaffold, not a global style.
Its inherited card padding and radius remain provisional until app validation;
see `../CAPCUT_COVERAGE.md` for the remaining fidelity limits.
