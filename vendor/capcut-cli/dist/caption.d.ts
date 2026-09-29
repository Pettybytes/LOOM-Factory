import { type AlignmentReport } from "./align.js";
import type { Draft } from "./draft.js";
import { type TextStylePreset } from "./preset.js";
import { type CaptionScript } from "./script.js";
export interface CaptionOptions {
    audio?: string;
    audioStream?: number;
    ffmpegCmd?: string;
    fromSegment?: string;
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
    source_audio: string;
    engine: "whisper-cli" | "shell" | "openai" | "stdin-srt";
    engine_name?: string;
    words?: number;
    karaoke?: boolean;
    word_reveal?: boolean;
    audio_stream?: number;
    keyword_matches?: number;
    color_cycle?: number;
    /** --script alignment quality (only when a script was given). */
    script?: AlignmentReport;
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
export declare function captionDraft(draft: Draft, opts: CaptionOptions): CaptionResult;
export declare function buildAudioStreamInvocation(audio: string, stream: number, output: string): string[];
interface TranscriptionResult {
    cues: CaptionCue[];
    words: CaptionWord[];
    engine: "openai" | "whisper-cpp" | "faster-whisper";
}
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
