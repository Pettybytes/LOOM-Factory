import { bubbleCatalogue } from "./decorators.js";
import { listEnum } from "./enums.js";
import { filterCatalogue } from "./factory.js";
import { userEntriesForCategory } from "./user-enums.js";
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
export const SEARCHABLE_CATEGORIES = [
    "transitions",
    "masks",
    "image_intros",
    "image_outros",
    "image_combos",
    "text_intros",
    "text_outros",
    "text_loop_anims",
    "scene_effects",
    "character_effects",
    "audio_effects",
    "fonts",
    "filters",
    "bubbles",
];
// Lower is better: 0 exact, 1 prefix, 2 substring. Ids only match exactly —
// pasting a resource_id/effect_id answers "what is this id?" without letting
// every query fuzzy-hit long numeric strings.
function scoreEntry(entry, q) {
    const texts = [entry.slug, entry.member, entry.name ?? "", entry.title ?? ""]
        .filter((t) => t.length > 0)
        .map((t) => t.toLowerCase());
    if (texts.some((t) => t === q))
        return 0;
    if (entry.resource_id === q || entry.effect_id === q)
        return 0;
    if (texts.some((t) => t.startsWith(q)))
        return 1;
    if (texts.some((t) => t.includes(q)))
        return 2;
    return null;
}
function toMatch(entry, category, source) {
    return {
        category,
        slug: entry.slug,
        member: entry.member,
        name: entry.name ?? entry.title ?? null,
        effect_id: entry.effect_id ?? null,
        resource_id: entry.resource_id ?? null,
        resource_type: entry.resource_type ?? null,
        source,
    };
}
/** Entries of one category with their provenance. listEnum appends user
 * entries after the bundled table, so everything past the bundled count came
 * from the user catalogue. */
function categoryEntries(category, namespace) {
    if (category === "bubbles") {
        return bubbleCatalogue().map((entry) => ({ entry, source: "bundled" }));
    }
    const combined = listEnum(category, namespace);
    const userCount = userEntriesForCategory(category).length;
    const bundledCount = combined.length - userCount;
    const rows = combined.map((entry, index) => ({
        entry,
        source: (index < bundledCount ? "bundled" : "user"),
    }));
    // The capcut namespace ships filters as a starter catalogue in code, not in
    // enums.json — same merge `enums --filters` does.
    if (category === "filters" && namespace === "capcut") {
        return [...filterCatalogue().map((entry) => ({ entry, source: "bundled" })), ...rows];
    }
    return rows;
}
export function searchCatalogue(query, opts = {}) {
    const namespace = opts.namespace ?? "capcut";
    const limit = opts.limit ?? 20;
    const q = query.trim().toLowerCase();
    const categories = opts.kind ? [opts.kind] : SEARCHABLE_CATEGORIES;
    const ranked = [];
    for (const category of categories) {
        for (const { entry, source } of categoryEntries(category, namespace)) {
            const score = scoreEntry(entry, q);
            if (score === null)
                continue;
            ranked.push({ match: toMatch(entry, category, source), score });
        }
    }
    ranked.sort((a, b) => a.score - b.score || a.match.category.localeCompare(b.match.category) || a.match.slug.localeCompare(b.match.slug));
    return ranked.slice(0, limit).map((r) => r.match);
}
