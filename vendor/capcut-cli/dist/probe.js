import { spawnSync } from "node:child_process";
import { statSync } from "node:fs";
/**
 * Variable-frame-rate heuristic: the average frame rate diverges from the
 * container's base rate by more than 1%. VFR media (screen recordings, phone
 * captures) is the classic silent breaker of frame-based pipelines — preview
 * and render timing drift, and strict renderers fail with missing-frame
 * errors (0xsline/OpenChatCut#1's failure class); the standard fix is to
 * normalize to constant frame rate before editing.
 */
export function isVfr(probe) {
    if (probe.avgFps === null || probe.baseFps === null || probe.baseFps <= 0)
        return false;
    return Math.abs(probe.avgFps - probe.baseFps) / probe.baseFps > 0.01;
}
const ffprobeAvailableCache = new Map();
/** True when the ffprobe binary runs. Cached per command string. */
export function ffprobeAvailable(ffprobeCmd = "ffprobe") {
    const cached = ffprobeAvailableCache.get(ffprobeCmd);
    if (cached !== undefined)
        return cached;
    let ok = false;
    try {
        ok = spawnSync(ffprobeCmd, ["-version"], { encoding: "utf-8", timeout: 10_000 }).status === 0;
    }
    catch {
        ok = false;
    }
    ffprobeAvailableCache.set(ffprobeCmd, ok);
    return ok;
}
const probeCache = new Map();
function fraction(value) {
    if (typeof value === "number" && Number.isFinite(value))
        return value;
    if (typeof value !== "string")
        return null;
    const [a, b] = value.split("/").map(Number);
    const result = b ? a / b : a;
    return Number.isFinite(result) && result > 0 ? result : null;
}
export function parseMediaProbe(json) {
    let parsed;
    try {
        parsed = JSON.parse(json);
    }
    catch {
        return null;
    }
    const streams = Array.isArray(parsed.streams) ? parsed.streams : [];
    const video = streams.find((stream) => stream.codec_type === "video");
    const audio = streams.find((stream) => stream.codec_type === "audio");
    if (!video && !audio)
        return null;
    let rotation = 0;
    const tags = video?.tags;
    if (tags?.rotate !== undefined && Number.isFinite(Number(tags.rotate)))
        rotation = Number(tags.rotate);
    else if (Array.isArray(video?.side_data_list)) {
        const item = video.side_data_list.find((value) => Number.isFinite(Number(value.rotation)));
        if (item)
            rotation = Number(item.rotation);
    }
    rotation = normalizeRotation(rotation);
    let width = video && Number(video.width) > 0 ? Number(video.width) : null;
    let height = video && Number(video.height) > 0 ? Number(video.height) : null;
    if ((rotation === 90 || rotation === 270) && width && height)
        [width, height] = [height, width];
    const format = (parsed.format ?? {});
    const durationSeconds = fraction(format.duration) ?? fraction(video?.duration) ?? fraction(audio?.duration);
    const videoDurationSeconds = fraction(video?.duration);
    return {
        width,
        height,
        rotation,
        durationUs: durationSeconds === null ? null : Math.round(durationSeconds * 1_000_000),
        videoDurationUs: videoDurationSeconds === null ? null : Math.round(videoDurationSeconds * 1_000_000),
        fps: fraction(video?.avg_frame_rate) ?? fraction(video?.r_frame_rate),
        avgFps: fraction(video?.avg_frame_rate),
        baseFps: fraction(video?.r_frame_rate),
        hasVideo: Boolean(video),
        hasAudio: Boolean(audio),
        audioChannels: audio && Number(audio.channels) > 0 ? Number(audio.channels) : null,
        videoCodec: typeof video?.codec_name === "string" ? video.codec_name : null,
        audioCodec: typeof audio?.codec_name === "string" ? audio.codec_name : null,
    };
}
export function probeMedia(path, ffprobeCmd = "ffprobe", useCache = true) {
    let cacheKey = `${ffprobeCmd}:${path}`;
    try {
        const stat = statSync(path);
        cacheKey += `:${stat.size}:${stat.mtimeMs}`;
    }
    catch {
        return null;
    }
    if (useCache && probeCache.has(cacheKey))
        return probeCache.get(cacheKey) ?? null;
    let result;
    try {
        result = spawnSync(ffprobeCmd, ["-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", path], {
            encoding: "utf-8",
            timeout: 30_000,
            maxBuffer: 16 * 1024 * 1024,
        });
    }
    catch {
        return null;
    }
    const parsed = result.status === 0 && typeof result.stdout === "string" ? parseMediaProbe(result.stdout) : null;
    if (useCache)
        probeCache.set(cacheKey, parsed);
    return parsed;
}
/** Normalize any degree value (negative or >360) to one of 0/90/180/270. */
export function normalizeRotation(rotation) {
    const r = ((Math.round(rotation) % 360) + 360) % 360;
    // Snap to the nearest quarter turn; ffprobe occasionally reports e.g. -90.
    return (Math.round(r / 90) * 90) % 360;
}
