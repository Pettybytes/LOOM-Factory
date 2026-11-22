import type { Draft } from "./draft.js";
export interface RelinkOptions {
    dir?: string;
    from?: string;
    to?: string;
    recursive?: boolean;
    stage?: boolean;
    dryRun?: boolean;
}
/** Plan file recovery by basename; multiple candidates remain unresolved. */
export declare function relinkMedia(draft: Draft, filePath: string, opts: RelinkOptions): {
    ok: boolean;
    relinked: number;
    staged: number;
    still_missing: number;
    present: number;
    changes: {
        id: string;
        from: string;
        to: string;
        staged: boolean;
    }[];
    ambiguous: {
        id: string;
        path: string;
        candidates: string[];
    }[];
};
