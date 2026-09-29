import { type Draft } from "./draft.js";
export type MetaMaterialKind = "video" | "photo" | "music";
/** draft_materials[type=0].value[] entry — field order follows CapCut's own sidecar. */
export interface MetaMaterialEntry {
    ai_group_type: string;
    create_time: number;
    duration: number;
    enter_from: number;
    extra_info: string;
    file_Path: string;
    height: number;
    id: string;
    import_time: number;
    import_time_ms: number;
    item_source: number;
    material_color_tag: string;
    md5: string;
    metetype: MetaMaterialKind;
    roughcut_time_range: {
        duration: number;
        start: number;
    };
    sub_time_range: {
        duration: number;
        start: number;
    };
    type: number;
    width: number;
}
/** Nominal duration CapCut records for an imported still image (pyCapCut PR #14). */
export declare const PHOTO_META_DURATION_US = 5000000;
/** The group `type` that holds imported media in `draft_materials`. */
export declare const IMPORTED_MEDIA_GROUP_TYPE = 0;
export interface ReferencedMedium {
    path: string;
    name: string;
    kind: MetaMaterialKind;
    durationUs: number;
    width: number;
    height: number;
}
export declare function buildMetaMaterialEntry(medium: ReferencedMedium): MetaMaterialEntry;
/**
 * Distinct local media the timeline references, in material order: video and
 * photo materials from `materials.videos`, audio from `materials.audios`. URLs
 * (Wikimedia adds resolve to local copies anyway) are skipped, and one file
 * referenced by several materials is one medium to register.
 */
export declare function referencedMedia(draft: Draft): ReferencedMedium[];
export interface MaterialsRegistrationTarget {
    file: "draft_meta_info.json";
    field: "draft_materials";
    path: string;
    state: "ok" | "no-media" | "unregistered" | "sidecar-unavailable";
    action: "none" | "update" | "blocked";
    detail: string;
    /** Distinct local media files the timeline references. */
    referenced: number;
    /** Of those, already present in draft_materials (matched by file_Path). */
    registered: number;
    /** Paths this plan registers (empty when nothing is missing). */
    to_register: string[];
    /** Entries already in the sidecar that no timeline material references — preserved, never removed. */
    unreferenced_entries: number;
    /** Timeline materials whose `local_material_id` does not name their entry (after this plan's registration). */
    unlinked_materials: number;
}
/** The project's draft_meta_info.json parsed as an object, or null when absent or unreadable. */
export declare function readSidecar(projectDir: string): Record<string, unknown> | null;
/** Every registered entry (`file_Path` + `id`) across ALL groups. */
export declare function registeredEntries(groups: unknown): Array<{
    path: string;
    id: string;
}>;
/**
 * Add-time registration (add-video / add-audio / quickstart / compile): put one
 * medium into the project's sidecar and return the entry id the timeline
 * material must carry as `local_material_id`. An entry already registered for
 * the file (the app's own, or an earlier add) is reused — merge, never
 * replace. Null when the project has no readable sidecar (a bare timeline
 * file, a draft the CLI never registered): nothing is written and
 * `register --materials` remains the repair.
 *
 * The write keeps register's discipline: a `.bak` of the sidecar as found,
 * then an atomic replace.
 */
export declare function registerMediumInSidecar(projectDir: string, medium: ReferencedMedium): {
    entryId: string;
    created: boolean;
} | null;
export interface UnlinkedMaterial {
    materialId: string;
    kind: "videos" | "audios";
    path: string;
    /** What the material carries today ("" when blank). */
    current: string;
    /** The sidecar entry id the material should carry — null when the file is not registered at all. */
    entryId: string | null;
}
/**
 * Timeline materials (video, photo, audio with a local path) whose
 * `local_material_id` does not name the sidecar entry registered for their
 * file. JianYing 5.9+ and CapCut 9.3 resolve local media through this key
 * (luoluoluo22/jianying-editor-skill#23, JmsLdrn/capcut-mcp#1): blank, the
 * clip shows as missing / inaccessible and the app's own Link-media dialog
 * cannot repair it. `sidecar === null` means every local material is unlinked
 * with no entry to link to.
 */
export declare function unlinkedMaterials(draft: Draft, sidecar: Record<string, unknown> | null, projectDir: string): UnlinkedMaterial[];
/** Write `local_material_id` on every unlinked material whose file has an entry; returns the material ids linked. */
export declare function linkLocalMaterialIds(draft: Draft, sidecar: Record<string, unknown> | null, projectDir: string): string[];
/**
 * Plan the draft_materials registration against a parsed sidecar object (the
 * one on disk, or the one `register`'s own sidecar repair is about to write —
 * so a missing/corrupt sidecar and its media registration land in ONE write).
 * `sidecar === null` means no readable sidecar exists and none is planned:
 * blocked, with the register step that unblocks it named in `detail`.
 * Returns the repaired sidecar object when there is something to write.
 */
export declare function planDraftMaterials(draft: Draft, sidecar: Record<string, unknown> | null, options: {
    sidecarPath: string;
    projectDir: string;
}): {
    target: MaterialsRegistrationTarget;
    fixed: Record<string, unknown> | null;
};
