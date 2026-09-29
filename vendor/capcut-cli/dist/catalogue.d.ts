import { type Namespace } from "./enums.js";
/**
 * Cross-category catalogue lookup (`capcut catalogue <query>`): name/slug ->
 * resource_id in one call, without knowing which category flag to pass to
 * `enums`. The pain it removes is well documented across the ecosystem —
 * users hand-extract resource ids from the app when a name is all they have
 * (pyCapCut#12: newer effect ids missing from static tables;
 * pyJianYingDraft#174: no extraction path at all for encrypted-era resources)
 * — and `harvest-enums` already collects ids into the user catalogue, but
 * nothing let you FIND one again by name.
 */
/** Every category `catalogue` searches: the enums.json categories plus the
 * two starter catalogues that live in code (filters, bubbles). Mirrors the
 * category list lint's unknown-slug check covers. */
export declare const SEARCHABLE_CATEGORIES: readonly string[];
export interface CatalogueMatch {
    category: string;
    slug: string;
    member: string;
    name: string | null;
    effect_id: string | null;
    resource_id: string | null;
    resource_type: string | null;
    /** bundled = shipped table or starter catalogue; user = harvest-enums
     * catalogue (~/.config/capcut-cli/user-enums.json). */
    source: "bundled" | "user";
}
export declare function searchCatalogue(query: string, opts?: {
    namespace?: Namespace;
    kind?: string;
    limit?: number;
}): CatalogueMatch[];
