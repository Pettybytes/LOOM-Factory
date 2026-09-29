import { randomBytes, randomUUID } from "node:crypto";
import { closeSync, existsSync, fsyncSync, linkSync, mkdirSync, openSync, readdirSync, readFileSync, renameSync, rmSync, unlinkSync, writeFileSync, writeSync, } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { appVersionEvidence, formatAppVersionDriftWarning, trackAppVersion } from "./app-versions.js";
import { stripBom } from "./bom.js";
import { discoverDraftStore, editorProcesses, isManagedDraftPath, nestedTimelinesWriteWarning, serializeDraftCandidate, storeAfterWrite, } from "./store.js";
import { repairDoubledRanges, storedTextLength } from "./text-offsets.js";
import { assessWriteSafety } from "./version.js";
// Fresh empty track. Key order is load-bearing: it is the order CapCut writes,
// and drafts are compared byte-for-byte. Callers append or splice it themselves.
export function makeTrack(type, name, isDefaultName) {
    return {
        id: randomUUID(),
        type,
        name,
        attribute: 0,
        segments: [],
        is_default_name: isDefaultName,
        flag: 0,
    };
}
export function findDraft(input) {
    return discoverDraftStore(input).canonical.path;
}
const loadContexts = new Map();
export function loadDraft(path) {
    const store = discoverDraftStore(path);
    const filePath = store.canonical.path;
    // The caller gets the parsed timeline itself, not a copy of it. The store
    // kept here never escapes this module — `loadContexts` is private and
    // `saveDraft` is its only reader — and everything saveDraft takes from it is
    // either fixed at discovery (`version`, `layout`, `projectDir`) or a string
    // snapshot of the file on disk (`raw`, `path`, `envelopePath`), never
    // anything re-derived from `canonical.draft`. So nothing observes the
    // caller's edits through the store, and cloning the whole draft on every
    // load — tens of milliseconds on a large project, on read commands too —
    // bought nothing. Anything added to saveDraft that wants the draft as it was
    // on disk must re-read it rather than reach for `store.canonical.draft`.
    loadContexts.set(resolve(filePath), { store });
    return { draft: store.canonical.draft, filePath };
}
// Canonical bottom->top layer order CapCut expects in the tracks array.
// Derived from a real CapCut-authored draft: [video, audio, text].
// Tracks are pushed in command-call order as content is added, so without
// this normalization the array order (which drives CapCut's timeline layout)
// ends up scrambled. Unknown types are kept after the known ones.
const TRACK_RANK = {
    video: 0,
    audio: 1,
    sticker: 2,
    effect: 3,
    filter: 4,
    text: 5,
};
// Sort tracks into the canonical layer order. Stable: tracks of the same type
// keep their authored order (tiebreak on original index).
export function sortTracks(draft) {
    draft.tracks = draft.tracks
        .map((track, index) => ({ track, index }))
        .sort((a, b) => (TRACK_RANK[a.track.type] ?? 99) - (TRACK_RANK[b.track.type] ?? 99) || a.index - b.index)
        .map(({ track }) => track);
}
// --dry-run state. When set, saveDraft computes the in-memory change but skips
// both the on-disk write and the .bak snapshot, so the draft is left untouched.
// All ~35 mutating commands funnel through saveDraft, so gating here covers them.
let dryRun = false;
let forceWrite = false;
export function setDryRun(value) {
    dryRun = value;
}
export function isDryRun() {
    return dryRun;
}
export function setForceWrite(value) {
    forceWrite = value;
}
// Multi-step undo history. Alongside the single `.bak`, every write also keeps
// a rolling stack of the pre-write content under `<draftdir>/.capcut-cli-history/`,
// capped at HISTORY_MAX. `restore --step N` rolls back N writes; CapCut ignores
// the hidden dir. snapshots are named `<draftbase>.NNNNNN.snap` (zero-padded,
// monotonically increasing) so the newest is the lexicographically last.
const HISTORY_DIR = ".capcut-cli-history";
const HISTORY_MAX = 20;
function historyDir(filePath) {
    return join(dirname(filePath), HISTORY_DIR);
}
function snapshotFiles(filePath) {
    const dir = historyDir(filePath);
    if (!existsSync(dir))
        return [];
    const prefix = `${basename(filePath)}.`;
    return readdirSync(dir)
        .filter((f) => f.startsWith(prefix) && f.endsWith(".snap"))
        .sort(); // zero-padded indices => lexicographic === numeric order, oldest first
}
// `source`, when given, is a file already holding exactly `content` — the
// `.bak` written moments earlier from the same pre-write bytes. The snapshot
// is then published as a second name for it instead of serializing the whole
// draft to disk a second time, which on a large project is another
// multi-megabyte write per target for bytes already on disk. The two names
// never diverge afterwards: every writer here replaces a name by rename or
// unlink, never edits a file in place, so the next write gives `.bak` a fresh
// file and leaves this snapshot holding what it always held.
function writeHistorySnapshot(filePath, content, source) {
    const dir = historyDir(filePath);
    if (!existsSync(dir))
        mkdirSync(dir, { recursive: true });
    const existing = snapshotFiles(filePath);
    const last = existing[existing.length - 1];
    const lastIndex = last ? Number.parseInt(last.match(/\.(\d+)\.snap$/)?.[1] ?? "0", 10) : 0;
    const name = `${basename(filePath)}.${String(lastIndex + 1).padStart(6, "0")}.snap`;
    const snapshot = join(dir, name);
    let linked = false;
    if (source !== undefined) {
        try {
            linkSync(source, snapshot);
            linked = true;
        }
        catch {
            // Hard links are not universal — FAT/exFAT sticks and some network
            // shares refuse them. Fall back to the full write so the snapshot is
            // always there, whatever the draft folder is sitting on.
        }
    }
    if (!linked)
        writeFileSync(snapshot, content, "utf-8");
    // Trim oldest beyond the cap.
    const all = snapshotFiles(filePath);
    while (all.length > HISTORY_MAX) {
        const oldest = all.shift();
        if (oldest)
            rmSync(join(dir, oldest));
    }
}
// Snapshots newest-first, step 1 = most recent write (equivalent to `.bak`).
export function listSnapshots(filePath) {
    const dir = historyDir(filePath);
    return snapshotFiles(filePath)
        .map((f) => ({ index: Number.parseInt(f.match(/\.(\d+)\.snap$/)?.[1] ?? "0", 10), path: join(dir, f) }))
        .sort((a, b) => b.index - a.index)
        .map((s, i) => ({ step: i + 1, index: s.index, path: s.path }));
}
// Optimistic concurrency check: never silently overwrite a file changed by
// CapCut, another CLI process, or a sync client since the candidates were read.
export function assertTargetsUnchangedOnDisk(targets) {
    for (const target of targets) {
        if (!target.exists || target.raw === null)
            continue;
        // `target.raw` was BOM-stripped at load; strip here too so a BOM'd file
        // that is otherwise untouched does not read as a concurrent change.
        const current = stripBom(readFileSync(target.path, "utf-8"));
        if (current !== target.raw) {
            // The [gate-id] prefix is load-bearing (issue #50): refusal reports
            // arrive paraphrased ("it said something was running"), and two of the
            // three gates mention --force-write, so a stable greppable tag is what
            // makes the next quoted stderr line unambiguous about WHICH gate fired.
            throw new Error(`refused [draft-changed-on-disk]: Draft changed on disk after it was loaded: ${target.name}. ` +
                "Reload and retry, or pass --force-write to overwrite intentionally.");
        }
    }
}
// Transactional multi-target write: serialize `draft` into each target's own
// envelope, then temp+fsync+rename with a `.bak` (and history snapshot) per
// file actually written, rolling back on a partial commit. Writes EXACTLY the
// given targets — callers decide the write set (saveDraft: every readable
// sibling; sync-timelines: only the drifted mirrors). No-ops under --dry-run.
export function commitDraftTargets(targets, draft, options = {}) {
    const written = new Map();
    if (dryRun)
        return written;
    // Prepare every replacement before renaming any target. This keeps the
    // multi-file write as close to a transaction as the filesystem allows.
    const prepared = targets.map((target) => {
        const content = serializeDraftCandidate(target, draft);
        return { target, temp: writeTemp(target.path, content), content };
    });
    const committed = [];
    try {
        for (const item of prepared) {
            if (options.backup !== false && item.target.raw !== null) {
                // One write of the pre-write content, published under both recovery
                // names: `.bak` (what `restore` reads) and the newest history snapshot
                // (what `restore --step 1` reads).
                const bak = `${item.target.path}.bak`;
                writeAtomic(bak, item.target.raw);
                writeHistorySnapshot(item.target.path, item.target.raw, bak);
            }
        }
        for (const item of prepared) {
            renameSync(item.temp, item.target.path);
            committed.push(item);
            written.set(item.target.path, item.content);
        }
    }
    catch (error) {
        // Roll back targets already renamed during a partial commit.
        for (const item of committed.reverse()) {
            if (item.target.raw !== null)
                writeAtomic(item.target.path, item.target.raw);
        }
        for (const item of prepared) {
            if (existsSync(item.temp))
                unlinkSync(item.temp);
        }
        throw error;
    }
    // Target path -> the content committed there, so a caller holding a store
    // for these targets can roll it forward without re-reading the project.
    return written;
}
export function saveDraft(filePath, draft, options = {}) {
    if (dryRun) {
        // Version guard, warning only: dry-run writes nothing, so it never blocks,
        // but the WARNING still previews what a real write would do. Draft-only
        // assessment (no store discovery) keeps dry-run free of extra I/O.
        if (options.skipVersionGuard !== true) {
            const safety = assessWriteSafety(draft, null);
            if (safety.action !== "ok")
                process.stderr.write(`WARNING: ${safety.reasons.join(" ")}\n`);
        }
        // Normalize in memory (so any read-back is consistent) but write nothing.
        sortTracks(draft);
        return;
    }
    const resolved = resolve(filePath);
    const context = loadContexts.get(resolved) ?? { store: discoverDraftStore(filePath) };
    const { store } = context;
    if (!forceWrite && isManagedDraftPath(filePath)) {
        const running = editorProcesses();
        if (running.length > 0) {
            // Tagged like the other write gates (issue #50: a report of this exact
            // message could not be told apart from the version boundary's refusal).
            throw new Error(`refused [editor-open]: ${running.join(" / ")} is running. Close the editor before writing this managed draft, ` +
                "or pass --force-write if you accept that the app may overwrite the change.");
        }
    }
    // Write-time version boundary: refuse when the draft's detected version or
    // schema generation is beyond the registry's evidence or known-broken (new
    // app builds are reported to reject tool-written drafts as corrupted).
    // store.version covers every readable sibling, so a mirror written by a
    // newer app build trips the guard too. Markerless drafts (capcut create
    // output) never trigger. --force-write overrides, but the WARNING still
    // lands on stderr so a forced write is never silent.
    // skipVersionGuard is for `restore`'s mirror re-sync ONLY: restoring a
    // backup is the escape hatch, not the hazard (docs/version-support.md), and
    // a mid-restore refusal would leave the canonical rolled back but the
    // mirrors diverged.
    if (options.skipVersionGuard !== true) {
        const safety = assessWriteSafety(draft, store.version);
        if (safety.action === "refuse" && !forceWrite) {
            throw new Error(`refused [version-boundary]: ${safety.reasons.join("\n")}`);
        }
        if (safety.action === "warn" || (safety.action === "refuse" && forceWrite)) {
            process.stderr.write(`WARNING: ${safety.reasons.join(" ")}\n`);
        }
    }
    // CapCut 7.x nested Timelines/ layout (issue #50): the live document is
    // reported to be Timelines/<id>/draft_info.json, with the root file a
    // regenerated mirror the app rebuilds on open. Detection-only guard — the
    // write below still targets the root candidates byte-identically (PR #51's
    // canonical flip was rejected pending a field artifact) — but the edit is
    // no longer silent: warn, never refuse. Shares skipVersionGuard with the
    // version guard: restore's mirror re-sync is the escape hatch, not a new
    // sighting of the hazard.
    if (options.skipVersionGuard !== true && store.layout === "timelines-nested") {
        process.stderr.write(`WARNING: ${nestedTimelinesWriteWarning(store.version)}\n`);
    }
    if (!forceWrite)
        assertTargetsUnchangedOnDisk(store.targets);
    sortTracks(draft);
    const written = commitDraftTargets(store.targets, draft, options);
    // App auto-upgrade tripwire (pyJianYingDraft#115, #178): warn-only. Compares
    // this store's version evidence against the CLI's last-seen record in
    // ~/.config/capcut-cli/app-versions.json and names old -> new on stderr when
    // the app moved underneath the pipeline; the guard above stays the only
    // refusal. The drift is also remembered for the command's JSON result (see
    // out() / takeAppVersionDrift). Shares skipVersionGuard with the guard:
    // restore's mirror re-sync is the escape hatch, not a sighting.
    if (options.skipVersionGuard !== true) {
        const { drift, error } = trackAppVersion(store.projectDir, appVersionEvidence(draft, store.version));
        if (error)
            process.stderr.write(`WARNING: ${error}\n`);
        if (drift)
            process.stderr.write(`WARNING: ${formatAppVersionDriftWarning(drift)}\n`);
    }
    // Refresh hashes/raw snapshots so a library caller can save the same loaded
    // draft more than once without tripping its own conflict guard. Rolled
    // forward from the bytes the commit just wrote rather than by discovering
    // the project again: a re-discovery re-read, re-parsed and re-hashed every
    // sibling — the single most expensive step of a write on a large draft — to
    // establish what the write already knew.
    loadContexts.set(resolved, { store: storeAfterWrite(store, draft, written) });
}
function writeAndSync(path, content) {
    // "wx" is O_CREAT|O_EXCL: it creates the file or fails, and never follows an
    // existing symlink. See writeTemp for why that matters.
    const fd = openSync(path, "wx", 0o600);
    try {
        writeSync(fd, content, undefined, "utf-8");
        fsyncSync(fd);
    }
    finally {
        closeSync(fd);
    }
}
/**
 * Create the scratch file a temp+fsync+rename write stages into, next to its
 * target, and return its path.
 *
 * The name carries crypto randomness and the open is exclusive. A predictable
 * name (pid + clock alone) is a write-through primitive: anyone able to create
 * files in the draft folder can pre-place a symlink at the path we are about
 * to open and redirect every draft save to a file of their choosing. O_EXCL is
 * the guarantee — it refuses a path that already exists, symlink included —
 * and the randomness keeps a legitimate concurrent writer from colliding with
 * us in the first place. A collision just draws another name; the bytes
 * written are unchanged.
 */
