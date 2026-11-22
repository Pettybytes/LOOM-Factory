import type { Draft } from "./draft.js";
export declare const MATTING_OFF = 0;
export declare const MATTING_SMART_PORTRAIT = 3;
export interface MattingObject {
    flag: number;
    has_use_quick_brush: boolean;
    has_use_quick_eraser: boolean;
    interactiveTime: unknown[];
    path: string;
    strokes: unknown[];
    [key: string]: unknown;
}
export interface MattingResult {
    ok: true;
    segmentId: string;
    materialId: string;
    flag: number;
    enabled: boolean;
    /** Other segments that reference the same material — matting is per material, so they change too. */
    shared_segments: string[];
}
/**
 * The matting object with `flag` set. Existing cache fields (the app's `path`,
 * brush strokes) are preserved so toggling never destroys app-authored state;
 * missing fields are filled from the documented off-shape defaults.
 */
export declare function mattingObject(flag: number, existing?: unknown): MattingObject;
/** Turn smart portrait matting on for the segment's video material. */
export declare function setMatting(draft: Draft, segId: string): MattingResult;
/** Turn matting off, writing the documented flag-0 object (cache fields kept). */
export declare function clearMatting(draft: Draft, segId: string): MattingResult;
