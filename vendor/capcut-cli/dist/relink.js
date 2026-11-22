import { existsSync, readdirSync, statSync } from "node:fs";
import { basename, isAbsolute, relative, resolve, sep, win32 } from "node:path";
import { copyAssetDeduped } from "./factory.js";
import { draftProjectDir } from "./store.js";
function localFile(path) {
    try {
        return statSync(path).isFile();
    }
    catch {
        return false;
    }
}
function portableBasename(path) {
    return path.includes("\\") ? win32.basename(path) : basename(path);
}
function remapPrefix(path, from, to) {
    const prefix = from.replace(/[/\\]+$/, "");
    if (path === prefix)
        return to;
    if (path.startsWith(prefix) && /[/\\]/.test(path.charAt(prefix.length))) {
        const separator = to.includes("\\") && !to.includes("/") ? "\\" : "/";
        const suffix = path.slice(prefix.length + 1).replace(/[/\\]/g, separator);
        return to.replace(/[/\\]+$/, "") + separator + suffix;
    }
    return path;
}
/** Plan file recovery by basename; multiple candidates remain unresolved. */
export function relinkMedia(draft, filePath, opts) {
    if (!opts.dir && !(opts.from && opts.to)) {
        throw new Error("relink requires --dir or both --from and --to");
    }
    if ((opts.from === undefined) !== (opts.to === undefined)) {
        throw new Error("relink requires both --from and --to");
    }
    const index = new Map();
    if (opts.dir) {
        const root = resolve(opts.dir);
        if (!existsSync(root) || !statSync(root).isDirectory())
            throw new Error(`--dir is not a directory: ${opts.dir}`);
        const walk = (dir) => {
            for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
                const path = resolve(dir, entry.name);
                if (entry.isDirectory() && opts.recursive)
                    walk(path);
                // Do not follow directory symlinks: recursion must not loop or leave the search tree.
                else if (entry.isFile() || (entry.isSymbolicLink() && localFile(path))) {
                    const matches = index.get(entry.name) ?? [];
                    matches.push(path);
                    index.set(entry.name, matches);
                }
            }
        };
        walk(root);
    }
    const projectDir = draftProjectDir(filePath);
    const localPath = (path) => (isAbsolute(path) || win32.isAbsolute(path) ? path : resolve(projectDir, path));
    const changes = [];
    const ambiguous = [];
    let missing = 0;
    let present = 0;
    let staged = 0;
    for (const [kind, values] of Object.entries(draft.materials)) {
        if (!Array.isArray(values))
            continue;
        for (const material of values) {
            const mat = material;
            if (typeof mat.path !== "string" || !mat.path || /^[a-z][a-z0-9+.-]*:\/\//i.test(mat.path))
                continue;
            const original = mat.path;
            let path = opts.from && opts.to ? remapPrefix(original, opts.from, opts.to) : original;
            if (!localFile(localPath(path)) && opts.dir) {
                const matches = index.get(portableBasename(path)) ?? [];
                if (matches.length === 1)
                    path = matches[0];
                else if (matches.length > 1) {
                    ambiguous.push({ id: typeof mat.id === "string" ? mat.id : "", path: original, candidates: matches });
                    missing++;
                    continue;
                }
            }
            const changed = path !== original;
            let didStage = false;
            const diskPath = localPath(path);
            const inside = relative(projectDir, diskPath);
            if (changed &&
                opts.stage &&
                !opts.dryRun &&
                (kind === "videos" || kind === "audios") &&
                localFile(diskPath) &&
                (inside === ".." || inside.startsWith(`..${sep}`) || isAbsolute(inside))) {
                const assetKind = kind === "audios" ? "audio" : "video";
                path = copyAssetDeduped(diskPath, resolve(projectDir, "assets", assetKind), assetKind === "audio" ? "audio.mp3" : "media");
                didStage = true;
                staged++;
                if ("material_name" in mat)
                    mat.material_name = basename(path);
                if ("name" in mat)
                    mat.name = basename(path);
            }
            if (changed && path !== original) {
                changes.push({ id: typeof mat.id === "string" ? mat.id : "", from: original, to: path, staged: didStage });
                mat.path = path;
            }
            if (localFile(localPath(path)))
                present++;
            else
                missing++;
        }
    }
    return { ok: true, relinked: changes.length, staged, still_missing: missing, present, changes, ambiguous };
}
