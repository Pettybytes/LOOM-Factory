import type { Draft } from "./draft.js";
declare const STANDARD_FILES: readonly ["draft_content.json", "draft_info.json", "draft_meta_info.json", "template-2.tmp"];
export type DraftCandidateName = (typeof STANDARD_FILES)[number] | string;
export interface DraftCandidate {
    name: DraftCandidateName;
    path: string;
    exists: boolean;
    size: number;
    mtime: string | null;
    sha256: string | null;
    raw: string | null;
    parseable: boolean;
    envelopePath: string[];
    draft: Draft | null;
    timelineHash: string | null;
    error?: string;
}
/** Which primary project file drives this store: draft_content.json (the
 * layout every pre-Mac-10.x build uses), draft_info.json with no
 * draft_content.json beside it (reported as the primary project file on newer
 * Mac builds — jianying-mcp#5, pyJianYingDraft#177/#194), a pre-8.7 store
 * carrying a nested Timelines/ directory (CapCut 7.x is reported to keep the
 * live document at Timelines/<main_timeline_id>/draft_info.json with the root
 * file a regenerated mirror — issue #50; DETECTION ONLY, reads and writes
 * still target the root candidates), or neither (timeline readable only from
 * a mirror such as template-2.tmp). */
export type DraftStoreLayout = "content-primary" | "info-primary" | "timelines-nested" | "unknown";
export interface DraftStore {
    projectDir: string;
    canonical: DraftCandidate;
    targets: DraftCandidate[];
    candidates: DraftCandidate[];
    version: string | null;
    modernStorage: boolean;
    diverged: boolean;
    layout: DraftStoreLayout;
    /** Project-relative paths of the nested Timelines/ documents (issue #50):
     * Timelines/project.json plus every Timelines/<id>/draft_info.json /
     * draft_content.json found. Reporting only — never read as a timeline
     * source, never part of the write set. Populated whenever the structure
     * exists, even on >= 8.7 stores where the layout value stays untouched. */
    nestedTimelines: string[];
}
export interface DraftStoreReport {
    ok: boolean;
    project_dir: string;
    canonical: string;
    version: string | null;
    modern_storage: boolean;
    diverged: boolean;
    layout: DraftStoreLayout;
    nested_timelines: string[];
    write_guard: "ok" | "warn" | "refuse";
    editor_running: string[];
    candidates: Array<{
        file: string;
        exists: boolean;
        size: number;
        mtime: string | null;
        sha256: string | null;
        parseable_timeline: boolean;
        envelope: string;
        timeline_hash: string | null;
        tracks?: number;
        segments?: number;
        app_version?: string | null;
        error?: string;
    }>;
    next_actions: string[];
    /** Present only when the timeline references local media that
     * draft_meta_info.json's `draft_materials` provably does not register —
     * see assessMediaRegistration. Informational: no exit-code change. */
    media_registration?: MediaRegistrationNote;
}
export interface MediaRegistrationNote {
    referenced_media: number;
    draft_materials: "missing-file" | "missing-key" | "all-groups-empty";
    note: string;
}
export declare function parseCandidate(path: string): DraftCandidate;
/** Warning every mutating write prints on a `timelines-nested` project (issue
 * #50). Warn, never refuse: the nested-live-document claim has no committed
 * field artifact yet, and the write itself still targets the root files. */
export declare const NESTED_TIMELINES_WRITE_WARNING: string;
/** The `diagnose` next_action / `version` note naming the layout — same shape
 * as the draft_info-primary action below (name the layout, state the risk,
 * end with the fixture CTA). */
export declare const NESTED_TIMELINES_ACTION: string;
/**
 * The same structure on >= 8.7 storage, where `layout` deliberately stays at its
 * content-/info-primary value so the 7.x claim never relabels a modern store.
 *
 * `diagnose` already attaches the redacted nested evidence on these stores
 * (index.ts `cmdDiagnose`, gated on `nested_timelines.length`), and `fixture`
 * already bundles it, but the human-readable action and the `version` note were
 * gated on the layout value alone — so a >= 8.7 user got a report carrying
 * Timelines/ evidence with no line of text saying why it was collected. This is
 * that line, and it asserts nothing: neither #50's 7.x discard risk nor #68's
 * 8.5.0 survival observation transfers across the 8.7 storage change.
 */
