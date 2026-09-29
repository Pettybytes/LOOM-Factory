import type { SceneSegment } from "./scenes.js";
import { type SilenceSpan } from "./silence.js";
export interface RetakeCue {
    text: string;
    startUs: number;
    endUs: number;
}
export interface RetakeOptions {
    /** Max gap between the earlier cue's end and the later cue's start, seconds (default 60). */
    window?: number;
    /** Similarity floor on the normalised word sequences, 0..1 (default 0.8). */
    similarity?: number;
    /** Cues with fewer normalised words never count as a take (default 4). */
    minWords?: number;
}
export interface RetakeSide {
    text: string;
    start: number;
    end: number;
    start_us: number;
    end_us: number;
}
export interface RetakePair {
    earlier: RetakeSide;
    later: RetakeSide;
    similarity: number;
    /** Normalised word count of the earlier cue. */
    words: number;
}
export interface RetakeReport {
    source: string;
    window: number;
    similarity: number;
    min_words: number;
    cues: number;
    duration: number | null;
    duration_us: number | null;
    retakes: RetakePair[];
    /** Spans to drop: the earlier take of every pair, merged where they touch. */
    cuts: SceneSegment[];
    /** Everything else, over [0, duration] — what `cut`/`compile` keeps. */
    keeps: SceneSegment[];
}
/** Words of a cue in comparison form (empty tokens removed). */
export declare function normalizeWords(text: string): string[];
/** 2·LCS/(|a|+|b|): 1 for identical sequences, 0 for nothing in common. */
export declare function sequenceSimilarity(a: string[], b: string[]): number;
/**
 * Find retake pairs among time-ordered cues. Each earlier cue is compared with
 * the later cues that start inside the window; the FIRST later cue that
 * clears the similarity floor is its retake (closest repeat wins). A cue that
 * is itself the later half of one pair can still be the earlier half of the
 * next — three attempts at one sentence yield two pairs and keep the last.
 */
export declare function findRetakes(cues: RetakeCue[], opts?: RetakeOptions): RetakePair[];
/** Merge touching/overlapping spans, in time order. */
export declare function mergeSpans(spans: SilenceSpan[]): SilenceSpan[];
/**
 * The full report: pairs, the cut spans (earlier takes, merged) and the keep
 * spans over [0, duration]. `durationUs` null → the last cue's end bounds the
 * keeps (open-ended like detect-silence without a probe would be misleading
 * here: cues are timeline facts, not a stream probe).
 */
export declare function buildRetakeReport(cues: RetakeCue[], durationUs: number | null, opts: RetakeOptions, source: string): RetakeReport;
