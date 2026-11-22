import { type AlignmentReport } from "./align.js";
import { type CoverageReport } from "./caption-coverage.js";
import type { Draft } from "./draft.js";
import { type TextStylePreset } from "./preset.js";
import { type CaptionScript } from "./script.js";
import { type TranscriptCacheKey, type TranscriptCacheState } from "./transcript-cache.js";
export interface CaptionOptions {
    /**
     * --words: the raw JSON of word timings from an external aligner. When set,
     * Whisper does not run; see parseWordTimings for the accepted shapes.
     */
    wordsJson?: string;
    wordsSource?: string;
    audio?: string;
    audioStream?: number;
    ffmpegCmd?: string;
    fromSegment?: string;
    cloud?: boolean;
    translate?: string;
    whisperCmd?: string;
    whisperModel?: string;
    language?: string;
    trackName?: string;
    styleRef?: string;
    preset?: TextStylePreset;
    whisperEngine?: "auto" | "openai" | "whisper-cpp" | "faster-whisper";
    karaoke?: boolean;
    wordReveal?: boolean;
    minScriptMatch?: number;
    maxWords?: number;
    maxChars?: number;
    maxGapMs?: number;
    highlightWords?: string[];
    keywordColor?: string;
    keywordSize?: number;
    colorCycle?: string[];
    /**
     * --script: the known transcript. Whisper's word timing is kept, the
     * script's wording is used (see align.ts); each non-empty script line is a
     * cue boundary, long lines still split at --max-chars.
     */
    scriptText?: string;
    /** false (--no-coverage) skips measuring which speech the cues leave uncaptioned. */
    coverage?: boolean;
    /** Shortest uncaptioned speech span the coverage check lists, seconds (default 1.5). */
    minUncaptioned?: number;
    /** false (--no-cache) neither reads nor writes the transcript cache. */
    cache?: boolean;
}
export interface CaptionWord {
    word: string;
    startUs: number;
    endUs: number;
}
export interface CaptionCue {
    startUs: number;
    endUs: number;
    text: string;
    words?: CaptionWord[];
}
export interface CaptionResult {
    ok: boolean;
    cues: number;
    language?: string;
    track_name: string;
    first_cue?: {
        start_us: number;
        text: string;
    };
    last_cue?: {
        start_us: number;
        text: string;
    };
    /** Absent with --words: no audio was read. */
    source_audio?: string;
    engine: "whisper-cli" | "shell" | "openai" | "stdin-srt" | "words" | "capcut-cloud";
    engine_name?: string;
    words?: number;
    karaoke?: boolean;
    word_reveal?: boolean;
    audio_stream?: number;
    translated_to?: string;
    /** --translate: what went to CapCut's translation, or under --dry-run what would have. */
    translation?: {
        language: string;
        lines: number;
        to: string;
        sent: boolean;
        kept_original?: number;
    };
    /** --words: the file the timings came from, its detected shape, and how
     * many entries had no usable timing and were left out. */
    source_words?: string;
    words_format?: WordsFormat;
    words_skipped?: number;
    keyword_matches?: number;
    color_cycle?: number;
    /** --script alignment quality (only when a script was given). */
    script?: AlignmentReport;
    /** Speech in the transcribed audio that no cue covers (absent with --words or --no-coverage). */
    coverage?: CoverageReport;
    /** Whether the transcription came from the transcript cache (absent with --words: nothing is transcribed). */
    transcript_cache?: TranscriptCacheState;
    /** The script the transcript is written in, which chose the word separator
     * and the --max-words / --max-chars defaults (see script.ts). */
    caption_script: CaptionScript;
}
/**
 * Generate captions by running whisper on the project's audio and emitting
 * real CapCut caption-track segments (not text-segment mimics — fixes the
 * import-srt pain documented in pyJianYingDraft #148).
 *
 * Whisper integration is shell-out; user supplies the binary path. We support
 * the canonical whisper.cpp output naming convention (`<base>.srt`).
 *
 * Architecture:
 *   1. Resolve audio file (either --audio or extracted from --from-segment)
 *   2. Spawn whisper CLI; capture SRT output
 *   3. Parse SRT into caption cues
 *   4. Create captions track + real subtitle material per cue
 */
/** Where the words come from, as far as the source rules below need to know. */
export interface TranscriptionSourceOptions {
    words: boolean;
    audio?: string;
    fromSegment?: string;
    cloud?: boolean;
    audioStream?: number;
    ffmpegCmd?: string;
    whisperCmd?: string;
    whisperEngine?: string;
    whisperModel?: string;
}
/**
 * The options that contradict the chosen transcription source, as the refusal
 * text, or undefined when they agree. --words replaces transcription entirely,
 * so every option that only steers Whisper (or the audio it would hear) is a
 * contradiction; --cloud replaces Whisper, so the --whisper-* options are.
 */
