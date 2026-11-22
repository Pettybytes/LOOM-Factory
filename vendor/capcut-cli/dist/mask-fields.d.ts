import type { Draft } from "./draft.js";
/**
 * Where mask materials live. Three array variants exist on disk: `masks`
 * (legacy JianYing 5.9 era), `common_mask` (the struct this CLI writes,
 * verified against CapCut) and `common_masks` (JianYing 9.6+ — masks left in
 * the legacy array silently no-op there, pyJianYingDraft#160).
 */
export type MaskFieldName = "masks" | "common_mask" | "common_masks";
/** The variants in on-disk era order, oldest first (report and listing order). */
export declare const MASK_FIELD_NAMES: readonly MaskFieldName[];
export declare const MASK_FIELDS: readonly MaskFieldName[];
export declare function maskEntries(draft: Draft, field: MaskFieldName): Array<Record<string, unknown>>;
/** The variants holding at least one entry, in on-disk era order. */
export declare function populatedMaskFields(draft: Draft): MaskFieldName[];
/** The one array a JianYing build reads masks from: 9.6 renamed masks[] -> common_masks[]. */
export declare function jianyingMaskField(version: string): "common_masks" | "masks";
