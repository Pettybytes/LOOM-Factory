import type { CaptionCue } from "./caption.js";
/** The captured headers this client replays; the shared identity is in capcut-cloud-headers.ts. */
export declare const DEFAULT_CAPCUT_TRANSLATE_HEADERS: Record<string, string>;
/**
 * Normalizes user-supplied language string into CapCut's canonical language code,
 * in any letter case. Every alias is written in lower case and every canonical
 * code is also an alias of itself, so one lookup of the lower-cased input finds
 * it; anything else passes through trimmed.
 */
export declare function normalizeLanguageCode(lang: string): string;
export interface CloudTranslateUtterance {
    start_time: number;
    end_time: number;
    text: string;
}
export interface CloudTranslateWord {
    text: string;
    start_time: number;
    end_time: number;
}
export interface CloudTranslateItem {
    start_time: number;
    end_time: number;
    text: string;
    translation_text: string;
    translation_words?: CloudTranslateWord[];
    /** The service sent no translation for this line, so translation_text is the original text. */
    kept_original?: boolean;
}
export interface CloudTranslateOptions {
    from?: string;
    requestTimeoutMs?: number;
    retryDelayMs?: number;
    chunkSize?: number;
}
/**
 * Translate a batch of utterances via ByteDance CapCut cloud translation.
 */
export declare function translateUtterancesCapcutCloud(utterances: CloudTranslateUtterance[], targetLang: string, opts?: CloudTranslateOptions): Promise<CloudTranslateItem[]>;
/**
 * Translate caption cues via CapCut Cloud Translation, updating cue text and
 * word-level karaoke timestamps in microseconds. A cue the service sent no
 * translation for keeps its text and its words, and is counted in kept_original.
 */
export declare function translateCuesCapcutCloud(cues: CaptionCue[], targetLang: string, opts?: CloudTranslateOptions): Promise<{
    cues: CaptionCue[];
    kept_original: number;
}>;
