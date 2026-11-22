import type { Draft } from "./draft.js";
export interface TranslateOptions {
    to: string;
    from?: string;
    apiKey?: string;
    model?: string;
    dryRun?: boolean;
    outPath: string;
    cloud?: boolean;
}
export interface TranslateResult {
    ok: boolean;
    count: number;
    to: string;
    from: string;
    out: string;
    pairs: Array<{
        id: string;
        original: string;
        translated: string;
    }>;
    dry_run: boolean;
    engine?: "anthropic" | "capcut-cloud";
    /** --cloud: segments the service sent no translation for, written with their original text. */
    kept_original?: number;
}
/**
 * Translate every text segment in a draft via the Anthropic API or CapCut cloud.
 * Writes the translated draft to `outPath` so the original stays untouched (multi-language
 * shorts factory — same source, N localized renders).
 *
 * Uses built-in `fetch` (Node ≥ 18), zero runtime deps.
 */
export declare function translateDraft(draft: Draft, opts: TranslateOptions): Promise<TranslateResult>;
