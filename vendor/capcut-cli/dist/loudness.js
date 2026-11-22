/**
 * `loudness`: how loud each audible clip is, and the gain that brings the
 * draft to a delivery target. A draft carries no master gain, so a CapCut
 * export of a quiet recording uploads as quiet as it was recorded; platforms
 * expect around -14 LUFS (long-form) and louder for shorts. Each clip is
 * measured with ffmpeg's EBU R128 meter over the part of its media the
 * timeline plays; the gain is written as segment volume, which the app applies
 * on export (its master bus is limited, so a boost cannot hard-clip it).
 * With a tolerance or a true-peak ceiling the report is also a delivery gate
 * (`judgeLoudness`) that a script can stop an export on.
 */
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";
import { isLocalPath } from "./materials-register.js";
/** Below this the meter reads silence (ffmpeg reports -70 LUFS). */
const SILENCE_LUFS = -69.9;
export const DEFAULT_MAX_GAIN_DB = 20;
/** Integrated loudness and true peak from ebur128's summary (the last ones printed). */
export function parseEbur128(text) {
    const last = (re) => {
        const all = [...text.matchAll(re)];
        if (all.length === 0)
            return null;
        const value = Number.parseFloat(all[all.length - 1][1]);
        return Number.isFinite(value) ? value : null;
    };
    return { lufs: last(/\bI:\s+(-?[\d.]+|-inf)\s+LUFS/g), peak: last(/\bPeak:\s+(-?[\d.]+|-inf)\s+dBFS/g) };
}
function measure(command, path, startUs, durationUs) {
    const args = ["-hide_banner", "-nostats", "-ss", (startUs / 1e6).toFixed(6)];
    if (durationUs > 0)
        args.push("-t", (durationUs / 1e6).toFixed(6));
    args.push("-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-");
    const r = spawnSync(command, args, { encoding: "utf-8", timeout: 600_000, maxBuffer: 64 * 1024 * 1024 });
    if (r.error && r.error.code === "ENOENT") {
        throw new Error(`loudness needs ffmpeg (its ebur128 meter) and '${command}' was not found. Install FFmpeg, or point --ffmpeg-cmd at it.`);
    }
    return parseEbur128(`${r.stdout ?? ""}${r.stderr ?? ""}`);
}
const round = (value, digits) => Number(value.toFixed(digits));
/** The clips that make sound in the export: audio tracks, and video clips that carry their own sound. */
function audibleSegments(draft) {
    const videos = new Map((draft.materials.videos ?? []).map((m) => [m.id, m]));
    const audios = new Map((draft.materials.audios ?? []).map((m) => [m.id, m]));
    const out = [];
    for (const track of draft.tracks) {
        if (track.type !== "audio" && track.type !== "video")
            continue;
        for (const segment of track.segments) {
            const name = track.name || track.type;
            if (track.type === "audio") {
                const material = audios.get(segment.material_id);
                out.push({
                    segment,
                    track: name,
                    path: material?.path || null,
                    why: material ? undefined : "no audio material",
                });
                continue;
            }
            const material = videos.get(segment.material_id);
            if (!material)
                continue;
            if (material.type === "photo")
                continue;
            if (material.has_sound_separated === true) {
                out.push({ segment, track: name, path: null, why: "sound separated to an audio track" });
                continue;
            }
            out.push({ segment, track: name, path: material.path || null });
        }
    }
    return out;
}
/** Measure every audible clip; with a target, compute the gain each one gets. */
export function measureLoudness(draft, opts = {}) {
    const command = opts.ffmpegCmd ?? "ffmpeg";
    if (opts.perClip && opts.target === undefined)
        throw new Error("--per-clip needs --target <LUFS>.");
    const maxGainDb = opts.maxGainDb ?? DEFAULT_MAX_GAIN_DB;
    if (!Number.isFinite(maxGainDb) || maxGainDb < 0)
        throw new Error("--max-gain-db must be a number >= 0.");
    if (opts.target !== undefined && (!Number.isFinite(opts.target) || opts.target > 0 || opts.target < -70)) {
        throw new Error("--target must be a loudness in LUFS between -70 and 0 (for example -14).");
    }
    if (opts.tolerance !== undefined) {
        if (opts.target === undefined)
            throw new Error("--tolerance needs --target <LUFS>: say what loudness to hold.");
        if (!Number.isFinite(opts.tolerance) || opts.tolerance < 0) {
            throw new Error("--tolerance must be a number of LU >= 0 (for example 1).");
        }
    }
    if (opts.maxTruePeak !== undefined && !Number.isFinite(opts.maxTruePeak)) {
        throw new Error("--max-true-peak must be a true-peak level in dBTP (for example -1).");
    }
    const clips = [];
    for (const { segment, track, path, why } of audibleSegments(draft)) {
        const volume = typeof segment.volume === "number" ? segment.volume : 1;
        const durationUs = segment.target_timerange.duration;
        const clip = {
            segment_id: segment.id,
            track,
            path: path ?? "",
            duration_us: durationUs,
            volume,
            measured_lufs: null,
            true_peak_dbfs: null,
            effective_lufs: null,
        };
        clips.push(clip);
        if (why) {
            clip.skipped = why;
            continue;
        }
        if (volume <= 0) {
            clip.skipped = "muted";
            continue;
        }
        const file = isLocalPath(path) && !isAbsolute(path) && opts.draftDir ? resolve(opts.draftDir, path) : path;
        if (!isLocalPath(file) || !existsSync(file)) {
            clip.skipped = "media not found";
            continue;
        }
        const source = segment.source_timerange;
        const { lufs, peak } = measure(command, file, source?.start ?? 0, source?.duration ?? durationUs);
        clip.true_peak_dbfs = peak;
        if (lufs === null) {
            clip.skipped = "no audio stream";
            continue;
        }
        clip.measured_lufs = lufs;
        if (lufs <= SILENCE_LUFS) {
            clip.skipped = "silent";
            continue;
        }
        clip.effective_lufs = round(lufs + 20 * Math.log10(volume), 2);
    }
    const measured = measuredClips(clips);
    const program = programLufs(measured, (clip) => clip.effective_lufs);
    const report = {
        mode: opts.perClip ? "per-clip" : "uniform",
        program_lufs: program,
        ...(opts.target !== undefined ? { target: opts.target } : {}),
        max_gain_db: maxGainDb,
        clips,
    };
    if (opts.target === undefined)
        return report;
    if (program === null) {
        throw new Error("No clip could be measured (no local, audible media on audio or video tracks), so there is no gain to set.");
    }
    const target = opts.target;
    for (const clip of measured) {
        const wanted = target - (opts.perClip ? clip.effective_lufs : program);
        const gain = Math.min(wanted, maxGainDb);
        clip.gain_db = round(gain, 2);
        clip.new_volume = round(clip.volume * 10 ** (gain / 20), 4);
        if (wanted > maxGainDb)
            clip.clamped = true;
    }
    return report;
}
/** Clips with a loudness reading and a length on the timeline. */
function measuredClips(clips) {
    return clips.filter((clip) => clip.effective_lufs !== null && clip.duration_us > 0);
}
/** Duration-weighted energy mean of the clips' loudness, or null with nothing to weigh. */
function programLufs(measured, lufsOf) {
    const totalUs = measured.reduce((sum, clip) => sum + clip.duration_us, 0);
    if (totalUs <= 0)
        return null;
    const energy = measured.reduce((sum, clip) => sum + clip.duration_us * 10 ** (lufsOf(clip) / 10), 0);
    return round(10 * Math.log10(energy / totalUs), 2);
}
/**
 * Judge the report against a delivery tolerance and a true-peak ceiling; null
 * when neither was asked for. `afterGain` judges the mix with this run's gain
 * (the volumes `applyLoudness` writes, --max-gain-db cap included); without
 * it, the mix as it stands. `failure` is a one-line reason, null on a pass.
 */
