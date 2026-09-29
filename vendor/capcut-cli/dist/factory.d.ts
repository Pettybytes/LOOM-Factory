import type { Draft } from "./draft.js";
import { type Namespace } from "./enums.js";
import { type WikimediaAsset } from "./wikimedia.js";
/**
 * If `path` is an http(s) URL, resolve it through the Wikimedia fetcher, saving
 * into a `wikimedia/` subdir of the draft's assets folder. Non-Wikimedia URLs
 * error — keeping network scope tight. Returns the local path + the fetched
 * asset (null for plain filesystem paths).
 */
export declare function resolveAssetPath(path: string, draftFilePath: string, kind: "video" | "audio", forceLicense?: boolean): Promise<{
    localPath: string;
    asset: WikimediaAsset | null;
    warning?: string;
}>;
export declare function uuid(): string;
/**
 * Copy a source file into an assets directory, keyed by its basename.
 *
 * The skip-on-exists behaviour is intentional and load-bearing: the Wikimedia
 * fetch path pre-writes into the same dir so this copy is a deliberate no-op,
 * and re-adding the same file must not re-copy. But when a *different* source
 * resolves to a basename already present (e.g. `en/0_00.png` vs `jp/0_00.png`),
 * skipping silently leaves the draft pointing at the wrong content. In that case
 * we de-collide by writing under a content-hashed name and warn on stderr.
 *
 * Returns the destination path the draft should reference.
 */
export declare function copyAssetDeduped(srcPath: string, assetsDir: string, fallbackName: string): string;
export interface InitOptions {
    name: string;
    templateDir: string;
    draftsDir: string;
    now?: number;
    /** Canvas override (see resolveCanvas); the template's canvas_config is kept when absent. */
    canvas?: CanvasConfig;
    /**
     * Store seeding (#67, #111). `auto` (the default): when the drafts folder
     * holds projects from a newer app major than the template declares, build
     * the skeleton from the store's best project instead of the template —
     * exactly the case where the template's draft is refused. `always`: seed
     * whenever the store holds a readable project. `off`: copy the template
     * as-is (`--template bundled`, or an explicit --template directory).
     */
    seed?: "auto" | "always" | "off";
}
export interface CanvasConfig {
    width: number;
    height: number;
    ratio: string;
}
/**
 * The aspect presets CapCut's canvas picker offers, at the pixel sizes the app
 * itself uses for them. The bundled template is 1920x1080 "16:9", so a portrait
 * short — the format most of this repo's users cut for — needed either a
 * hand-written spec through `compile` or a second step; the most-diverged
 * community fork shipped `init --width/--height` for exactly that.
 */
export declare const CANVAS_RATIO_PRESETS: Readonly<Record<string, {
    width: number;
    height: number;
}>>;
/**
 * Resolve `--ratio` / `--width` / `--height` into a canvas_config, or null when
 * none was given. A preset alone picks its native size; explicit width+height
 * win over the preset's size but keep its label; width+height without a preset
 * get the matching preset label when the pair reduces to one, else "original"
 * (the label CapCut writes for a custom canvas). One of width/height alone is an
 * error — the app has no notion of a half-specified canvas.
 */
export declare function resolveCanvas(opts: {
    width?: number;
    height?: number;
    ratio?: string;
}): CanvasConfig | null;
/**
 * One project in a drafts ROOT that `init` can seed a new draft from: its
 * canonical timeline document, the app version it declares, and whether it
 * carries the schema markers only the app writes (`version`, `new_version`,
 * `last_modified_platform`) — the markers the bundled template lacks and that
 * modern builds refuse a draft without (#67, #111).
 */
export interface StoreSeed {
    projectDir: string;
    filePath: string;
    draft: Draft;
    appVersion: string;
    /** True when the project carries app-written schema markers (not a CLI-built draft). */
    appAuthored: boolean;
    mtimeMs: number;
}
/**
 * The best seed in a drafts ROOT: app-authored projects first (a draft this
 * CLI built from the bundled template would only reproduce the template),
 * then the newest `platform.app_version`, then the most recently modified.
 * Null when the folder holds no readable draft. One reading per project
 * folder; the new draft being created is never in the scan (the store is read
 * before its folder exists).
 */
