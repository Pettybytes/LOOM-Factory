import { randomUUID } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, renameSync, rmSync, unlinkSync, writeFileSync, } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { appVersionEvidence, formatAppVersionDriftWarning, trackAppVersion } from "./app-versions.js";
import { writeTemp } from "./atomic-write.js";
import { stripBom } from "./bom.js";
import { journalDigest } from "./journal-digest.js";
import { assertActiveTimelineUnchanged, discoverDraftStore, draftProjectDir, editorProcesses, isManagedDraftPath, nestedTimelinesWriteWarning, serializeDraftCandidate, storeAfterWrite, } from "./store.js";
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
export function findDraft(input, options = {}) {
    return discoverDraftStore(input, options).canonical.path;
}
const loadContexts = new Map();
export function loadDraft(path, options = {}) {
    const store = discoverDraftStore(path, options);
    const filePath = store.canonical.path;
    // The caller gets the parsed timeline itself, not a copy of it. The store
    // snapshot is also used for asset roots and compiler preflight. Everything saveDraft takes from it is
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
/** Project root captured at load, including an explicit active-timeline selection. */
export function loadedDraftProjectDir(filePath) {
    return loadContexts.get(resolve(filePath))?.store.projectDir ?? draftProjectDir(filePath);
}
export function loadedDraftStore(filePath) {
    return loadContexts.get(resolve(filePath))?.store ?? discoverDraftStore(filePath);
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
// The snapshot is its own file, never a second name for `.bak`: the app
// rewrites `<draft>.bak` in place when it saves (CapCut 9.5, issue #151), and
// a hard link would carry that rewrite into the history the CLI restores from.
function writeHistorySnapshot(filePath, content, after) {
    const dir = historyDir(filePath);
    if (!existsSync(dir))
        mkdirSync(dir, { recursive: true });
    const existing = snapshotFiles(filePath);
    const last = existing[existing.length - 1];
    const lastIndex = last ? snapshotIndex(last) : 0;
    const name = `${basename(filePath)}.${String(lastIndex + 1).padStart(6, "0")}.snap`;
    const snapshot = join(dir, name);
    writeFileSync(snapshot, content, "utf-8");
    appendJournal(filePath, {
        index: lastIndex + 1,
        time: new Date().toISOString(),
        command: writeContext.command,
        argv: writeContext.argv,
        before_sha256: journalDigest(content),
        after_sha256: after === undefined ? null : journalDigest(after),
    });
    // Trim oldest beyond the cap.
    const all = snapshotFiles(filePath);
    let trimmed = false;
    while (all.length > HISTORY_MAX) {
        const oldest = all.shift();
        if (oldest)
            rmSync(join(dir, oldest));
        trimmed = true;
    }
    if (trimmed)
        trimJournal(filePath, new Set(all.map(snapshotIndex)));
}
// Set once by the CLI entry point (like setDryRun). Library callers that never
// set it journal `command: null`.
let writeContext = { command: null, argv: null };
export function setWriteContext(command, argv) {
    writeContext = { command, argv: argv === null ? null : [...argv] };
}
function snapshotIndex(name) {
    return Number.parseInt(name.match(/\.(\d+)\.snap$/)?.[1] ?? "0", 10);
}
function journalPath(filePath) {
    return join(historyDir(filePath), `${basename(filePath)}.journal.jsonl`);
}
function appendJournal(filePath, entry) {
    try {
        appendFileSync(journalPath(filePath), `${JSON.stringify(entry)}\n`, "utf-8");
    }
    catch {
        // Best effort: the snapshot is the record that matters.
    }
}
// Parsed journal lines keyed by snapshot index. Unreadable files and lines
// that are not a JSON object with a numeric index are skipped.
export function readJournal(filePath) {
    const entries = new Map();
    let raw;
    try {
        raw = readFileSync(journalPath(filePath), "utf-8");
    }
    catch {
        return entries;
    }
    for (const line of raw.split("\n")) {
        if (!line.trim())
            continue;
        try {
            const entry = JSON.parse(line);
            if (entry && typeof entry === "object" && Number.isInteger(entry.index))
                entries.set(entry.index, entry);
        }
        catch {
            // A torn or hand-edited line never breaks the history listing.
        }
    }
    return entries;
}
// Keep the journal aligned with the snapshots HISTORY_MAX left on disk.
function trimJournal(filePath, kept) {
    try {
        const entries = readJournal(filePath);
        const lines = [...entries.values()]
            .filter((entry) => kept.has(entry.index))
            .sort((a, b) => a.index - b.index)
            .map((entry) => `${JSON.stringify(entry)}\n`);
        writeAtomic(journalPath(filePath), lines.join(""));
    }
    catch {
        // Best effort, like the append.
    }
}
function lastWritePath(filePath) {
    return join(historyDir(filePath), `${basename(filePath)}.last-write`);
}
/** The digest a record carries (the journal's rule), for comparing a file with it. */
export function contentDigest(content) {
    return journalDigest(content);
}
/** Record `content` as what the CLI just wrote to `filePath` (temp + rename). */
export function recordLastWrite(filePath, content) {
    const record = {
        written_at: new Date().toISOString(),
        sha256: journalDigest(content),
        command: writeContext.command,
        content,
    };
    try {
        mkdirSync(historyDir(filePath), { recursive: true });
        writeAtomic(lastWritePath(filePath), `${JSON.stringify(record)}\n`);
    }
    catch {
        // Best effort, like the journal: the draft write already happened. A
        // record of an older write must not outlive this one, though, or the
        // comparison would show this write as a change made by the app.
        try {
            rmSync(lastWritePath(filePath), { force: true });
        }
        catch {
            // Nothing more to do; `diff --since-write` then reports no record.
        }
    }
}
/** The last recorded write to `filePath`, or null when there is none (or it is unreadable). */
export function readLastWrite(filePath) {
    try {
        const record = JSON.parse(readFileSync(lastWritePath(filePath), "utf-8"));
        if (typeof record?.content !== "string" ||
            typeof record.sha256 !== "string" ||
            typeof record.written_at !== "string") {
            return null;
        }
        return {
            written_at: record.written_at,
            sha256: record.sha256,
            command: typeof record.command === "string" ? record.command : null,
            content: record.content,
        };
    }
    catch {
        return null;
    }
}
// Snapshots newest-first, step 1 = most recent write (equivalent to `.bak`).
export function listSnapshots(filePath) {
    const dir = historyDir(filePath);
    return snapshotFiles(filePath)
        .map((f) => ({ index: snapshotIndex(f), path: join(dir, f) }))
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
export function commitDraftTargets(targets, draft, options = {}) {
    const written = new Map();
    if (dryRun)
        return written;
    // A sidecar can also contain a timeline. Shell compilation merges its
    // import group into that file's serialized timeline and commits it once.
    const embeddedSidecars = new Map();
    const additionalPaths = new Set();
    for (const file of options.additionalFiles ?? []) {
        const path = resolve(file.path);
        if (additionalPaths.has(path))
            throw new Error(`Duplicate draft transaction target: ${path}`);
        additionalPaths.add(path);
        if (options.preserveProjectFields &&
            basename(path) === "draft_meta_info.json" &&
            targets.some((target) => resolve(target.path) === path))
            embeddedSidecars.set(path, file);
    }
    // Prepare every replacement before renaming any target. This keeps the
    // multi-file write as close to a transaction as the filesystem allows.
    const replacements = [
        ...targets.map((target) => ({
            path: target.path,
            raw: target.raw,
            content: serializeDraftCandidate(target, draft, options.preserveProjectFields, embeddedSidecars.has(resolve(target.path))
                ? { draft_materials: JSON.parse(embeddedSidecars.get(resolve(target.path)).content).draft_materials }
                : undefined),
        })),
        ...(options.additionalFiles ?? []).filter((file) => !embeddedSidecars.has(resolve(file.path))),
    ];
    const paths = new Set();
    for (const item of replacements) {
        let path = existsSync(item.path)
            ? realpathSync.native(item.path)
            : resolve(realpathSync.native(dirname(item.path)), basename(item.path));
        if (process.platform === "win32")
            path = path.toLowerCase();
        if (paths.has(path))
            throw new Error(`Duplicate draft transaction target: ${path}`);
        paths.add(path);
    }
    for (const item of options.additionalFiles ?? []) {
        const current = existsSync(item.path) ? readFileSync(item.path, "utf-8") : null;
        if (current !== item.raw) {
            throw new Error(`refused [draft-changed-on-disk]: Draft sidecar changed after it was planned: ${item.path}`);
        }
    }
    const prepared = [];
    try {
        for (const target of replacements) {
            if (options.additionalFiles?.includes(target) && target.content === target.raw)
                continue;
            prepared.push({ target, temp: writeTemp(target.path, target.content), content: target.content });
        }
    }
    catch (error) {
        for (const item of prepared)
            if (existsSync(item.temp))
                unlinkSync(item.temp);
        throw error;
    }
    const committed = [];
    try {
        for (const item of prepared) {
            if (options.backup !== false && item.target.raw !== null) {
                // The pre-write content under both recovery names: `.bak` (what
                // `restore` reads) and the newest history snapshot (`restore --step 1`).
                writeAtomic(`${item.target.path}.bak`, item.target.raw);
                writeHistorySnapshot(item.target.path, item.target.raw, item.content);
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
            else if (existsSync(item.target.path))
                unlinkSync(item.target.path);
        }
        for (const item of prepared) {
            if (existsSync(item.temp))
                unlinkSync(item.temp);
        }
        throw error;
    }
    // Every timeline file this commit wrote, recorded as written. Sidecars are
    // not timelines and have nothing for `diff --since-write` to compare.
    const timelinePaths = new Set(targets.map((target) => target.path));
    for (const item of committed) {
        if (timelinePaths.has(item.target.path))
            recordLastWrite(item.target.path, item.content);
    }
    // Target path -> the content committed there, so a caller holding a store
    // for these targets can roll it forward without re-reading the project.
    return written;
}
/** Run the normal save gates before a caller stages project assets. Save repeats them at commit. */
export function assertDraftWriteable(filePath, draft, options = {}, storeSnapshot) {
    const store = storeSnapshot ?? loadContexts.get(resolve(filePath))?.store ?? discoverDraftStore(filePath);
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
        if (options.warnings !== false && (safety.action === "warn" || (safety.action === "refuse" && forceWrite))) {
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
    if (options.warnings !== false && options.skipVersionGuard !== true && store.layout === "timelines-nested") {
        process.stderr.write(`WARNING: ${nestedTimelinesWriteWarning(store.version)}\n`);
    }
    assertActiveTimelineUnchanged(store);
    if (!forceWrite)
        assertTargetsUnchangedOnDisk(store.targets);
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
    assertDraftWriteable(filePath, draft, options, store);
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
    const timelinePaths = new Set(store.targets.map((target) => target.path));
    const timelineWrites = new Map([...written].filter(([path]) => timelinePaths.has(path)));
    const embeddedSidecar = options.additionalFiles?.some((file) => timelinePaths.has(resolve(file.path)));
    loadContexts.set(resolved, {
        store: embeddedSidecar
            ? discoverDraftStore(filePath, { activeTimeline: store.activeTimeline?.explicit === true })
            : storeAfterWrite(store, draft, timelineWrites, options.preserveProjectFields),
    });
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