export function judgeLoudness(report, opts) {
    if (opts.tolerance === undefined && opts.maxTruePeak === undefined)
        return null;
    const afterGain = Boolean(opts.afterGain) && report.target !== undefined;
    const volumeOf = (clip) => afterGain && clip.new_volume !== undefined ? clip.new_volume : clip.volume;
    const mix = afterGain ? "the mix after this run's gain" : "the mix as it stands";
    const estimate = programLufs(measuredClips(report.clips), (clip) => round(clip.measured_lufs + 20 * Math.log10(volumeOf(clip)), 2));
    const target = report.target;
    if (opts.tolerance !== undefined && target === undefined) {
        throw new Error("--tolerance needs --target <LUFS>: say what loudness to hold.");
    }
    const gate = opts.tolerance !== undefined
        ? { target, tolerance: opts.tolerance, estimate_lufs: estimate }
        : { estimate_lufs: estimate };
    const failures = [];
    if (opts.tolerance !== undefined && target !== undefined) {
        const within = estimate !== null && Math.abs(estimate - target) <= opts.tolerance + 1e-9;
        gate.within = within;
        if (estimate === null)
            failures.push("no clip could be measured, so the loudness cannot be judged");
        else if (!within) {
            const capped = afterGain && report.clips.some((clip) => clip.clamped);
            failures.push(`${mix} is estimated at ${estimate} LUFS, ${round(Math.abs(estimate - target), 2)} LU ` +
                `${estimate < target ? "below" : "above"} the ${target} LUFS target (tolerance ${opts.tolerance} LU)` +
                (capped ? `; --max-gain-db ${report.max_gain_db} capped the boost` : ""));
        }
    }
    if (opts.maxTruePeak !== undefined) {
        const peaks = report.clips
            .filter((clip) => clip.true_peak_dbfs !== null && volumeOf(clip) > 0)
            .map((clip) => clip.true_peak_dbfs + 20 * Math.log10(volumeOf(clip)));
        const peak = peaks.length > 0 ? round(Math.max(...peaks), 2) : null;
        const ok = peak !== null && peak <= opts.maxTruePeak + 1e-9;
        gate.max_true_peak = opts.maxTruePeak;
        gate.estimate_true_peak = peak;
        gate.true_peak_ok = ok;
        if (peak === null)
            failures.push("no clip could be measured, so the true peak cannot be judged");
        else if (!ok) {
            failures.push(`the loudest clip in ${mix} peaks at ${peak} dBTP, above the ${opts.maxTruePeak} dBTP ceiling`);
        }
    }
    return { gate, failure: failures.length > 0 ? failures.join("; ") : null };
}
/** Write each measured clip's new volume into the draft; returns how many changed. */
export function applyLoudness(draft, report) {
    const byId = new Map(report.clips.filter((c) => c.new_volume !== undefined).map((c) => [c.segment_id, c]));
    let changed = 0;
    for (const track of draft.tracks) {
        for (const segment of track.segments) {
            const clip = byId.get(segment.id);
            if (!clip || clip.new_volume === undefined || segment.volume === clip.new_volume)
                continue;
            segment.volume = clip.new_volume;
            changed++;
        }
    }
    return changed;
}
