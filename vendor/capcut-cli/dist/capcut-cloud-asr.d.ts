import type { CaptionCue, CaptionWord } from "./caption.js";
/** Pure CRC32 hex string calculation (zero external dependencies). */
export declare function crc32(buf: Uint8Array): string;
export interface AwsSignV4Options {
    host: string;
    path: string;
    service?: string;
    region?: string;
    method?: string;
    body?: string;
    headers?: Record<string, string>;
}
export interface AwsCredentials {
    access_key_id: string;
    secret_access_key: string;
    session_token?: string;
}
/**
 * Pure TypeScript AWS Signature v4 signer (zero-dependency replacement for `aws4`).
 * Conforms exactly to AWS SigV4 specification with ASCII-ordered canonical headers and queries.
 */
export declare function signAwsV4(opts: AwsSignV4Options, creds: AwsCredentials, now?: Date): {
    headers: Record<string, string>;
    url: string;
};
export declare const DEFAULT_CAPCUT_HEADERS: Record<string, string>;
export interface CloudAsrWord {
    text: string;
    start_time: number;
    end_time: number;
    attribute?: Record<string, unknown>;
}
export interface CloudAsrUtterance {
    text: string;
    start_time: number;
    end_time: number;
    words?: CloudAsrWord[];
    attribute?: Record<string, unknown>;
}
export interface CloudAsrPayload {
    utterances?: CloudAsrUtterance[];
    url?: string;
    model?: string;
    duration?: number;
}
export interface CloudAsrOptions {
    durationMs: number;
    verbose?: boolean;
    onProgress?: (progress: number, status: string) => void;
    /** Longest wait for one request before it is abandoned (default 60 s; the upload gets more per MiB). */
    requestTimeoutMs?: number;
    /** Pause before the n-th retry is n times this (default 1.5 s). */
    retryDelayMs?: number;
    /** Pause between status polls (default 2 s). */
    pollIntervalMs?: number;
    /** Longest wait for the recognition to finish (default: pollBudgetMs of the audio's length). */
    pollBudgetMs?: number;
}
/** How long to wait for the recognition: 2 minutes plus half the audio's length, at most 30 minutes. */
export declare function pollBudgetMs(durationMs: number): number;
export interface CloudAsrResult {
    cues: CaptionCue[];
    words: CaptionWord[];
    engine: "capcut-cloud";
    engine_name: string;
    duration_ms?: number;
}
/**
 * Upload the audio to CapCut's cloud speech recognition and return its
 * sentence cues and word timings (milliseconds from the service, as µs).
 */
export declare function transcribeAudioCapcutCloud(audioPath: string, opts: CloudAsrOptions): Promise<CloudAsrResult>;
