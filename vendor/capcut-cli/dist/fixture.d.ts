export interface SanitizeFileResult {
    file: string;
    bytes_in: number;
    bytes_out: number;
    redactions: number;
}
export interface SanitizeReport {
    ok: boolean;
    source_dir: string;
    out_dir: string;
    version: string | null;
    modern_storage: boolean;
    files: SanitizeFileResult[];
    redaction_kinds: Record<string, number>;
    media_excluded: boolean;
    mask_keyframe_evidence: MaskKeyframeSummary;
    notes: string[];
}
export interface MaskEvidence {
    json_path: string;
    id: string | null;
    name: string | null;
    resource_type: string | null;
    config_keys: string[];
    /** Keys on the mask entry beyond what this CLI writes — worth a human look. */
    unrecognized_keys: string[];
    /** Paths under this mask entry that look like keyframes (time_offset / keyframe_list / property_type). */
    keyframe_shaped_nodes: string[];
}
export interface MaskedSegmentEvidence {
    segment_id: string | null;
    mask_material_ids: string[];
    property_types: string[];
}
export interface MaskKeyframeFileEvidence {
    file: string;
    parsed: boolean;
    masks: MaskEvidence[];
    property_types: {
        known: string[];
        unknown: string[];
    };
    /** Segments carrying BOTH a mask ref and common_keyframes entries. */
    segments_with_mask_and_keyframes: MaskedSegmentEvidence[];
}
export type MaskKeyframeVerdict = "mask-keyframe-evidence-found" | "no-mask-keyframe-evidence";
export interface MaskKeyframeSummary {
    verdict: MaskKeyframeVerdict;
    masks_found: number;
    unknown_property_types_on_masked_segments: string[];
}
export interface MaskKeyframeReport {
    issue: string;
    looking_for: string[];
    verdict: MaskKeyframeVerdict;
    summary: MaskKeyframeSummary;
    files: MaskKeyframeFileEvidence[];
}
/** Build the #44 harvest report from the redacted timeline texts of a bundle. */
export declare function buildMaskKeyframeReport(files: Array<{
    file: string;
    text: string;
}>): MaskKeyframeReport;
/**
 * Build a shareable, redacted fixture bundle from a real draft folder.
 * Reuses the same storage discovery the `diagnose` command uses, so the
 * reported version/canonical-file analysis matches.
 */
export declare function sanitizeDraftBundle(input: string, outDir: string): SanitizeReport;
export interface NestedFileTreeEntry {
    path: string;
    size: number;
    mtime: string | null;
}
export interface NestedTrackSummary {
    type: string;
    segments: number;
    texts: number;
    /** Hash over the track's ordered text contents — proves a text changed
     * without carrying what it says. */
    text_hash: string | null;
}
export interface NestedTrackDelta {
    track: number;
    root: NestedTrackSummary | null;
    nested: NestedTrackSummary | null;
}
export interface NestedRootComparison {
    root_file: string;
    nested_file: string;
    identical: boolean;
    root_sha256: string | null;
    nested_sha256: string | null;
    root_timeline_hash: string | null;
    nested_timeline_hash: string | null;
    /** Which side is mtime-newer when the documents differ; null when identical. */
    mtime_newer: "root" | "nested" | "equal" | "unknown" | null;
    root_mtime: string | null;
    nested_mtime: string | null;
    track_deltas: NestedTrackDelta[];
    verdict: string;
}
export interface NestedTimelinesEvidence {
    issue: string;
    note: string;
    app_version: string | null;
    os: string | null;
    /** Raw Timelines/project.json text, passed through the bundle redactors. */
    timelines_project_json: string | null;
    redaction_kinds: Record<string, number>;
    file_tree: NestedFileTreeEntry[];
    root_vs_nested: NestedRootComparison[];
}
/**
 * Build the #50 evidence section for `diagnose` / `diagnose --bundle`, or null
 * when neither the timelines-nested layout nor any nested Timelines/ document
 * exists — a normal draft's diagnose report must stay byte-identical.
 */
export declare function buildNestedTimelinesEvidence(input: string): NestedTimelinesEvidence | null;
export interface RedactionFinding {
    file: string;
    line: number;
    kind: "home-path" | "email" | "device-key" | "username";
}
export interface RedactionCheck {
    ok: boolean;
    bundle_dir: string;
    files_scanned: number;
    findings: RedactionFinding[];
}
export declare function verifyBundleRedaction(bundleDir: string): RedactionCheck;
