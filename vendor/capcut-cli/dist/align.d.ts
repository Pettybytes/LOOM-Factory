import type { CaptionWord } from "./caption.js";
export interface AlignedWord extends CaptionWord {
    /** True when this script word was aligned to an identical recognised word. */
    matched: boolean;
    /** Which script line (0-based) the word came from — the cue boundary hint. */
    line: number;
}
export interface AlignmentReport {
    script_words: number;
    recognized_words: number;
    /** Script words aligned to an identical recognised token. */
    matched: number;
    /** Script words aligned to a DIFFERENT recognised token (whisper mis-heard). */
    substituted: number;
    /** Script words whisper produced nothing for (timing interpolated). */
    inserted: number;
    /** Recognised words the script does not contain (ignored). */
    dropped: number;
    /** matched / script_words — below ~0.5 the script probably does not belong to this audio. */
    match_ratio: number;
}
/** Comparison form of a token: NFKC, lower-case, letters/digits only (any script). */
export declare function normalizeToken(token: string): string;
/**
 * Split a Chinese/Japanese line the way speech recognisers timestamp it:
 * Han/kana characters are individual alignment units, while Latin/digit runs
 * stay together. Punctuation is kept with the preceding spoken unit so the
 * author's visible wording survives even though normalizeToken ignores it.
 */
export declare function tokenizeCjkText(text: string): string[];
/**
 * Script text → lines → words. Blank lines are dropped; a line is a unit the
 * caller may keep together as one cue. Tokens with no letters or digits
 * (a lone "—", "...") cannot be aligned or spoken and are removed.
 */
export declare function tokenizeScript(text: string): string[][];
/** Fallback word length when the recogniser produced no word timings at all. */
export declare const DEFAULT_WORD_DURATION_US = 300000;
/**
 * Time the script against the recognised words. Every script word gets a
 * start/end; the report says how well the two sides agreed.
 */
export declare function alignScript(scriptLines: string[][], recognized: CaptionWord[]): {
    words: AlignedWord[];
    lines: AlignedWord[][];
    report: AlignmentReport;
};