export interface StoreScan {
    seed: StoreSeed | null;
    /** Newest `platform.app_version` across every readable project (the #67 comparison value). */
    newestVersion: string | null;
    /** What the folder held, project by project — the part of the scan the
     * template report shows the user when nothing could seed. */
    store: StoreScanSummary;
}
/**
 * Per-project outcome of a store scan. A project is a sub-folder holding a
 * draft_info.json or draft_content.json. `readable` parsed and declares an
 * app version (the only kind that can seed or be compared against);
 * `markerless` parsed without one; `encrypted` is the JianYing 6.0+ payload
 * this CLI deliberately does not read (docs/jianying-encryption.md) — on a
 * JianYing store that is every project the app wrote; `unreadable` is any
 * other document JSON.parse rejects.
 */
export interface StoreScanSummary {
    projects: number;
    readable: number;
    markerless: number;
    encrypted: number;
    unreadable: number;
}
export declare function scanStore(draftsDir: string, options?: {
    exclude?: string;
}): StoreScan;
/**
 * A JianYing 6.0+ store holds nothing the CLI can seed from: every project
 * the app wrote is an encrypted payload (docs/jianying-encryption.md), so
 * `init` falls back to the bundled template — and until now said nothing
 * about the projects it skipped, leaving the user to learn whether the app
 * accepts that draft by opening it. Name the fallback and what is known
 * about it. Fires only when the store holds encrypted projects and no
 * readable seed at all: a readable donor is covered by seeding, and an
 * explicit `--template` (seed off) is the caller's own choice.
 */
export declare function encryptedStoreWarning(scan: StoreScan, seedMode: "auto" | "always" | "off", templateVersion: string | null): string | null;
export declare function findStoreSeed(draftsDir: string, options?: {
    exclude?: string;
}): StoreSeed | null;
/**
 * Newest `platform.app_version` across the drafts already in a drafts ROOT, or
 * null when the folder holds no readable draft. store.ts's highestVersion does
 * the same job across one project's siblings; `init` has no project to discover
 * yet, so it scans the store instead.
 */
export declare function detectStoreAppVersion(draftsDir: string): string | null;
/** True when the store's projects come from a materially newer app than the template declares (#67's gate). */
export declare function storeOutgrowsTemplate(templateVersion: string | null, storeVersion: string | null): boolean;
/**
 * #67: the bundled template declares app_version 6.5.0 and carries none of the
 * schema markers a modern draft has (`version`, `new_version`, `color_space`).
 * Dropped into a materially newer store, CapCut lists the draft at 00:00 and
 * then refuses to open it, blaming the path — which is not the cause, and which
 * costs the user a long hunt. Name the real reason instead.
 *
 * Since v0.23 `init` seeds the new draft from the store's newest app-authored
 * project by default (`--template auto`), so this fires only when that seeding
 * was switched off (`--template bundled`, or an explicit older template) or
 * when the store holds no project to seed from. Warn, never refuse: the
 * evidence is one 8.5.0 report plus the 8.7.0 / 9.3.0 confirmations in #111,
 * and both escape hatches work. Gated on the major version so a 6.5.0 template
 * stays quiet on a 6.x store.
 */
