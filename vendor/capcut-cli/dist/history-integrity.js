import { existsSync, readFileSync } from "node:fs";
import { listSnapshots, readJournal } from "./draft.js";
import { journalDigest } from "./journal-digest.js";
function integrityOf(path, entry) {
    if (!entry || typeof entry.before_sha256 !== "string")
        return "unverified";
    let content;
    try {
        content = readFileSync(path, "utf-8");
    }
    catch {
        return "changed";
    }
    return journalDigest(content) === entry.before_sha256 ? "intact" : "changed";
}
/**
 * A draft's recovery files, each checked against the write journal (read
 * once): the history snapshots newest-first (step 1 = most recent write), and
 * the `.bak`, which holds the content before the most recent write and so is
 * checked against the newest snapshot's entry when `backup()` is called.
 */
export function readRecoveryHistory(filePath) {
    const journal = readJournal(filePath);
    const snapshots = listSnapshots(filePath).map((snapshot) => {
        const entry = journal.get(snapshot.index);
        return { ...snapshot, entry, integrity: integrityOf(snapshot.path, entry) };
    });
    return { snapshots, backup: () => integrityOf(`${filePath}.bak`, snapshots[0]?.entry) };
}
/**
 * Which recovery file `restore` copies back: snapshot `step` (1 = the most
 * recent write) when one is asked for, otherwise the `.bak`. A file that
 * changed on disk since capcut-cli wrote it is refused unless `forceWrite`;
 * each refusal names its check and the way out. `projectPath` is the project
 * as the user named it, for the commands the messages suggest.
 */
export function pickRestoreSource(history, filePath, projectPath, opts) {
    const snaps = history.snapshots;
    if (opts.step !== undefined) {
        if (!Number.isInteger(opts.step) || opts.step < 1) {
            throw new Error("--step must be a positive integer (1 = most recent).");
        }
        const target = snaps.find((s) => s.step === opts.step);
        if (!target) {
            const avail = snaps.length ? `1..${snaps.length}` : "none yet";
            throw new Error(`No snapshot at --step ${opts.step}. Available: ${avail}. Try: capcut restore ${projectPath} --list`);
        }
        if (target.integrity === "changed" && !opts.forceWrite) {
            throw new Error(`refused [snapshot-changed]: snapshot step ${opts.step} no longer holds what capcut-cli saved before that write ` +
                "(it changed on disk afterwards; snapshots written before 0.30.0 share their bytes with the .bak the app rewrites on save). " +
                `Check the others with: capcut restore ${projectPath} --list, or pass --force-write to restore it as it is now.`);
        }
        return { path: target.path, integrity: target.integrity, step: opts.step };
    }
    const bakPath = `${filePath}.bak`;
    if (!existsSync(bakPath)) {
        throw new Error(`No backup found at ${bakPath}. Nothing to restore (a .bak is written on the first edit).`);
    }
    const integrity = history.backup();
    if (integrity === "changed" && !opts.forceWrite) {
        const step1 = snaps[0]?.integrity === "intact"
            ? ` Restore capcut-cli's own copy with: capcut restore ${projectPath} --step 1.`
            : "";
        throw new Error(`refused [backup-changed]: ${bakPath} no longer holds what capcut-cli saved before its last write ` +
            `(the app rewrites it when it saves).${step1} Pass --force-write to restore the .bak as it is now.`);
    }
    return { path: bakPath, integrity };
}
