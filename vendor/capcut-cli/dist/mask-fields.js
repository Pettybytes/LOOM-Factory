import { atLeast } from "./version.js";
/** The variants in on-disk era order, oldest first (report and listing order). */
export const MASK_FIELD_NAMES = ["masks", "common_mask", "common_masks"];
// Priority newest-first: when consistency-following has to pick between
// multiple populated variants, prefer the newest-era array.
export const MASK_FIELDS = ["common_masks", "common_mask", "masks"];
export function maskEntries(draft, field) {
    const arr = draft.materials[field];
    return Array.isArray(arr) ? arr : [];
}
/** The variants holding at least one entry, in on-disk era order. */
export function populatedMaskFields(draft) {
    return MASK_FIELD_NAMES.filter((field) => maskEntries(draft, field).length > 0);
}
/** The one array a JianYing build reads masks from: 9.6 renamed masks[] -> common_masks[]. */
export function jianyingMaskField(version) {
    return atLeast(version, "9.6") ? "common_masks" : "masks";
}