function writeTemp(target, content) {
    for (let attempt = 0; attempt < 8; attempt++) {
        const temp = `${target}.capcut-cli-${process.pid}-${Date.now()}-${randomBytes(8).toString("hex")}.tmp`;
        try {
            writeAndSync(temp, content);
            return temp;
        }
        catch (error) {
            if (error.code !== "EEXIST")
                throw error;
        }
    }
    throw new Error(`Could not create a temp file next to ${target} after 8 attempts.`);
}
// Exported for factory.ts (register): the same temp+fsync+rename write the
// draft save path uses, for metadata files outside commitDraftTargets.
export function writeAtomic(path, content) {
    renameSync(writeTemp(path, content), path);
}
export function extractText(content) {
    try {
        const parsed = JSON.parse(content);
        if (parsed.text)
            return parsed.text;
    }
    catch {
        return content
            .replace(/<[^>]*>/g, "")
            .replace(/\[|\]/g, "")
            .trim();
    }
    return content;
}
// Style `range` entries exactly as stored, for callers that need to judge the
// raw values (the lint detector for pre-0.19.1 doubled offsets). Everything
// that wants to *use* an offset wants extractCodeUnitStyleRanges below.
export function extractStyleRanges(content) {
    try {
        const parsed = JSON.parse(content);
        if (!Array.isArray(parsed.styles))
            return [];
        const ranges = [];
        for (const style of parsed.styles) {
            const r = style.range;
            if (Array.isArray(r) && r.length === 2 && typeof r[0] === "number" && typeof r[1] === "number") {
                ranges.push([r[0], r[1]]);
            }
        }
        return ranges;
    }
    catch {
        return [];
    }
}
/**
 * Style ranges as code-unit offsets (text-offsets.ts), repairing the doubled
 * form this CLI wrote before 0.19.1 so a reader — `export-srt --granularity
 * word` above all — still lines its highlights up with the words in an old
 * karaoke draft.
 */
