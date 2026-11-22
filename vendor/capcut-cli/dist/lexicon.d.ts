/**
 * Pronunciation lexicon for `tts --lexicon`: rewrite only the text handed to
 * the TTS engine ("Nguyen" -> "Win"), never what the draft shows.
 *
 * Matching is leftmost-longest over the source text. A rule whose first/last
 * character is a word character (letter, digit, mark, underscore) only matches
 * where the neighbouring text character is not one, so "SQL" never fires inside
 * "MySQLite". CJK scripts have no word boundaries: CJK characters neither need
 * nor block a boundary, so a CJK rule matches anywhere and "SQL" still matches
 * in "我用SQL". Offsets are JS string (UTF-16 code unit) indices into the
 * source text. Pure: no I/O, so the matcher is unit-testable on its own.
 */
export interface LexiconRule {
    text: string;
    say: string;
    /** Default true. */
    case_sensitive: boolean;
}
export interface LexiconApplied {
    rule_text: string;
    say: string;
    /** UTF-16 code unit offsets into the source text, end exclusive. */
    source_start: number;
    source_end: number;
}
export interface LexiconResult {
    spoken_text: string;
    applied: LexiconApplied[];
}
/** A lexicon problem carrying its refusal gate id (`lexicon-invalid` / `lexicon-ambiguous`). */
export declare class LexiconError extends Error {
    readonly gate: "lexicon-invalid" | "lexicon-ambiguous";
    constructor(gate: "lexicon-invalid" | "lexicon-ambiguous", message: string);
}
/**
 * Validate a parsed lexicon document: `{ "rules": [...] }` or a bare array of
 * `{ text, say, case_sensitive? }`. Throws LexiconError(lexicon-invalid).
 */
export declare function parseLexicon(doc: unknown): LexiconRule[];
/**
 * Apply the rules to `source`: at each position the longest matching rule
 * wins; equally long matches that would say different things refuse with
 * LexiconError(lexicon-ambiguous). Replaced spans are not rescanned.
 */
export declare function applyLexicon(source: string, rules: readonly LexiconRule[]): LexiconResult;
