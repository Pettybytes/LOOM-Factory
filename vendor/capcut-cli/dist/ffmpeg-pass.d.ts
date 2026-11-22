export interface FfmpegPassOptions {
    /** Command name every error starts with, e.g. "detect-scenes". */
    label: string;
    /** What the pass does, for the timeout message, e.g. "scene detection". */
    what: string;
    cmd: string;
    mediaPath: string;
    args: string[];
    timeoutMs: number;
    maxBuffer: number;
    /** What to try after a timeout. */
    timeoutHint: string;
    /** Which option emits less output after a buffer overflow. */
    bufferHint: string;
}
/**
 * Run one ffmpeg analysis pass (a filter that prints to stderr, output to the
 * null muxer) and return its stderr. Every failure throws an actionable error:
 * a missing binary says how to install or point at one, while a tripped
 * timeout or buffer — ffmpeg ran — says so instead of blaming the install.
 */
export declare function runFfmpegAnalysis(o: FfmpegPassOptions): string;