export function extractCodeUnitStyleRanges(content) {
    const ranges = extractStyleRanges(content);
    const text = extractText(content);
    return repairDoubledRanges(text, ranges) ?? ranges;
}
export function updateTextContent(content, newText) {
    try {
        const parsed = JSON.parse(content);
        if (parsed.text !== undefined) {
            parsed.text = newText;
            if (parsed.styles && parsed.styles.length > 0) {
                parsed.styles[0].range = [0, storedTextLength(newText)];
            }
            return JSON.stringify(parsed);
        }
    }
    catch {
        const match = content.match(/^(.*\])?(.*?)(\[.*)?$/s);
        if (match) {
            return content.replace(/\[[^\]]*\]/, `[${newText}]`);
        }
    }
    return newText;
}
export function findSegment(draft, id) {
    const shortId = id.toLowerCase();
    for (const track of draft.tracks) {
        for (let i = 0; i < track.segments.length; i++) {
            const seg = track.segments[i];
            if (seg.id === id || seg.id.toLowerCase().startsWith(shortId)) {
                return { track, segment: seg, index: i };
            }
        }
    }
    return null;
}
// id -> material index, one per materials array, so a caller that resolves a
// material per segment does not rescan the whole array each time (lint walks
// materials.texts once per caption).
//
// Keyed on the array itself and validated by its length, which is exactly what
// changes when membership does: every command that adds a material pushes onto
// the array, every command that drops one replaces the array with a filtered
// copy (a brand-new key, so a fresh index), and `migrate` moves entries between
// arrays — all of which the length check or the key change catches. Mutating a
// material in place stays visible without any invalidation, because the index
// holds the same objects the array does; that is the case lint's fixDraft
// relies on when it rewrites a text material's `content`.
const materialIndex = new WeakMap();
export function findMaterial(arr, id) {
    const cached = materialIndex.get(arr);
    if (cached !== undefined && cached.length === arr.length)
        return cached.index.get(id);
    // Miss: answer from the scan this function has always done, so a malformed
    // draft still fails with the identical TypeError, then index for next time.
    const found = arr.find((m) => m.id === id);
    const index = new Map();
    for (const m of arr)
        if (!index.has(m.id))
            index.set(m.id, m); // first id wins, as find() did
    materialIndex.set(arr, { length: arr.length, index });
    return found;
}
export function getTracksByType(draft, type) {
    return draft.tracks.filter((t) => t.type === type);
}
export function getMaterialTypes(draft) {
    return Object.entries(draft.materials)
        .filter(([, v]) => Array.isArray(v))
        .map(([type, arr]) => ({ type, count: arr.length }))
        .sort((a, b) => b.count - a.count);
}
export function findMaterialGlobal(draft, id) {
    const shortId = id.toLowerCase();
    for (const [type, arr] of Object.entries(draft.materials)) {
        if (!Array.isArray(arr))
            continue;
        for (const mat of arr) {
            if (mat && typeof mat === "object" && typeof mat.id === "string") {
                const m = mat;
                const matId = m.id;
                if (matId === id || matId.toLowerCase().startsWith(shortId)) {
                    return { type, material: m };
                }
            }
        }
    }
    return null;
}