export declare function transcriptionSourceConflict(o: TranscriptionSourceOptions): string | undefined;
/** The option checks every caption run makes before it reads or sends any audio; a --cloud dry run makes them too. */
export declare function checkCaptionOptions(opts: CaptionOptions): void;
export declare function captionDraft(draft: Draft, opts: CaptionOptions): Promise<CaptionResult>;
/**
 * The transcript cache key for this run: the transcribed audio's bytes plus
 * every setting that changes what the engine returns. --cloud takes no model
 * or language (the service detects the language), so those stay null there.
 */
export declare function transcriptCacheKey(opts: CaptionOptions, audioSha256: string): TranscriptCacheKey;
export declare function buildAudioStreamInvocation(audio: string, stream: number, output: string): string[];
/**
 * What `caption --cloud` would send, read without sending it: the file, its
 * size and length. `--dry-run` reports this instead of uploading, the way
 * translate sends nothing in a dry run. With --audio-stream the upload would
 * be that stream extracted from this file. The options are checked as in a
 * real run, so a dry run refuses what the run would refuse.
 */
export declare function planCloudUpload(draft: Draft, opts: CaptionOptions): {
    path: string;
    bytes: number;
    duration_ms: number;
    audio_stream?: number;
};
/** CapCut's recognition picks the spoken language itself; the request has no language field. */
export declare function cloudLanguageWarning(opts: Pick<CaptionOptions, "cloud" | "language">): string | undefined;
interface TranscriptionResult {
    cues: CaptionCue[];
    words: CaptionWord[];
    engine: "openai" | "whisper-cpp" | "faster-whisper" | "words" | "capcut-cloud";
}
/**
 * The word-timing shapes --words recognises:
 *   whisper   — openai-whisper / whisper.cpp JSON, segments[].words[] {word,start,end}
 *   whisperx  — WhisperX, word_segments[] (or segments[].words[] carrying a score)
 *   word-list — a plain array of {word|text|char, start, end} in seconds, or
 *               start_ms/end_ms, or start_time/end_time (Qwen3-ForcedAligner)
 *   whisper-cpp — whisper.cpp full JSON (whisper-cli -ojf), transcription[].tokens[]
 */
export type WordsFormat = "whisper" | "whisperx" | "word-list" | "whisper-cpp";
export interface ParsedWordTimings {
    format: WordsFormat;
    words: CaptionWord[];
    /** Entries with text but no usable start/end (WhisperX leaves digits unaligned). */
    skipped: number;
}
/**
 * Parse word timings from an external aligner into caption words, detecting
 * the shape. Entries without timing are skipped and counted; an entry whose
 * end precedes its start, or that starts before the previous timed entry, is
 * refused with its index, since every cue built from it would be wrong.
 */
export declare function parseWordTimings(raw: string): ParsedWordTimings;
/**
 * The Whisper binary to run: the caller's `--whisper-cmd` wins, otherwise the
 * first dialect actually installed on PATH, otherwise the historical default.
 * `doctor` has always probed all three names; `caption` used to spawn only
 * `whisper`, so on a host with whisper.cpp (`whisper-cli`) installed and no
 * `whisper`, doctor said ok and caption died with ENOENT.
 */
export declare function whisperCommand(opts: CaptionOptions): string;
export declare function detectEngine(opts: CaptionOptions): TranscriptionResult["engine"];
export declare function buildWhisperInvocation(engine: TranscriptionResult["engine"], audio: string, model: string, language: string, outputDir: string, json: boolean): {
    args: string[];
    prefix: string;
    extension: ".json" | ".srt";
};
export declare function parseWhisperJson(raw: string): {
    cues: CaptionCue[];
    words: CaptionWord[];
};
export declare function wordsFromCues(cues: CaptionCue[]): CaptionWord[];
/**
 * Group timed words into cues: a cue closes when the next word would exceed
 * maxWords, would push the joined text past maxChars, or starts after a gap
 * longer than maxGapUs. `separator` is what joins the words into the cue's
 * text — a space for Latin and Korean, nothing for Chinese and Japanese
 * (wordSeparator in script.ts) — and counts towards maxChars like any other
 * character.
 */
export declare function groupWords(words: CaptionWord[], maxWords?: number, maxChars?: number, maxGapUs?: number, separator?: string): CaptionCue[];
export {};