export declare function templateVersionWarning(templateVersion: string | null, storeVersion: string | null): string | null;
/** How a new draft's skeleton was obtained; reported by init / quickstart / compile. */
export interface TemplateReport {
    /** `store`: seeded from a project in the drafts folder; `path`: the --template directory (or the bundled _init template). */
    source: "store" | "path";
    /** The seed project, or the template directory. */
    path: string;
    /** `platform.app_version` the skeleton declares. */
    app_version: string | null;
    /** Per-project state deliberately not carried over from a template directory. */
    skipped: string[];
    /** Top-level keys reset to empty when seeding from a store project. */
    reset: string[];
    /** What the drafts folder held when the skeleton was chosen (see StoreScanSummary). */
    store: StoreScanSummary;
    /** Why the app may still refuse this skeleton, when the scan could tell: the
     * bundled template in a store the app has outgrown (#67, #111), or a store
     * whose projects are all encrypted and could not seed. Also written to
     * stderr; carried here so quickstart / compile / library callers see it. */
    warning?: string;
}
/**
 * Build an empty draft skeleton from a real project's timeline document: the
 * app's schema markers and settings stay, the content goes. `canvas` / `fps`
 * come from the caller (the bundled template's defaults, or --ratio/--width),
 * not from the donor — a portrait donor must not silently turn every new draft
 * portrait. Returns the skeleton plus the keys it reset, for the report.
 */
export declare function seedDraftSkeleton(donor: Draft, opts: {
    name: string;
    id: string;
    canvas: CanvasConfig;
    fps: number;
    nowMs: number;
    materialKeys: string[];
}): {
    draft: Draft;
    reset: string[];
};
export declare function initDraft(opts: InitOptions): {
    draftPath: string;
    filePath: string;
    registered: boolean;
    canvas: CanvasConfig | null;
    template: TemplateReport;
};
interface RegisterOptions {
    draftsDir: string;
    draftPath: string;
    filePath: string;
    draftId: string;
    name: string;
    nowMs: number;
    durationUs?: number;
}
/**
 * Add the new draft to root_meta_info.json (creating the file if absent) and write
 * a per-folder draft_meta_info.json sidecar. Merges into the existing index — never
 * clobbers other drafts — and backs the index up to .bak before writing.
 * Returns true if the draft was registered in the root index.
 */
export declare function registerDraftInIndex(opts: RegisterOptions): boolean;
export interface RegistrationTarget {
    file: "draft_meta_info.json" | "root_meta_info.json";
    path: string | null;
    state: "ok" | "missing" | "unreadable" | "stale" | "unregistered" | "unknown-store-root";
    action: "none" | "create" | "update" | "blocked";
    detail: string;
    stale_fields: string[];
}
export interface RegistrationPlan {
    project_dir: string;
    /** Path of the identity source (draft_content.json, or draft_info.json on
     * the draft_info-primary Mac layout). Field name kept for compatibility. */
    content_path: string;
    identity_source: string;
    draft_id: string;
    draft_name: string;
    duration_us: number;
    store_root: string | null;
    store_root_source: string;
    needs_repair: boolean;
    targets: RegistrationTarget[];
    /** Files --apply would write (target action create/update). */
    repairs: string[];
    /** Targets needing attention that the CLI refuses to write (target action blocked). */
    blocked: string[];
}
interface RegistrationWrite {
    file: RegistrationTarget["file"];
    path: string;
    content: string;
    /** Raw pre-plan content, null when the file did not exist (no .bak then). */
    previous: string | null;
}
export interface RegistrationResult {
    plan: RegistrationPlan;
    writes: RegistrationWrite[];
}
/**
 * Plan for `capcut register`: report whether an existing draft's
 * draft_meta_info.json sidecar and root_meta_info.json entry are present and
 * agree with the identity source (draft_content.json — or draft_info.json on
 * the draft_info-primary Mac layout; read-only either way), and prepare the
 * exact writes --apply would make. Accepts a project directory or its primary
 * timeline file path; any other explicitly named file is rejected so the plan
 * and the write always cover the same target set. An unreadable
 * root_meta_info.json is never rewritten (it lists EVERY draft); it is
 * reported blocked instead — as is everything when the draft does not live
 * inside a known store root.
 */
export declare function planDraftRegistration(input: string, options?: {
    draftsDir?: string;
    now?: number;
}): RegistrationResult;
/**
 * Perform the writes a registration plan prepared: temp+fsync+rename per file,
 * with a `.bak` of the pre-plan content for every file that already existed.
 * Unless forceWrite, refuses when a target changed on disk between the plan
 * read and now (the sync-timelines optimistic-concurrency rule).
 */
