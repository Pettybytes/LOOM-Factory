import type { CaptionCue, CaptionWord } from "./caption.js";
/**
 * Transcript cache for `caption`.
 *
 * Transcribing is the slow (and, with --cloud, the uploading) step of a
 * caption run; everything after it — --script matching, grouping, styling,
 * --translate — is cheap and deterministic. So the raw transcription is kept,
 * keyed by the exact audio bytes that were transcribed plus every setting that
 * changes what the engine returns, and a run that only changes the styling
 * reads it back instead of transcribing again.
 *
 * One JSON file per key under `transcriptCacheDir()`, written with the same
 * temp + fsync + rename as a draft save. An entry that cannot be read, does
 * not parse, or does not match its key is a miss and is rewritten.
 */
/** Bump when the stored shape, or what a stored transcription means, changes. */
export declare const TRANSCRIPT_CACHE_VERSION = 1;
export declare const TRANSCRIPT_CACHE_ENV = "CAPCUT_CLI_CACHE_DIR";
export type TranscriptCacheState = "hit" | "miss" | "off";
/** Everything that decides what a transcription returns. */
export interface TranscriptCacheKey {
    version: number;
    /** sha256 of the audio bytes handed to the engine (after any stream extraction). */
    audio_sha256: string;
    engine: string;
    whisper_cmd: string | null;
    model: string | null;
    language: string | null;
    /** "words": word-timestamp JSON (--karaoke / --word-reveal, --cloud); "lines": SRT cues. */
    word_timing: "words" | "lines";
    cloud: boolean;
}
export interface CachedTranscription {
    engine: string;
    cues: CaptionCue[];
    words: CaptionWord[];
}
/**
 * Where entries live: CAPCUT_CLI_CACHE_DIR, else $XDG_CACHE_HOME/capcut-cli/transcripts,
 * else ~/.cache/capcut-cli/transcripts (%LOCALAPPDATA%\capcut-cli\cache\transcripts on
 * Windows, where HOME is usually unset).
 */
export declare function transcriptCacheDir(): string;
/** sha256 of a file, read in chunks so long recordings are not held in memory. */
export declare function sha256File(path: string): string;
export declare function transcriptCachePath(key: TranscriptCacheKey, dir?: string): string;
/** The stored transcription for `key`, or undefined when there is no usable entry. */
export declare function readTranscriptCache(key: TranscriptCacheKey): CachedTranscription | undefined;
/**
 * Store a transcription under `key`, atomically. Best effort: a cache that
 * cannot be written never fails the caption run that produced the result.
 */
export declare function writeTranscriptCache(key: TranscriptCacheKey, transcription: CachedTranscription): void;
