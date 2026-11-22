import type { Draft } from "./draft.js";
import { type CaptionScript, type ScriptLimits } from "./script.js";
export type { CaptionScript, ScriptLimit, ScriptLimits } from "./script.js";
export { CJK_SCRIPT_LIMITS, captionScript } from "./script.js";
export type Severity = "error" | "warning" | "info";
export interface LintIssue {
    severity: Severity;
    code: string;
    message: string;
    fixable: boolean;
    /** Concrete remediation one-liner for report-only issues (the user runs it
     * deliberately — unlike --fix, it may touch content or need a human input
     * such as the replacement media directory). */
    suggested_command?: string;
    location?: {
        track?: string;
        segment_id?: string;
        material_id?: string;
        path?: string;
    };
}
export declare const MIN_CAPTION_DURATION_US = 100000;
/** The line-length and reading-speed limits for one caption's text, with the
 * suffix its messages carry when a script-specific default applied. */
export declare function captionLimits(text: string, opts: LintOptions): {
    script: CaptionScript;
    maxCharsPerLine: number;
    maxCharsPerSecond: number | undefined;
    note: string;
};
/**
 * The script table minus the rules the caller set explicitly: `--max-chars 30`
 * means 30 for every script, while the reading-speed defaults still follow
 * the script (and vice versa). Null when nothing script-specific is left.
 */
export declare function scriptLimitsExcept(limits: ScriptLimits | null, explicit: {
    maxCharsPerLine?: boolean;
    maxCharsPerSecond?: boolean;
}): ScriptLimits | null;
export interface LintOptions {
    maxCharsPerLine: number;
    maxCueDurationUs: number;
    minGapBetweenCaptionsUs: number;
    checkLocalPaths: boolean;
    /** Reading-speed ceiling in characters per second. maxCueDurationUs caps how
     * LONG a caption may stay up; this caps how FAST it goes by, which is the
     * limit a viewer actually feels — 45 characters in 1.2s breaks no absolute
     * duration rule and is still unreadable. 20 is the common subtitling ceiling
     * (BBC/Netflix sit at 17-20 for Latin scripts). */
    maxCharsPerSecond?: number;
    /** Fraction of a vertical canvas's height treated as safe for captions,
     * measured from the centre outward in CapCut's normalized transform space.
     * A caption past it sits where TikTok/Reels/Shorts draw their own UI. */
    safeAreaFraction?: number;
    /** ffprobe local media that exists (VFR/unreadable checks). Best-effort:
     * silently skipped when ffprobe is unavailable. Default true. */
    probeMedia?: boolean;
    ffprobeCmd?: string;
    /** Absolute path of the draft's folder (the directory holding the timeline
     * file). Enables the media-outside-draft check and its --fix stage-in;
     * library callers that lint a Draft with no on-disk home simply omit it and
     * the rule never runs. */
    draftDir?: string;
    /** Preview mode for fixDraft: staging external media copies a file into the
     * draft folder — a side effect no draft write can roll back — so under
     * dry-run the stage-in pass is skipped entirely and its issues stay
     * reported. The pure-JSON repair passes run either way (the caller's
     * dry-run save discards them). */
    dryRun?: boolean;
    /** Newest `platform.app_version` among the projects in the draft's drafts
     * folder, when the caller looked it up. Enables template-stale: a draft
     * stamped by the pre-0.23 bundled template (app_version 6.5.0, no
     * version/new_version markers) sitting in a store whose projects come from
     * a newer app major is the draft CapCut refuses as "from an unusual path"
     * (#67, #111). Library callers with no store simply omit it. */
    storeAppVersion?: string | null;
    /** How many projects in that drafts folder are JianYing 6.0+ encrypted
     * payloads the CLI could neither seed from nor compare against. With no
     * readable project at all, a bundled-template draft cannot be called stale
     * — only unverified for the app that wrote those projects. */
    storeEncryptedProjects?: number;
    /** Script-specific replacements for maxCharsPerLine / maxCharsPerSecond
     * (see CJK_SCRIPT_LIMITS). Null applies the Latin values to every script. */
    scriptLimits?: ScriptLimits | null;
    /** Validate every target start/end against draft.fps. Opt-in because older
     * app-authored drafts commonly carry millisecond timestamps. With --fix,
     * start and end are snapped together so duration is derived, never rounded
     * independently into a 1us overlap. */
    frameGrid?: boolean;
    /** Opt-in pacing: report still-too-long for a still image (a photo
     * material) that plays longer than this on the timeline, in microseconds. */
    maxStillUs?: number;
    /** Opt-in pacing: report shot-too-long for a video clip (any material but a
     * photo) that plays longer than this on the timeline, in microseconds. */
    maxShotUs?: number;
    /** Opt-in: report silent-stretch for every range of at least this many
     * microseconds, inside the timeline, where nothing audible plays. */
    minSilentUs?: number;
}
export declare const DEFAULT_LINT_OPTIONS: LintOptions;
export declare function lintDraft(draft: Draft, opts?: LintOptions): LintIssue[];
export interface PipReport {
    overlays: number;
    overlay_keyframes: number;
    masks_attached: number;
    masks_orphaned: number;
    missing_media: string[];
}
/** PIP + local-mask validation (issue #78, split from #44): the four ways the
 * discussion-#43 workflow can be silently wrong, as countable facts — the
 * overlay never landed (overlays), the mask never got attached
 * (masks_orphaned), the keyframes did not write (overlay_keyframes), the
 * copied clip points at missing media (missing_media, by path — sourced from
 * the missing-file issues the ordinary lint walk already found, so the two
 * never disagree). Overlays are segments on every video track above the
 * first: the layer order sortTracks maintains and `duplicate` builds. */
export declare function buildPipReport(draft: Draft, issues: LintIssue[]): PipReport;
/** The loud side of the --pip report: one warning per orphaned mask, so the
 * exit code fails CI exactly when the workflow's mask never got attached.
 * Emitted only under --pip: the check encodes the PIP workflow's expectation,
 * and an ordinary draft carrying an unreferenced mask is not necessarily
 * damaged (the #88 lesson) — but in a pipeline that just tried to attach one,
 * it is precisely the failure being looked for. */
export declare function pipLintIssues(draft: Draft): LintIssue[];
export declare function knownEffectIds(): Set<string>;
export declare function summarize(issues: LintIssue[]): {
    errors: number;
    warnings: number;
    info: number;
    total: number;
};
export declare function lintExitCode(summary: {
    errors: number;
    warnings: number;
}): number;
export interface FixResult {
    fixed: LintIssue[];
    remaining: LintIssue[];
}
export declare function fixDraft(draft: Draft, opts?: LintOptions): FixResult;
