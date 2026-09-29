import type { Draft } from "./draft.js";
import type { Category, EnumEntry } from "./enums.js";
/**
 * User enum catalogue (`harvest-enums`).
 *
 * The bundled enums.json only covers resource ids the CLI could already
 * write, and it drifts stale as the apps ship new store effects
 * (GuanYixuan/pyCapCut#12: newer effects missing entirely from ecosystem
 * tables). Instead of guessing at app internals, `harvest-enums` reads ids
 * out of drafts the app itself authored — fully evidence-based — into a
 * per-user catalogue file that the normal lookups then merge in:
 *
 * - every harvested effect_id/resource_id joins lint's known-id set, so
 *   app-authored effects stop self-flagging as unknown-effect-slug;
 * - entries from kinds that map cleanly onto an enums category (effects,
 *   filters, transitions, masks, audio effects) become writable slugs in
 *   findEnum/listEnum — point the CLI at a draft using "Snowfly" once, and
 *   `add-effect snowfly` works from then on;
 * - ambiguous kinds (animations — intro/outro/combo cannot be told apart
 *   from a draft; bubbles; nameless font ids) stay id-only: they inform
 *   lint but are never guessed into a writable category.
 *
 * The bundled table always wins on slug collisions (user entries are
 * appended after it). A hand-broken catalogue file never breaks lookups or
 * lint — it reads as empty and the error is surfaced by `harvest-enums`.
 */
export declare const USER_ENUMS_ENV = "CAPCUT_CLI_USER_ENUMS";
export type HarvestKind = "video_effects" | "filters" | "transitions" | "masks" | "audio_effects" | "animations" | "bubbles" | "fonts";
export interface UserEnumEntry {
    kind: HarvestKind;
    slug: string;
    name: string;
    effect_id?: string;
    resource_id?: string;
    resource_type?: string;
    harvested_from?: string;
    harvested_at?: string;
}
export declare const HARVEST_KINDS: readonly HarvestKind[];
export declare function userEnumsPath(override?: string): string;
export declare function clearUserEnumsCache(): void;
export declare function loadUserEnums(path?: string): {
    entries: UserEnumEntry[];
    error: string | null;
};
/** User entries that belong to a bundled category, as findEnum-compatible
 * EnumEntry objects. Only slug-carrying entries are returned — id-only
 * entries inform lint via allUserEnumIds(), never slug lookup. */
export declare function userEntriesForCategory(category: Category, path?: string): EnumEntry[];
/** Every effect_id/resource_id in the user catalogue — lint's known-id feed. */
export declare function allUserEnumIds(path?: string): Set<string>;
export declare function slugify(name: string): string;
export interface HarvestResult {
    found: number;
    known: number;
    candidates: UserEnumEntry[];
}
/**
 * Scan a draft's effect-shaped material arrays for ids the known-id set does
 * not cover. Candidates keep the draft's own name/ids verbatim; kinds without
 * a safe category mapping come back slug-less (id-only).
 */
export declare function harvestDraft(draft: Draft, knownIds: Set<string>): HarvestResult;
/** Merge candidates into the catalogue file (created if absent). Existing
 * entries win: a candidate whose id pair is already present is a duplicate. */
export declare function mergeUserEnums(path: string, candidates: UserEnumEntry[]): {
    added: number;
    duplicates: number;
    total: number;
};
export interface ManualEntryInput {
    kind: string;
    slug: string;
    resourceId: string;
    effectId?: string;
}
/** Validate a hand-registered entry (`--add`: the witness draft is gone).
 * Refusals come back as `error`; a clashing id never silently re-keys or
 * merges over the entry that already owns it. */
export declare function planManualEntry(input: ManualEntryInput, path?: string): {
    entry: UserEnumEntry;
    error: null;
} | {
    entry: null;
    error: string;
};