export declare function applyDraftRegistration(result: RegistrationResult, options?: {
    forceWrite?: boolean;
}): {
    applied: string[];
    backups: string[];
};
export interface RenameTarget {
    file: "draft_meta_info.json" | "root_meta_info.json";
    path: string | null;
    state: "present" | "missing" | "unregistered" | "unknown-store-root";
    action: "update" | "none";
    detail: string;
    updated_fields: string[];
}
export interface RenamePlan {
    old_name: string;
    new_name: string;
    old_path: string;
    new_path: string;
    store_root: string | null;
    store_root_source: string;
    targets: RenameTarget[];
    /** Absolute paths the rename rewrites, as they will exist AFTER the folder rename. */
    updates: string[];
    /** Old-folder-path references left inside timeline files (read-only there; `relink` repairs them). */
    stale_media_refs: number;
}
interface RenameWrite {
    file: RenameTarget["file"];
    /** Where the file lives BEFORE the folder rename (concurrency check + rollback source). */
    readPath: string;
    /** Where the rewritten file lands AFTER the folder rename. */
    writePath: string;
    content: string;
    /** Raw pre-plan content. Rename only rewrites files that exist, so never null. */
    previous: string;
}
export interface RenameResult {
    plan: RenamePlan;
    writes: RenameWrite[];
}
/**
 * Plan for `capcut rename`: move the draft folder to `<parent>/<newName>` and
 * rewrite the draft's registration metadata (draft_meta_info.json sidecar +
 * the entry in the store's root_meta_info.json) to match. Accepts the project
 * directory or its primary timeline file path, like register. Refuses invalid
 * names, an existing target folder, and metadata that exists but cannot be
 * parsed — rename never renames around a file it cannot update. A missing
 * sidecar or index entry is only reported (register recreates those); the
 * folder is renamed anyway. Timeline files are read only to count references
 * to the old folder path (stale_media_refs — relink repairs them after).
 */
export declare function planDraftRename(input: string, newName: string, options?: {
    draftsDir?: string;
    now?: number;
}): RenameResult;
/**
 * Perform a rename plan as one transaction: rename the draft folder, then
 * rewrite the planned metadata files with the same temp+fsync+rename pattern
 * register uses, a `.bak` beside every rewritten file. A failed step restores
 * the already-rewritten files and puts the folder back under its old name
 * before rethrowing. Unless forceWrite, refuses when a target changed on disk
 * between the plan read and now (the sync-timelines optimistic-concurrency
 * rule) — and re-checks that the target folder is still free.
 */
