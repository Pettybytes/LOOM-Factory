/**
 * Synthesize a voiceover through a user-provided local TTS command.
 *
 * Like caption's whisper integration, the engine is shell-out only: the user
 * supplies the command as a template with `{out}` (required — the audio file
 * the tool must write) and optionally `{text}`. The template is tokenized here
 * and run WITHOUT a shell — `{text}` lands as a single argv token, so spaces,
 * quotes, and metacharacters in the script are inert (the 0.17.1 injection
 * class). When the template has no `{text}`, the text is piped to stdin
 * instead, which is what piper expects and also sidesteps argv length limits.
 */
export interface TtsRunResult {
    outPath: string;
    bytes: number;
    delivery: "stdin" | "argv";
}
/**
 * Split a command template into argv tokens: whitespace-separated, with single
 * or double quotes grouping (the quote characters themselves are dropped).
 * Deliberately no escapes, globs, or substitution — this is not a shell.
 */
export declare function splitCommandTemplate(template: string): string[];
/**
 * Substitute `{out}` / `{text}` into the tokenized template. Each placeholder
 * is replaced inside its token, never re-split, so text containing spaces or
 * quotes stays one argument.
 */
export declare function buildTtsArgv(template: string, text: string, outPath: string): {
    argv: string[];
    delivery: "stdin" | "argv";
};
/**
 * First free `<stem><ext>`, `<stem>-2<ext>`, ... path in `dir` (created if
 * needed). Numbering instead of overwriting: an earlier voiceover may still be
 * referenced by a segment in the draft.
 */
export declare function collisionSafeOutPath(dir: string, stem?: string, ext?: string): string;
/** Run the TTS template and verify it produced a non-empty file at `outPath`. */
export declare function synthesizeSpeech(text: string, template: string, outPath: string): TtsRunResult;
