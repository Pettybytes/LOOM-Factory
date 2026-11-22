import { type DraftCandidate, type DraftStore, type DraftStoreOptions } from "./store.js";
export interface Timerange {
    start: number;
    duration: number;
}
export interface Segment {
    id: string;
    material_id: string;
    target_timerange: Timerange;
    source_timerange: Timerange;
    speed: number;
    volume: number;
    visible: boolean;
    clip: {
        alpha: number;
        rotation: number;
        scale: {
            x: number;
            y: number;
        };
        transform: {
            x: number;
            y: number;
        };
    } | null;
    extra_material_refs: string[];
    render_index: number;
    [key: string]: unknown;
}
export interface Track {
    id: string;
    type: string;
    name: string;
    attribute: number;
    segments: Segment[];
}
export declare function makeTrack(type: string, name: string, isDefaultName: boolean): Track;
export interface MaterialText {
    id: string;
    type: string;
    content: string;
    font_size: number;
    text_color: string;
    alignment: number;
    [key: string]: unknown;
}
export interface MaterialVideo {
    id: string;
    path: string;
    material_name: string;
    type: string;
    duration: number;
    width: number;
    height: number;
    [key: string]: unknown;
}
export interface MaterialAudio {
    id: string;
    path: string;
    name: string;
    duration: number;
    type: string;
    [key: string]: unknown;
}
export interface Draft {
    id: string;
    name: string;
    duration: number;
    fps: number;
    canvas_config: {
        width: number;
        height: number;
        ratio: string;
    };
    tracks: Track[];
    materials: {
        videos: MaterialVideo[];
        audios: MaterialAudio[];
        texts: MaterialText[];
        speeds: Array<{
            id: string;
            speed: number;
            [key: string]: unknown;
        }>;
        material_animations: Array<Record<string, unknown>>;
        audio_fades: Array<Record<string, unknown>>;
        transitions: Array<Record<string, unknown>>;
        [key: string]: Array<Record<string, unknown>>;
    };
    platform?: {
        app_source: string;
        app_version: string;
        os: string;
    };
    [key: string]: unknown;
}
export declare function findDraft(input: string, options?: DraftStoreOptions): string;
export declare function loadDraft(path: string, options?: DraftStoreOptions): {
    draft: Draft;
    filePath: string;
};
/** Project root captured at load, including an explicit active-timeline selection. */
export declare function loadedDraftProjectDir(filePath: string): string;
export declare function loadedDraftStore(filePath: string): DraftStore;
export declare function sortTracks(draft: Draft): void;
export declare function setDryRun(value: boolean): void;
export declare function isDryRun(): boolean;
export declare function setForceWrite(value: boolean): void;
export interface JournalEntry {
    index: number;
    time: string;
    command: string | null;
    argv: string[] | null;
    before_sha256: string;
    after_sha256: string | null;
}
export declare function setWriteContext(command: string | null, argv: string[] | null): void;
export declare function readJournal(filePath: string): Map<number, JournalEntry>;
export interface LastWrite {
    written_at: string;
    sha256: string;
    command: string | null;
    content: string;
}
/** The digest a record carries (the journal's rule), for comparing a file with it. */
export declare function contentDigest(content: string): string;
/** Record `content` as what the CLI just wrote to `filePath` (temp + rename). */
export declare function recordLastWrite(filePath: string, content: string): void;
/** The last recorded write to `filePath`, or null when there is none (or it is unreadable). */
export declare function readLastWrite(filePath: string): LastWrite | null;
export declare function listSnapshots(filePath: string): Array<{
    step: number;
    index: number;
    path: string;
}>;
export declare function assertTargetsUnchangedOnDisk(targets: DraftCandidate[]): void;
export interface AdditionalDraftFile {
    path: string;
    /** Bytes observed while planning; null only when the file did not exist. */
    raw: string | null;
    content: string;
}
export declare function commitDraftTargets(targets: DraftCandidate[], draft: Draft, options?: {
    backup?: boolean;
    additionalFiles?: AdditionalDraftFile[];
    preserveProjectFields?: boolean;
}): Map<string, string>;
/** Run the normal save gates before a caller stages project assets. Save repeats them at commit. */
export declare function assertDraftWriteable(filePath: string, draft: Draft, options?: {
    skipVersionGuard?: boolean;
    warnings?: boolean;
}, storeSnapshot?: DraftStore): void;
export declare function saveDraft(filePath: string, draft: Draft, options?: {
    backup?: boolean;
    skipVersionGuard?: boolean;
    additionalFiles?: AdditionalDraftFile[];
    preserveProjectFields?: boolean;
}): void;
export declare function writeAtomic(path: string, content: string): void;
export declare function extractText(content: string): string;
export declare function extractStyleRanges(content: string): Array<[number, number]>;
/**
 * Style ranges as code-unit offsets (text-offsets.ts), repairing the doubled
 * form this CLI wrote before 0.19.1 so a reader — `export-srt --granularity
 * word` above all — still lines its highlights up with the words in an old
 * karaoke draft.
 */
export declare function extractCodeUnitStyleRanges(content: string): Array<[number, number]>;
export declare function updateTextContent(content: string, newText: string): string;
export declare function findSegment(draft: Draft, id: string): {
    track: Track;
    segment: Segment;
    index: number;
} | null;
export declare function findMaterial<T extends {
    id: string;
}>(arr: T[], id: string): T | undefined;
export declare function getTracksByType(draft: Draft, type: string): Track[];
export declare function getMaterialTypes(draft: Draft): Array<{
    type: string;
    count: number;
}>;
export declare function findMaterialGlobal(draft: Draft, id: string): {
    type: string;
    material: Record<string, unknown>;
} | null;
