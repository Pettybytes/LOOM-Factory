/**
 * Caption coverage: how much of the speech in the transcribed audio the
 * written cues actually cover.
 *
 * A transcription can stop before the end of the audio, or skip a quiet
 * stretch, and still exit cleanly; the draft then simply has no captions
 * there. After `caption` transcribes, the audio is measured with ffmpeg's
 * silencedetect (the same pass `detect-silence` runs): its keep spans are the
 * speech. The cue spans are subtracted from them, and what is left over —
 * speech no cue covers — is reported. All times are the audio's own seconds,
 * the same clock the cues are timed on.
 *
 * The span math (`mergeSpans`, `subtractSpans`, `captionCoverage`) is pure and
 * asserted without ffmpeg; `measureSpeech` is the live pass and never throws:
 * a missing ffmpeg or an unreadable file becomes a `skipped` reason.
 */
/** A span of the audio, in seconds. */
export interface TimeSpan {
    start: number;
    end: number;
}
export interface CaptionCoverage {
    /** Seconds of non-silent audio. */
    speech_s: number;
    /** Seconds of that speech a cue covers. */
    captioned_s: number;
    /** captioned_s / speech_s (1 when the audio holds no speech). */
    ratio: number;
    /** Speech no cue covers, each span at least the --min-uncaptioned length. */
    uncaptioned: Array<{
        start_s: number;
        end_s: number;
    }>;
    /** Seconds of the listed spans that come after the last cue ends. */
    tail_uncaptioned_s: number;
}
export type CoverageReport = CaptionCoverage | {
    skipped: string;
};
/** Shortest uncaptioned speech span reported, seconds. */
export declare const DEFAULT_MIN_UNCAPTIONED = 1.5;
/** Sort spans and merge the ones that overlap or touch. Empty spans drop. */
export declare function mergeSpans(spans: TimeSpan[]): TimeSpan[];
/** The parts of `from` that no span of `minus` covers. */
export declare function subtractSpans(from: TimeSpan[], minus: TimeSpan[]): TimeSpan[];
/**
 * Compare the speech spans with the cue spans. `uncaptioned` lists only the
 * uncovered speech spans of at least `minUncaptioned` seconds; shorter ones
 * (a breath, a cue ending a few frames early) still count against
 * captioned_s and ratio.
 */
export declare function captionCoverage(speech: TimeSpan[], cues: TimeSpan[], minUncaptioned?: number): CaptionCoverage;
/**
 * The speech in `audioPath` as spans (silencedetect's keep spans, unpadded),
 * or why it could not be measured. Never throws: the coverage check must not
 * fail a caption run that already succeeded.
 */
export declare function measureSpeech(audioPath: string, ffmpegCmd: string): TimeSpan[] | {
    skipped: string;
};
/** The one stderr line a run with uncaptioned speech prints. */
export declare function uncaptionedWarning(coverage: CaptionCoverage): string;