export declare function applyDraftRename(result: RenameResult, options?: {
    forceWrite?: boolean;
}): {
    renamed: boolean;
    applied: string[];
    backups: string[];
};
interface CompanionRefs {
    ids: string[];
    materials: Array<{
        type: string;
        data: Record<string, unknown>;
    }>;
}
export declare function createCompanionMaterials(trackType: "text" | "video" | "audio" | "sticker" | "effect"): CompanionRefs;
export declare function registerCompanions(draft: Draft, companions: CompanionRefs): void;
export interface AddTextOptions {
    text: string;
    start: number;
    duration: number;
    fontSize?: number;
    color?: string;
    alignment?: number;
    x?: number;
    y?: number;
    trackName?: string;
}
export declare const STYLE_FIELDS: readonly ["alignment", "font_size", "text_color", "typesetting", "letter_spacing", "line_spacing", "line_feed", "line_max_width", "force_apply_line_max_width", "fixed_width", "fixed_height", "text_alpha", "has_shadow", "shadow_alpha", "shadow_angle", "shadow_color", "shadow_distance", "shadow_smoothing", "has_border", "border_width", "border_color", "border_alpha", "has_text_shadow_config", "background_color", "background_alpha", "background_style", "background_round_radius", "background_width", "background_height", "background_horizontal_offset", "background_vertical_offset", "font_id", "font_name", "font_path", "font_resource_id", "bold", "italic", "underline"];
export declare function copyTextStyle(draft: Draft, refSegmentId: string, targetMaterialId: string, opts?: {
    keepFillColor?: boolean;
}): void;
export declare function addText(draft: Draft, _filePath: string, opts: AddTextOptions): {
    segmentId: string;
    materialId: string;
    trackId: string;
};
export interface AddAudioOptions {
    path: string;
    start: number;
    duration: number;
    volume?: number;
    trackName?: string;
    placeholder?: {
        path: string;
        name: string;
    };
}
export declare function addAudio(draft: Draft, filePath: string, opts: AddAudioOptions): {
    segmentId: string;
    materialId: string;
    trackId: string;
    registered: boolean;
};
export interface AddVideoOptions {
    path: string;
    start: number;
    duration: number;
    type?: "video" | "photo";
    width?: number;
    height?: number;
    trackName?: string;
    placeholder?: {
        path: string;
        name: string;
    };
}
export declare function addVideo(draft: Draft, filePath: string, opts: AddVideoOptions): {
    segmentId: string;
    materialId: string;
    trackId: string;
    registered: boolean;
};
export interface CutOptions {
    start: number;
    end: number;
}
export declare function cutProject(draft: Draft, opts: CutOptions): {
    kept: number;
    removed: number;
};
/**
 * Orphan-material GC shared by `prune` and `remove`: drop every material entry
 * that no surviving segment references via material_id or extra_material_refs
 * (the latter is what keeps masks/effects/animations/fades from being wrongly
 * deleted). Entries without a string id are kept (can't prove they're
 * orphaned). Behavior-identical extraction of the original cmdPrune loop; the
 * caller decides whether a nonzero `removed` warrants a save.
 */
export declare function pruneOrphanMaterials(draft: Draft): {
    removed: number;
    byType: Record<string, {
        removed: number;
        kept: number;
    }>;
};
export interface RemoveSegmentOptions {
    keepTrack?: boolean;
    keepMaterials?: boolean;
    /** Close the removed time span across every track. Refuses when another
     * segment crosses that span, because silently slicing it would change
     * source media; remove/trim the named blocker explicitly first. */
    ripple?: boolean;
}
export interface RemoveSegmentResult {
    segmentId: string;
    trackId: string;
    trackName: string;
    trackType: string;
    trackRemoved: boolean;
    materialsRemoved: number;
    materialsByType: Record<string, {
        removed: number;
        kept: number;
    }>;
    durationBefore: number;
    durationAfter: number;
    rippleShifted: number;
}
/**
 * Remove one segment in place: splice it out of its track, drop the track when
 * it becomes empty (unless keepTrack — cutProject already removes empty tracks
 * wholesale, so CapCut tolerates it), GC newly-orphaned materials with the same
 * conservative sweep prune uses (unless keepMaterials; a material any surviving
 * segment still references is never deleted), and recompute draft.duration as
 * the max segment end across ALL tracks (0 when no segments remain, same as a
 * fresh init draft).
 */
export declare function removeSegment(draft: Draft, segId: string, opts?: RemoveSegmentOptions): RemoveSegmentResult;
export interface Template {
    name: string;
    type: string;
    segment: Record<string, unknown>;
    material: {
        type: string;
        data: Record<string, unknown>;
    };
    extra_materials: Array<{
        type: string;
        data: Record<string, unknown>;
    }>;
}
export declare function saveTemplate(draft: Draft, segId: string, name: string, outPath: string): Template;
export declare function applyTemplate(draft: Draft, templatePath: string, start: number, duration: number, overrides?: {
    x?: number;
    y?: number;
    scaleX?: number;
    scaleY?: number;
    text?: string;
}): {
    segmentId: string;
    materialId: string;
    trackId: string;
};
export interface DuplicateSegmentOptions {
    /** Place the copy onto this existing same-type track instead of creating one. */
    trackName?: string;
}
export interface DuplicateSegmentResult {
    segmentId: string;
    sourceSegmentId: string;
    materialId: string;
    trackId: string;
    trackName: string;
    createdTrack: boolean;
    clonedMaterials: Array<{
        type: string;
        id: string;
        source_id: string;
    }>;
}
/**
 * Duplicate a segment at the SAME timeline position/duration onto a track that
 * renders above the source. Default: a fresh same-type track inserted directly
 * after the source track — sortTracks is stable within a type and a later
 * same-type track renders ABOVE, so the copy sits exactly on top of its
 * source. With `trackName`, the copy goes onto that existing same-type track
 * instead; occupied target range / missing track / type mismatch all throw.
 */
