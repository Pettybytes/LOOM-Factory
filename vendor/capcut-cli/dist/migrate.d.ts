import type { Draft } from "./draft.js";
/**
 * The top-level markers that tell an app build which schema generation wrote a
 * draft. A draft built from the pre-0.23 bundled template carries only
 * `platform` (app_version 6.5.0, os mac) and none of the rest, which is what
 * CapCut 8.4 / 8.5 / 8.7 Windows / 9.3 refuse with "Current project is from an
 * unusual path" (#67, #111). Restamping copies exactly these from a project the
 * installed app wrote; nothing in `tracks` or `materials` is touched.
 */
export declare const RESTAMP_FIELDS: readonly ["version", "new_version", "platform", "last_modified_platform", "color_space", "render_index_track_mode_on", "free_render_index_mode_on", "source"];
export interface RestampResult {
    ok: true;
    donor: string;
    donor_app_version: string | null;
    /** Markers rewritten to the donor's value. */
    restamped: string[];
    /** Markers the draft lacked entirely and now carries. */
    added: string[];
    /** Markers already equal to the donor's. */
    unchanged: string[];
    /** Markers the donor does not carry either. */
    unavailable: string[];
}
/**
 * `migrate --like <project>` / `--from-store`: rewrite a draft's schema
 * markers from a donor draft the app itself wrote, so a draft an older
 * template stamped opens in the installed build without being recreated
 * (#111: "multiple drafts", each one a full re-compile otherwise).
 */
export declare function restampDraft(draft: Draft, donor: Draft, donorLabel: string): RestampResult;
export interface MigrationResult {
    ok: boolean;
    from: string;
    to: string;
    applied: string[];
    skipped: string[];
    warnings: string[];
}
/**
 * Migrate a draft's schema across known version jumps.
 *
 * Currently implements:
 *   - mask -> common_masks (JianYing 5.9 -> 9.6+, CapCut older -> newer),
 *     also consolidating the CapCut-variant `common_mask[]` into the target —
 *     the destination app reads exactly one mask array, so entries left in a
 *     sibling variant would silently not appear (pyJianYingDraft#160).
 *
 * Migrations are best-effort: if a field doesn't apply (e.g. no masks present),
 * we record it as skipped rather than failing. The draft is mutated in place.
 */
export declare function migrateDraft(draft: Draft, from: string, to: string): MigrationResult;
