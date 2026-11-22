import type { Draft } from "./draft.js";
/**
 * OpenTimelineIO export (`export-timeline`).
 *
 * The exit ramp for the version-compat dead ends: when an app build rejects a
 * draft (encrypted era, beyond-evidence version), the cut itself — clip
 * order, trims, speeds — should not be lost with it. This module flattens a
 * draft's video/audio tracks into an OTIO 0.14-era JSON document (Timeline.1
 * / Stack.1 / Track.1 / Clip.1 — the stable schema set every OTIO reader,
 * including DaVinci Resolve's native .otio import, still accepts; newer
 * libraries up-convert on read).
 *
 * Deliberate scope:
 * - Video and audio tracks only. Text/caption tracks are skipped with a
 *   pointer to `export-srt` (OTIO has no standard title schema — the request
 *   has been open since 2017, OpenTimelineIO#62), UNLESS `captions: "markers"`
 *   is asked for: then every caption cue becomes a timeline marker on the
 *   Stack (name = cue text, marked_range = cue timing), which is what NLEs
 *   already round-trip — Resolve and Premiere import Stack markers as timeline
 *   markers — so the captions at least survive the handoff as positioned notes
 *   and `import-timeline` can rebuild the text track from them. Sticker /
 *   effect / filter tracks have no portable equivalent and are skipped.
 *   Every skip is reported, never silent.
 * - `speed` maps to a LinearTimeWarp effect (time_scalar = speed), matching
 *   OTIO semantics: timeline duration = source_range.duration / time_scalar,
 *   which is exactly CapCut's source/target timerange relationship.
 * - Times are converted from CapCut microseconds to frames at the draft fps
 *   (default 30); media is assumed to share the timeline rate — the draft
 *   does not store per-file rates, and readers re-probe the media anyway.
 * - CapCut-only facts (segment/material ids, volume) ride along under
 *   metadata.capcut so a round-trip stays debuggable.
 *
 * Pure and deterministic: same draft in, byte-identical document out.
 */
interface OtioObject {
    [key: string]: unknown;
}
export interface OtioSkip {
    track: string;
    type: string;
    reason: string;
}
export interface OtioStats {
    tracks: number;
    clips: number;
    gaps: number;
    /** Caption cues written as timeline markers (`captions: "markers"`); 0 otherwise. */
    captions: number;
    skipped: OtioSkip[];
}
export interface OtioExportOptions {
    /** How text/caption tracks travel: dropped with a note (default) or as Stack markers. */
    captions?: "skip" | "markers";
}
/** The marker colour every caption marker carries — one colour, so an NLE user can filter them. */
export declare const CAPTION_MARKER_COLOR = "YELLOW";
/**
 * OpenTimelineIO import (`import-timeline`) — the inverse of `draftToOtio`.
 *
 * Reads the exact schema set the exporter emits (Timeline.1 / Stack.1 /
 * Track.1 / Clip.1 / Gap.1, ExternalReference / MissingReference,
 * LinearTimeWarp) into a flat plan the command applies through the same
 * factory functions `add-video` / `add-audio` use. Nested Timeline/Stack/Track
 * sequences are flattened into ordinary tracks with their offsets preserved.
 * Everything the CLI cannot represent — unknown track kinds, transitions,
 * other effects, generator references, markers, a non-zero global start — is REPORTED in
 * `skipped`, never silently dropped (the export-timeline house rule).
 *
 * Speed inverts the exporter's documented LinearTimeWarp relationship
 * (timeline duration = source_range.duration / time_scalar): speed =
 * time_scalar, and the clip's timeline duration is recomputed from its source
 * duration. Times convert from frames back to CapCut microseconds at each
 * RationalTime's own rate (falling back to the timeline rate).
 */
export interface ImportClipPlan {
    name: string;
    targetStartUs: number;
    targetDurationUs: number;
    sourceStartUs: number;
    sourceDurationUs: number;
    speed: number;
    volume: number | null;
    /** ExternalReference target_url; null = MissingReference (or an unsupported reference, reported). */
    mediaPath: string | null;
    /** ExternalReference available_range duration (0 = unknown). */
    mediaDurationUs: number;
}
export interface ImportTrackPlan {
    kind: "video" | "audio";
    name: string;
    clips: ImportClipPlan[];
}
export interface ImportCaptionPlan {
    text: string;
    startUs: number;
    durationUs: number;
    /** Text track name recorded at export (falls back to "captions"). */
    track: string;
}
export interface ImportPlan {
    name: string;
    rate: number;
    tracks: ImportTrackPlan[];
    clips: number;
    gaps: number;
    /** Caption cues recovered from Stack markers that export-timeline --captions markers wrote. */
    captions: ImportCaptionPlan[];
    skipped: OtioSkip[];
}
/** Resolve local references without fetching remote URLs or changing foreign Windows paths. */
export declare function resolveOtioMediaPath(reference: string, mediaDir?: string): string;
export declare function otioToImportPlan(doc: unknown, options?: {
    mediaDir?: string;
}): ImportPlan;
export declare function draftToOtio(draft: Draft, options?: OtioExportOptions): {
    doc: OtioObject;
    stats: OtioStats;
};
export {};
