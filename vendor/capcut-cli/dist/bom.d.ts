/**
 * Lenient text reads: strip a leading UTF-8 BOM (U+FEFF). Windows PowerShell's
 * `Set-Content` (and some editors) prepend one to text files; `JSON.parse`
 * rejects it and the SRT/ASS/JSONL parsers would misread the first token.
 * Applied at every user-supplied text/JSON read path (draft files, presets,
 * @file arguments, stdin, subtitle files, compile specs, config). Writes never
 * emit a BOM, so saving a BOM'd file through the CLI drops it.
 */
export declare function stripBom(text: string): string;
/** A text file BOM-stripped, or null when it does not exist; other read errors propagate. */
export declare function readTextIfPresent(path: string): string | null;
/** `raw` parsed as JSON when it is a plain object; null for an array, a scalar or a parse error. */
export declare function parseJsonObject(raw: string): Record<string, unknown> | null;
/**
 * A JSON metadata file as BOM-stripped text (`raw`, null when the file is
 * absent) and as a plain object (`obj`, null when absent or not an object).
 * Callers tell "missing" from "corrupt" by which of the two is null; read
 * errors other than absence propagate.
 */
export declare function readJsonObjectFile(path: string): {
    raw: string | null;
    obj: Record<string, unknown> | null;
};
/** Read UTF-8 subtitles without silently replacing malformed source bytes. */
export declare function readSubtitleText(path: string | number): string;
