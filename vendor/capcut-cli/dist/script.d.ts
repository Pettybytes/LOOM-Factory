/**
 * Which script a caption is written in, and what that changes: the limits a
 * line and a reading speed are held to (lint), and how whisper's words are
 * joined and grouped into cues (caption). Latin and everything else share one
 * bucket; Chinese, Japanese and Korean each get their own because their
 * subtitling conventions differ from Latin ones and from each other.
 */
/** The script a caption is written in, as far as the line-length and
 * reading-speed rules care: Latin (and everything else), or one of the three
 * CJK scripts whose subtitling conventions differ from Latin ones. */
export type CaptionScript = "latin" | "zh" | "ja" | "ko";
export interface ScriptLimit {
    maxCharsPerLine?: number;
    maxCharsPerSecond?: number;
}
/** Caption limits that replace `maxCharsPerLine` / `maxCharsPerSecond` for a
 * cue written in the given script. An absent key falls back to the Latin
 * value for that rule. */
export type ScriptLimits = Partial<Record<Exclude<CaptionScript, "latin">, ScriptLimit>>;
/**
 * Where the Latin defaults (42 characters per line, 20 per second) come from
 * a Latin alphabet, a CJK character carries a syllable or a word, so a line
 * a third as long is already full and a third the speed is already fast:
 * the streaming style guides sit at 16 characters per line and 9 per second
 * for Simplified Chinese, 13 and 4 for Japanese, 16 and 12 for Korean. A
 * 30-character Chinese line passing a 42-character check is the failure this
 * table exists for.
 */
export declare const CJK_SCRIPT_LIMITS: ScriptLimits;
/**
 * The script a caption's limits should follow: CJK when at least half of its
 * visible characters are CJK, then Japanese if any kana is present, Korean if
 * any hangul, else Chinese. A mixed caption below that share (a Latin line
 * with one CJK name) keeps the Latin limits.
 */
export declare function captionScript(text: string): CaptionScript;
/**
 * How a cue's words are joined into its text: Chinese and Japanese are written
 * without spaces, so a space between two of whisper's tokens would land in the
 * caption; Korean and Latin scripts are written with them.
 */
export declare function wordSeparator(script: CaptionScript): string;
export interface GroupingDefaults {
    /** --max-words default for karaoke cues. */
    karaokeMaxWords: number;
    /** --max-chars default for karaoke cues. */
    karaokeMaxChars: number;
    /** --max-chars default when a script line outgrows one cue. */
    lineMaxChars: number;
}
/**
 * The defaults `caption` groups words with when the caller sets nothing. A
 * "word" whisper emits for Chinese or Japanese is a character or a short
 * token, so the Latin four-words-per-cue rule would cut a sentence into
 * fragments; there the character cap is the only bound, and it is the line
 * width the lint limits already hold captions to (16 zh, 13 ja, 16 ko).
 */
export declare function groupingDefaults(script: CaptionScript): GroupingDefaults;
