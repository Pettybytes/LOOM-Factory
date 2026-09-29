import type { Draft } from "./draft.js";
export interface RenderOptions {
    out?: string;
    scale?: number;
    fps?: number;
    ffmpegCmd?: string;
    encoder?: string;
    crf?: number;
    videoBitrate?: string;
    burnCaptions?: boolean;
    softCaptions?: boolean;
    allVideoTracks?: boolean;
    dryRun?: boolean;
    progress?: boolean;
}
/**
 * `--soft-captions`: reviewers asked for subtitle STREAMS they can toggle in
 * the player rather than pixels burned into the proxy (munim-ffmpeg#3,
 * home-os#86 — "soft subtitle muxing" in ffmpeg terms). The text-track cues
 * are rendered to an SRT beside the output (`<preview>.srt`, which most
 * players auto-load by name anyway) and muxed as an mp4 `mov_text` stream.
 * Two ffmpeg facts shape the plan: mov_text is an encoder some minimal builds
 * lack (probed; skipped with a note, like drawtext), and `-shortest` treats
 * the subtitle stream as a stream — the file would end at the last cue — so
 * the plan drops `-shortest` when a subtitle stream is muxed (the video and
 * audio chains are already bounded by their trims, photos by `-t`).
 */
export interface SoftCaptionsPlan {
    /** Where the SRT is written before ffmpeg runs (next to the output). */
    path: string;
    /** SRT content, one cue per text segment with text. */
    srt: string;
    cues: number;
    /** Input index the SRT occupies in the ffmpeg invocation. */
    inputIndex: number;
}
export declare function srtPathFor(output: string): string;
export declare const FILTER_SCRIPT_THRESHOLD = 8192;
export declare function filterScriptPathFor(output: string): string;
/**
 * Turn an ffmpeg stderr tail into one actionable line, or "" when nothing in it
 * is recognised. Pure and exported so the mapping is testable without ffmpeg —
 * same reason `buildRenderPlan` is pure.
 */
export declare function explainFfmpegFailure(stderr: string): string;
export interface RenderInput {
    index: number;
    path: string;
    kind: "video" | "photo" | "audio" | "subtitle";
}
export interface RenderPlan {
    output: string;
    width: number;
    height: number;
    fps: number;
    inputs: RenderInput[];
    filterComplex: string;
    args: string[];
    videoSegments: number;
    audioSegments: number;
    textOverlays: number;
    skipped: Array<{
        segmentId: string;
        reason: string;
    }>;
    overlaySegments: number;
    capabilities?: FfmpegCapabilities;
    /** Present only with --soft-captions and at least one cue. */
    softCaptions?: SoftCaptionsPlan;
    /** Long graphs are supplied through a file so Windows command-line limits cannot truncate them. */
    filterScript?: {
        path: string;
        content: string;
    };
    quality: {
        mode: "crf";
        crf: number;
    } | {
        mode: "bitrate";
        bitrate: string;
    };
}
export interface RenderResult extends RenderPlan {
    ok: boolean;
    executed: boolean;
}
/**
 * Every filter name the -filters probe checks. The chain/probe guard test
 * asserts the generated filter_complex never uses a name outside this list
 * (plus its explicit allowlist), so graph and probe cannot drift apart again.
 */
export declare const PROBED_FILTERS: readonly ["drawtext", "overlay", "fps", "scale", "pad", "setsar", "format", "concat", "trim", "setpts", "atrim", "asetpts", "adelay", "anull", "amix", "atempo", "volume", "afade"];
export interface FfmpegCapabilities {
    available: boolean;
    drawtext: boolean;
    overlay: boolean;
    x264: boolean;
    fps: boolean;
    scale: boolean;
    pad: boolean;
    setsar: boolean;
    format: boolean;
    concat: boolean;
    trim: boolean;
    setpts: boolean;
    atrim: boolean;
    asetpts: boolean;
    adelay: boolean;
    anull: boolean;
    amix: boolean;
    atempo: boolean;
    volume: boolean;
    afade: boolean;
}
export declare function probeFfmpegCapabilities(command?: string): FfmpegCapabilities;
/**
 * Filter names used in a filter_complex string, in the grammar buildRenderPlan
 * emits: `;`-separated chains of `,`-separated filters, each surrounded by
 * optional [labels], with single-quoted option values that may themselves
 * carry commas and brackets (drawtext's enable='between(t,..)'). Pure and
 * exported for the chain/probe guard test; renderDraft uses it to check the
 * conditional audio filters against what the plan actually emits.
 */
export declare function filterNamesInGraph(filterComplex: string): string[];
/**
 * Encoder names from `ffmpeg -hide_banner -encoders` output. Rows look like
 * ` V....D libx264              libx264 H.264 / ...`: a six-character
 * capability column whose first letter is the codec type, then the name. The
 * legend at the top reuses the column shape (` V..... = Video`), so the name
 * token must not be `=`. Pure and exported so it can be fed captured output
 * strings in tests, the same reason parse helpers elsewhere are.
 */
export declare function parseFfmpegEncoders(output: string): Set<string>;
/**
 * The one extra spawn --encoder costs, run only when the flag was given
 * (renderDraft) — the default libx264 path never pays it. `listed: false`
 * means the binary would not enumerate its encoders at all; validation is then
 * skipped so the render still reaches ffmpeg, whose own "Unknown encoder"
 * error explainFfmpegFailure already maps.
 */
export declare function probeFfmpegEncoders(command?: string): {
    listed: boolean;
    encoders: Set<string>;
};
/**
 * Build the ffmpeg invocation for a draft. Pure and deterministic given the
 * draft + options (no uuids, no clock) so it can be asserted in tests.
 */
export declare function buildRenderPlan(draft: Draft, opts: RenderOptions): RenderPlan;
export declare function renderDraft(draft: Draft, filePath: string, opts: RenderOptions): RenderResult;
