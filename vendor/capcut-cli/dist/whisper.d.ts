/** Minimal cross-platform PATH lookup — no `which`/`where` shell-out. */
export declare function onPath(cmd: string): string | null;
/** A PATH lookup, injectable so the resolution below is testable without a
 * real whisper install on the test host. */
export type PathProbe = (cmd: string) => string | null;
/**
 * Whisper CLI dialects this project knows how to drive, in preference order:
 * openai-whisper's `whisper`, then the name whisper.cpp has shipped since
 * v1.7.4 (`whisper-cli`), then `faster-whisper`.
 *
 * `doctor` has always probed all three, but `caption` only ever spawned
 * `whisper` and defaulted its dialect to openai — so on a host that installs
 * whisper.cpp (a `whisper-cli` binary and no `whisper`), doctor reported the
 * check as ok and the first `caption` run died with ENOENT, hinting at the
 * wrong dialect. One resolver, used by both.
 */
export declare const WHISPER_COMMANDS: readonly ["whisper", "whisper-cli", "faster-whisper"];
export type WhisperDialect = "openai" | "whisper-cpp" | "faster-whisper";
/**
 * The dialect a Whisper command speaks, read from its name: faster-whisper,
 * whisper.cpp (`whisper-cli`, or its older `main` binary), otherwise
 * openai-whisper.
 */
export declare function whisperDialect(command: string): WhisperDialect;
/** First installed Whisper dialect, as an absolute path, or null. */
export declare function resolveWhisperCommand(probe?: PathProbe): string | null;