export declare const NESTED_TIMELINES_MODERN_ACTION: string;
/** Version-gated form of NESTED_TIMELINES_WRITE_WARNING (issue #68). */
export declare function nestedTimelinesWriteWarning(appVersion: string | null): string;
/** Version-gated form of NESTED_TIMELINES_ACTION (issue #68). */
export declare function nestedTimelinesAction(appVersion: string | null): string;
export declare function discoverDraftStore(input: string): DraftStore;
/**
 * The store as a fresh discovery would find it immediately after a write,
 * built from what the write already produced instead of re-reading the project.
 *
 * `saveDraft` keeps a per-path store so a library caller can save the same
 * loaded draft twice without tripping its own changed-on-disk guard, and used
 * to refresh that cache by calling `discoverDraftStore` again — re-reading,
 * re-parsing, and re-hashing every sibling to learn one thing the write
 * already knew: each target now holds exactly the bytes it was just handed.
 * `written` maps target path -> the content committed there; candidates
 * outside it are carried over untouched, so a partial write set stays honest.
 *
 * Everything a re-discovery would recompute is recomputed: `size` and `mtime`
 * come from a `stat` of the file that was just renamed into place, `version`
 * (and with it `modernStorage` and `layout`) is re-derived from the timelines
 * the targets now hold — which is how a mirror that used to carry a newer app
 * version stops raising the store's version once it has been overwritten — and
 * `diverged` collapses to false on its own, because every written target now
 * exposes the same timeline.
 */
export declare function storeAfterWrite(store: DraftStore, draft: Draft, written: Map<string, string>): DraftStore;
export declare function serializeDraftCandidate(candidate: DraftCandidate, draft: Draft): string;
export declare function editorProcesses(): string[];
/**
 * Match a raw process listing against the editor executables, one process per
 * line — never as a substring of the joined listing: npm rewrites its own
 * process title to the full command line (`npm exec capcut-cli@0.21.0 …`), so
 * a substring test detects the CLI itself and refuses every write under npx
 * (#99). The comparison is the exact basename, not the whole line, because
 * macOS `ps -o comm=` prints the executable's full bundle path; on Windows the
 * image name is the first quoted field of a `tasklist /FO CSV` row.
 */
export declare function matchEditorProcesses(listing: string, flavor: "win32" | "posix"): string[];
export declare function isManagedDraftPath(path: string): boolean;
/**
 * Per-OS CapCut/JianYing draft stores, in preference order. Empty on platforms
 * with no desktop editor (Linux), where the caller must be told to pass a path.
 */
export declare function draftDirCandidates(): {
    label: string;
    path: string;
}[];
/**
 * Where a draft-creating command writes when the caller passed no --drafts.
 * CAPCUT_DRAFT_DIR wins, then the first store that exists, then the first
 * candidate. Null means this platform has no known store: callers must fail
 * loudly rather than guess, because a draft outside the app's store opens as
 * "this draft comes from an unconventional path" (issue #52).
 */
