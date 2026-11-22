export interface AnonymizationCounts {
    /** Distinct media files given a neutral name. */
    media_files: number;
    /** Media path and media name strings rewritten, in every mirror. */
    media_references: number;
    /** Distinct folder names replaced on media and project paths. */
    folders: number;
    /** Strings set to the neutral project name. */
    project_names: number;
    /** Text strings replaced with placeholders. */
    text_fields: number;
    /** Characters replaced inside those text strings. */
    characters: number;
}
export declare const NEUTRAL_PROJECT_NAME = "project";
/**
 * Replace every letter and digit with a placeholder of the same UTF-16 length
 * and script class: CJK ideographs, kana and Hangul become 字, Latin letters
 * x/X by case (full-width ones ｘ/Ｘ), digits 0. Whitespace, punctuation,
 * symbols and combining marks are kept, so the string keeps its length in code
 * units (the unit CapCut's style ranges count) and its line-break opportunities.
 */
export declare function maskText(text: string): {
    text: string;
    replaced: number;
};
/**
 * Re-emit `raw` (valid JSON) with the string values at the given paths
 * replaced, every other byte unchanged. Keys of `edits` are JSON-encoded paths.
 */
export declare function replaceJsonStrings(raw: string, edits: ReadonlyMap<string, string>): string;
/**
 * One anonymizer per bundle: the same media file, folder and project name map
 * to the same neutral name in every file it rewrites.
 */
export declare class BundleAnonymizer {
    private readonly projectNames;
    private readonly media;
    private readonly mediaNames;
    private readonly folders;
    private readonly counts;
    /** `projectNames`: the project folder's name and the draft's display names. */
    constructor(projectNames: Iterable<string>);
    summary(): AnonymizationCounts;
    /** Anonymize one bundled file's text; text that is not JSON is returned unchanged. */
    anonymizeDocument(raw: string): string;
    /** A folder path shown in the report (source or output directory). */
    anonymizeFolderPath(value: string): string;
    private walk;
    private set;
    private timeline;
    private projectMeta;
    private maskPlain;
    private maskDocument;
    private record;
    private nextMediaName;
    private mediaName;
    private mediaPath;
    private folderPath;
    private folder;
}
