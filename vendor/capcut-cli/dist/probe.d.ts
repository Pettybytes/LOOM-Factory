/**
 * Best-effort media dimension probe.
 *
 * `add-video` previously hardcoded 1920x1080 for every source, so a portrait
 * (1080x1920) clip landed in the draft as landscape and CapCut rendered it
 * letterboxed/cropped. This module shells out to `ffprobe` to read the real
 * stored dimensions AND the display rotation, then returns the dimensions as
 * they should appear on screen.
 *
 * Architecture mirrors `render`/`caption`: the parsing + rotation math is a
 * pure, deterministic function (`parseMediaProbe`) that tests can assert
 * without invoking ffprobe; the live shell-out (`probeMedia`) is best-effort
 * and returns null whenever ffprobe is missing, errors, or emits something we
 * cannot parse. Callers fall back to their previous defaults on null, so a
 * host without ffprobe is never worse off than before — it just does not get
 * auto-detection.
 */
export interface MediaProbe {
    width: number | null;
    height: number | null;
    rotation: number;
    durationUs: number | null;
    videoDurationUs: number | null;
    fps: number | null;
    /** avg_frame_rate: frames / duration — the real average. */
    avgFps: number | null;
    /** r_frame_rate: the container's base/tick rate. Diverges from avgFps on
     * variable-frame-rate media. */
    baseFps: number | null;
    hasVideo: boolean;
    hasAudio: boolean;
    audioChannels: number | null;
    videoCodec: string | null;
    audioCodec: string | null;
}
/**
 * Variable-frame-rate heuristic: the average frame rate diverges from the
 * container's base rate by more than 1%. VFR media (screen recordings, phone
 * captures) is the classic silent breaker of frame-based pipelines — preview
 * and render timing drift, and strict renderers fail with missing-frame
 * errors (0xsline/OpenChatCut#1's failure class); the standard fix is to
 * normalize to constant frame rate before editing.
 */
export declare function isVfr(probe: MediaProbe): boolean;
/** True when the ffprobe binary runs. Cached per command string. */
export declare function ffprobeAvailable(ffprobeCmd?: string): boolean;
export declare function parseMediaProbe(json: string): MediaProbe | null;
export declare function probeMedia(path: string, ffprobeCmd?: string, useCache?: boolean): MediaProbe | null;
/** Normalize any degree value (negative or >360) to one of 0/90/180/270. */
export declare function normalizeRotation(rotation: number): number;