export declare function defaultDraftsDir(): string | null;
export interface TimelineSyncTarget {
    file: string;
    state: "canonical" | "in_sync" | "drifted";
    envelope: string;
    mtime: string | null;
    timeline_hash: string | null;
    guid: string | null;
    guid_drifted: boolean;
    /** Set on Timelines/<id>/ documents included by the --nested opt-in. These
     * keep their own GUID on repair: the verified 9.2.8 workaround in issue #50
     * writes the timeline id into the nested document, not the root draft id. */
    nested?: boolean;
    tracks?: number;
    segments?: number;
}
export interface TimelineSyncUnreconcilable {
    file: string;
    reason: string;
    workaround: string;
}
export interface TimelineSyncPlan {
    project_dir: string;
    canonical: string;
    canonical_path: string;
    version: string | null;
    modern_storage: boolean;
    layout: DraftStoreLayout;
    /** Set when the canonical is not draft_content.json (draft_info-primary
     * layout): names the promotion and carries the fixture CTA. */
    canonical_note: string | null;
    in_sync: boolean;
    /** Drifted mirrors whose mtime is newer than draft_content.json's. CapCut
     * >= 8.7 writes the mirrors on save, so a newer drifted mirror may hold app
     * edits that a canonical -> mirror repair would roll back. */
    newer_mirrors: string[];
    canonical_stale: boolean;
    targets: TimelineSyncTarget[];
    drifted: string[];
    unreconcilable: TimelineSyncUnreconcilable[];
    /** Count of Timelines/<id>/ documents on disk that --nested would cover —
     * reported even when the run did not include them, so the plan can point at
     * the opt-in instead of silently ignoring the nested layout (issue #50). */
    nested_available: number;
    /** Whether this plan was computed with the --nested opt-in. */
    nested_included: boolean;
}
export interface TimelineSyncResult {
    plan: TimelineSyncPlan;
    canonicalDraft: Draft;
    canonicalCandidate: DraftCandidate;
    driftedCandidates: DraftCandidate[];
    /** Drifted Timelines/<id>/ documents (--nested only). Each carries the GUID
     * the rewrite must keep: the nested document's own timeline id, per the
     * verified issue-#50 workaround — never the canonical root draft id. */
    nestedDriftedCandidates: Array<{
        candidate: DraftCandidate;
        keepGuid: string | null;
    }>;
}
/**
 * Plan for `sync-timelines` (issue #39, symptom #35): draft_content.json is
 * canonical (draft_info.json on the draft_info-primary Mac layout — see
 * DraftStoreLayout); every other readable timeline target is compared against
 * it by timeline hash. Direction is always draft_content.json -> mirror, but each
 * target's mtime is surfaced: CapCut >= 8.7 writes the mirrors on save, so a
 * drifted mirror NEWER than draft_content.json may hold app edits that the
 * repair would roll back (canonical_stale / newer_mirrors flag this; --apply
 * refuses without --force-write). A mirror file that exists but holds no
 * readable timeline (binary/encrypted template-2.tmp) cannot be reconciled
 * and is reported as such instead of being silently skipped. Accepts a
 * project directory or its draft_content.json path; any other explicitly
 * named file is rejected so the plan and the write always cover the same
 * target set.
 *
 * With `nested: true` (the --nested opt-in, issue #50) the plan additionally
 * covers every Timelines/<id>/ timeline document plus its template-2.tmp
 * mirror, in the same canonical -> mirror direction and behind the same
 * newer-mirror refusal. Nested documents keep their own GUID on rewrite and
 * compare by id-normalized timeline hash; Timelines/project.json is never a
 * target. This is the 9.2.8 workaround verified in issue #50, mechanized —
 * it does not change which file any command reads (PR #51's canonical flip
 * stays rejected pending a field artifact).
 */
export declare function planTimelineSync(input: string, opts?: {
    nested?: boolean;
}): TimelineSyncResult;
/** The sidecar-note core, decoupled from store discovery so `lint` can run the
 * same observation from a draft + project dir (pyCapCut#13 reports the symptom
 * landing in CI-shaped pipelines, where diagnose is never run). */
export declare function assessMediaRegistrationRaw(draft: Draft | null, meta: {
    exists: boolean;
    raw: string | null;
}): MediaRegistrationNote | null;
/** File-reading form of the sidecar note for callers that hold a draft and its
 * project directory rather than a discovered store (lint's media-unregistered
 * check). Unreadable sidecars return null, same as the store form. */
export declare function assessMediaRegistrationAt(draft: Draft, projectDir: string): MediaRegistrationNote | null;
export declare function diagnoseDraftStore(input: string): DraftStoreReport;
export {};
