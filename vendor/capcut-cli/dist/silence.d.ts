import { type SceneSegment } from "./scenes.js";
export interface SilenceSpan {
    start: number;
    end: number | null;
}
export interface SilenceDetectOptions {
    thresholdDb?: number;
    minSilence?: number;
    pad?: number;
    limit?: number;
    ffmpegCmd?: string;
    ffprobeCmd?: string;
    timeoutMs?: number;
    maxBufferBytes?: number;
}
export interface SilenceReport {
    media: string;
    threshold_db: number;
    min_silence: number;
    pad: number;
    limit: number | null;
    duration: number | null;
    duration_us: number | null;
    duration_source: "container" | "ffmpeg-header" | null;
    silences: SceneSegment[];
    keeps: SceneSegment[];
}
/**
 * Parse the stderr of `-af silencedetect=noise=TdB:d=D -f null -`. Each span
 * emits two lines:
 *   [silencedetect @ 0x...] silence_start: 1.33915
 *   [silencedetect @ 0x...] silence_end: 3.55712 | silence_duration: 2.21798
 * A silence_start with no matching silence_end means the input ended during
 * the silence; the span is kept with end null.
 */
export declare function parseSilenceSpans(stderr: string): SilenceSpan[];
/**
 * Bound spans to a known duration: an open-ended span (silence to EOF) closes
 * at `duration`, ends clamp to it, and spans starting at/after it drop. With
 * an unknown duration the spans pass through untouched.
 */
export declare function closeSilenceSpans(spans: SilenceSpan[], duration: number | null): SilenceSpan[];
/**
 * Shrink each span by `pad` seconds on both ends so the keep segments retain a
 * margin around speech — cutting exactly at silence_end clips the soft attack
 * of the next word mid-syllable. A span not longer than 2*pad disappears
 * (never goes negative). An open-ended span keeps its null end: no speech
 * follows a silence that runs to EOF, so only its start is padded.
 */
export declare function padSilenceSpans(spans: SilenceSpan[], pad: number): SilenceSpan[];
/** Keep the N longest silences (a silence to EOF always counts as longest,
 * earliest wins ties), returned back in time order. */
export declare function limitSilences(spans: SilenceSpan[], limit: number | undefined): SilenceSpan[];
/**
 * Complement the silence spans over [0..duration]: the KEEP segments (speech)
 * an auto-cut feeds into `cut`/`compile`. A silence running to EOF ends the
 * list — nothing to keep after it. With an unknown duration the trailing keep
 * segment is open-ended (end null). An all-silence file keeps nothing.
 */
export declare function buildKeepSpans(silences: SilenceSpan[], duration: number | null): SilenceSpan[];
/** Spans -> detect-scenes' segment shape (seconds + draft-native microseconds). */
export declare function spansToSegments(spans: SilenceSpan[]): SceneSegment[];
/**
 * Live detection. Runs ffmpeg's silencedetect filter over `mediaPath` and
 * assembles the report. Throws with an actionable message when the file or
 * ffmpeg is missing, or when ffmpeg fails (mirrors detect-scenes).
 */
export declare function detectSilence(mediaPath: string, opts?: SilenceDetectOptions): SilenceReport;
