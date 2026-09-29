import type { Draft } from "./draft.js";
import type { AppSource } from "./version.js";
/**
 * App auto-upgrade tripwire (`app-versions.json`).
 *
 * The desktop apps update themselves, and an updated build silently rewrites
 * the drafts it opens — a pipeline validated against 8.x wakes up to a 10.x
 * store and drafts that have crossed a support boundary, with nothing in the
 * toolchain saying so (GuanYixuan/pyJianYingDraft#115, #178). This module
 * remembers the last version evidence the CLI saw per draft store — in the
 * CLI's own config area, following the `harvest-enums` per-user catalogue
 * pattern (~/.config/capcut-cli/, env override, never state inside a draft) —
 * and compares on every mutating write:
 *
 * - first sighting records silently;
 * - a difference prints a stderr WARNING naming old -> new and adds an
 *   `app_version_drift` field to the command's JSON result — warn only,
 *   NEVER a refusal (the v0.15 write guard remains the sole gate on
 *   beyond-evidence writes);
 * - `capcut version` reports the drift read-only (it never updates the
 *   record) and `capcut doctor` re-inspects every tracked store.
 *
 * The evidence tuple is the write guard's own detection (`assessWriteSafety`
 * `.detected`): effective app version (max across platform,
 * last_modified_platform, and the newest readable sibling), app source, and
 * the top-level schema integer. Markerless CLI-created drafts carry no
 * evidence and are never tracked. A hand-broken state file never breaks a
 * write — it reads as empty with a WARNING and the next mutating write
 * rebuilds it (the `user-enums.json` robustness rule).
 */
export declare const APP_VERSIONS_ENV = "CAPCUT_CLI_APP_VERSIONS";
export interface AppVersionEvidence {
    app_source: AppSource;
    app_version: string | null;
    schema_int: number | null;
}
export interface AppVersionRecord extends AppVersionEvidence {
    seen_at: string;
}
export interface AppVersionDrift {
    store_dir: string;
    from: AppVersionRecord;
    to: AppVersionEvidence;
    /** Human fragments, one per changed marker: "app version 8.7.0 -> 10.5.0". */
    changes: string[];
}
export declare function appVersionsPath(override?: string): string;
export declare function loadAppVersions(path?: string): {
    stores: Record<string, AppVersionRecord>;
    error: string | null;
};
/** The tripwire's evidence tuple for a draft: exactly what the write guard
 * detects. Null when the draft carries no version evidence at all (markerless
 * `capcut create` output) — such stores are never tracked. */
export declare function appVersionEvidence(draft: Draft, storeVersion: string | null): AppVersionEvidence | null;
/** Read-only comparison for `version` and `doctor` — never records. */
export declare function assessAppVersionDrift(projectDir: string, evidence: AppVersionEvidence | null, statePath?: string): {
    drift: AppVersionDrift | null;
    error: string | null;
};
export declare function takeAppVersionDrift(): AppVersionDrift | null;
/**
 * Mutating-write tripwire: compare the store's current evidence against the
 * last-seen record, remember any drift for the JSON result, and update the
 * record. Only a change touches the file (first sighting or drift); a corrupt
 * state file is treated as empty — the caller surfaces `error` as a WARNING —
 * and is rebuilt here. Warn only: this function never throws on state
 * problems and never blocks the write it accompanies.
 */
export declare function trackAppVersion(projectDir: string, evidence: AppVersionEvidence | null, statePath?: string): {
    drift: AppVersionDrift | null;
    error: string | null;
};
export declare function formatAppVersionDriftWarning(drift: AppVersionDrift): string;
/** `doctor` sweep: re-inspect every tracked store dir that still holds a
 * readable draft and report the ones whose evidence moved. */
export declare function scanTrackedStores(statePath?: string): {
    tracked: number;
    drifts: AppVersionDrift[];
    error: string | null;
};
