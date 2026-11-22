import type { Draft } from "./draft.js";
export interface LoudnessOptions {
    ffmpegCmd?: string;
    /** Delivery target in LUFS; without it the report only measures. */
    target?: number;
    /** Normalize every clip to the target instead of one gain for the whole mix. */
    perClip?: boolean;
    /** Largest boost a clip may get, in dB (default 20). */
    maxGainDb?: number;
    /** Folder relative media paths resolve against. */
    draftDir?: string;
    /** How far the judged loudness may sit from the target, in LU (needs target). */
    tolerance?: number;
    /** Ceiling for the judged true peak, in dBTP. */
    maxTruePeak?: number;
}
export interface ClipLoudness {
    segment_id: string;
    track: string;
    path: string;
    duration_us: number;
    volume: number;
    /** Integrated loudness of the clip's media at volume 1, LUFS. */
    measured_lufs: number | null;
    true_peak_dbfs: number | null;
    /** measured_lufs with the segment's volume applied. */
    effective_lufs: number | null;
    gain_db?: number;
    new_volume?: number;
    /** The gain wanted more than maxGainDb and was capped. */
    clamped?: boolean;
    skipped?: string;
}
export interface LoudnessReport {
    mode: "uniform" | "per-clip";
    /** Duration-weighted energy mean of the measured clips: an estimate of the
     * export's integrated loudness when clips do not overlap. */
    program_lufs: number | null;
    target?: number;
    max_gain_db: number;
    clips: ClipLoudness[];
}
/** The delivery check: did the judged mix land within the tolerance of the
 * target, and does its loudest clip stay under the true-peak ceiling. */
export interface LoudnessGate {
    target?: number;
    tolerance?: number;
    /** The loudness judged: the mix after this run's gain when it is applied,
     * the mix as it stands otherwise. */
    estimate_lufs: number | null;
    within?: boolean;
    max_true_peak?: number;
    /** Highest clip true peak at the volume judged, in dBTP. */
    estimate_true_peak?: number | null;
    true_peak_ok?: boolean;
}
export declare const DEFAULT_MAX_GAIN_DB = 20;
/** Integrated loudness and true peak from ebur128's summary (the last ones printed). */
export declare function parseEbur128(text: string): {
    lufs: number | null;
    peak: number | null;
};
/** Measure every audible clip; with a target, compute the gain each one gets. */
export declare function measureLoudness(draft: Draft, opts?: LoudnessOptions): LoudnessReport;
/**
 * Judge the report against a delivery tolerance and a true-peak ceiling; null
 * when neither was asked for. `afterGain` judges the mix with this run's gain
 * (the volumes `applyLoudness` writes, --max-gain-db cap included); without
 * it, the mix as it stands. `failure` is a one-line reason, null on a pass.
 */
export declare function judgeLoudness(report: LoudnessReport, opts: {
    tolerance?: number;
    maxTruePeak?: number;
    afterGain?: boolean;
}): {
    gate: LoudnessGate;
    failure: string | null;
} | null;
/** Write each measured clip's new volume into the draft; returns how many changed. */
export declare function applyLoudness(draft: Draft, report: LoudnessReport): number;