export declare function duplicateSegment(draft: Draft, segId: string, opts?: DuplicateSegmentOptions): DuplicateSegmentResult;
export interface AddStickerOptions {
    resourceId: string;
    start: number;
    duration: number;
    x?: number;
    y?: number;
    scale?: number;
    rotation?: number;
    trackName?: string;
}
export declare function addSticker(draft: Draft, opts: AddStickerOptions): {
    segmentId: string;
    materialId: string;
    trackId: string;
};
export declare function effectSlugs(): string[];
export declare function effectCatalogue(): Array<{
    slug: string;
    member: string;
    name: string;
    effect_id: string;
    resource_id: string;
}>;
export interface AddEffectOptions {
    slug: string;
    start: number;
    duration: number;
    params?: number[];
    trackName?: string;
    namespace?: Namespace;
    /** Raw store/catalogue resource ID — skips slug lookup; `slug` becomes the display name. */
    resourceId?: string;
    /** Raw effect ID; defaults to `resourceId`. Only meaningful with `resourceId`. */
    effectId?: string;
    /** Effect strength 0..1 (material `value`); default 1. */
    intensity?: number;
    /** Experimental: bind the effect to one segment (`apply_target_type: 0`) instead of the whole frame. */
    bindSegmentId?: string;
}
export declare function addEffect(draft: Draft, opts: AddEffectOptions): {
    segmentId: string;
    materialId: string;
    trackId: string;
    name: string;
};
export declare const MIX_MODES: Record<string, string>;
export declare function mixModeSlugs(): string[];
export declare function setMixMode(draft: Draft, segmentId: string, mode: string): {
    segmentId: string;
    material_id: string;
    mix_mode: string;
};
export interface CropRect {
    x: number;
    y: number;
    w: number;
    h: number;
}
export declare function cropPresets(): string[];
export declare function cropRectForRatio(width: number, height: number, preset: string): CropRect;
export declare function getCrop(draft: Draft, segmentId: string): {
    segmentId: string;
    material_id: string;
    width: number | null;
    height: number | null;
    crop: Record<string, number> | null;
};
export declare function setCrop(draft: Draft, segmentId: string, rect: CropRect): {
    segmentId: string;
    material_id: string;
    rect: CropRect;
    crop: Record<string, number>;
    crop_ratio?: string;
};
export declare function setAudioFade(draft: Draft, segmentId: string, opts: {
    fadeInUs?: number;
    fadeOutUs?: number;
}): {
    segmentId: string;
    fade_id: string;
    fade_in_us: number;
    fade_out_us: number;
};
export declare function setCover(draft: Draft, imagePath: string, timeMs?: number): {
    cover_path: string;
    time_ms: number;
};
export declare function filterSlugs(namespace?: Namespace): string[];
export declare function filterCatalogue(): Array<{
    slug: string;
    member: string;
    name: string;
    effect_id: string;
    resource_id: string;
}>;
export interface AddFilterOptions {
    slug: string;
    start: number;
    duration: number;
    intensity?: number;
    trackName?: string;
    namespace?: Namespace;
    /** Raw store/catalogue resource ID — skips slug lookup; `slug` becomes the display name. */
    resourceId?: string;
    /** Raw effect ID; defaults to `resourceId`. Only meaningful with `resourceId`. */
    effectId?: string;
}
export declare function addFilter(draft: Draft, opts: AddFilterOptions): {
    segmentId: string;
    materialId: string;
    trackId: string;
    name: string;
};
export {};
