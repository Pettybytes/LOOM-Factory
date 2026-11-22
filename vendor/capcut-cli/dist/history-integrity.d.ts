import { type JournalEntry } from "./draft.js";
/**
 * Whether a recovery file still holds what the CLI recorded before the write
 * it undoes. `intact`: its sha256 equals the journal's `before_sha256`.
 * `changed`: it differs, so something rewrote it after the CLI wrote it (the
 * app rewrites `<draft>.bak` when it saves; snapshots from versions before
 * 0.30.0 are hard links to that `.bak`). `unverified`: no journal entry to
 * compare with (history from before the journal, or a torn journal line).
 */
export type RecoveryIntegrity = "intact" | "changed" | "unverified";
/**
 * A draft's recovery files, each checked against the write journal (read
 * once): the history snapshots newest-first (step 1 = most recent write), and
 * the `.bak`, which holds the content before the most recent write and so is
 * checked against the newest snapshot's entry when `backup()` is called.
 */
export declare function readRecoveryHistory(filePath: string): {
    snapshots: Array<{
        step: number;
        index: number;
        path: string;
        entry: JournalEntry | undefined;
        integrity: RecoveryIntegrity;
    }>;
    backup: () => RecoveryIntegrity;
};
/** The recovery file `restore` copies back: a history step, or the `.bak`. */
export interface RestorePick {
    path: string;
    integrity: RecoveryIntegrity;
    step?: number;
}
/**
 * Which recovery file `restore` copies back: snapshot `step` (1 = the most
 * recent write) when one is asked for, otherwise the `.bak`. A file that
 * changed on disk since capcut-cli wrote it is refused unless `forceWrite`;
 * each refusal names its check and the way out. `projectPath` is the project
 * as the user named it, for the commands the messages suggest.
 */
export declare function pickRestoreSource(history: ReturnType<typeof readRecoveryHistory>, filePath: string, projectPath: string, opts: {
    step?: number;
    forceWrite?: boolean;
}): RestorePick;
