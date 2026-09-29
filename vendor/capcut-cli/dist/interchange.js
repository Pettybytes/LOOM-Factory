import { basename } from "node:path";
import { extractText } from "./draft.js";
import { framesFor } from "./time.js";
/** The marker colour every caption marker carries — one colour, so an NLE user can filter them. */
export const CAPTION_MARKER_COLOR = "YELLOW";
/**
 * One caption cue as an OTIO Marker.1 (the 0.14-era schema, which every
 * reader still accepts and newer libraries upgrade on read). The cue text is
 * the marker name; it is repeated under metadata.capcut.text because some
 * NLEs shorten or rewrite marker names on import, and `import-timeline` needs
 * the exact text back. metadata.Resolve_OTIO.Note is DaVinci Resolve's own
 * marker-note field (it writes and reads it on its .otio round trips); harmless
 * elsewhere, it makes the cue text show in Resolve's marker panel too.
 */
function captionMarker(text, startFrames, durationFrames, rate, capcut) {
    return {
        OTIO_SCHEMA: "Marker.1",
        color: CAPTION_MARKER_COLOR,
        marked_range: timeRange(startFrames, durationFrames, rate),
        metadata: {
            Resolve_OTIO: { Keywords: [], Note: text },
            capcut: { kind: "caption", text, ...capcut },
        },
        name: text,
    };
}
function rationalTime(value, rate) {
    return { OTIO_SCHEMA: "RationalTime.1", rate, value };
}
function timeRange(startFrames, durationFrames, rate) {
    return {
        OTIO_SCHEMA: "TimeRange.1",
        duration: rationalTime(durationFrames, rate),
        start_time: rationalTime(startFrames, rate),
    };
}
function gap(durationFrames, rate) {
    return {
        OTIO_SCHEMA: "Gap.1",
        effects: [],
        markers: [],
        metadata: {},
        name: "",
        source_range: timeRange(0, durationFrames, rate),
    };
}
const EXPORTABLE_TRACK_KINDS = { video: "Video", audio: "Audio" };
function mediaForSegment(draft, segment) {
    for (const kind of ["videos", "audios"]) {
        for (const mat of draft.materials[kind] ?? []) {
            const m = mat;
            if (m.id !== segment.material_id)
                continue;
            const path = typeof m.path === "string" ? m.path : "";
            return {
                path,
                name: m.material_name ?? m.name ?? (path ? basename(path) : segment.material_id),
                durationUs: typeof m.duration === "number" && Number.isFinite(m.duration) ? m.duration : 0,
            };
        }
    }
    return null;
}
function clipForSegment(draft, segment, rate, toFrames) {
    const media = mediaForSegment(draft, segment);
    const speed = typeof segment.speed === "number" && segment.speed > 0 ? segment.speed : 1;
    const effects = [];
    if (speed !== 1) {
        effects.push({
            OTIO_SCHEMA: "LinearTimeWarp.1",
            effect_name: "LinearTimeWarp",
            metadata: {},
            name: "speed",
            time_scalar: speed,
        });
    }
    const mediaReference = media?.path
        ? {
            OTIO_SCHEMA: "ExternalReference.1",
            available_range: media.durationUs > 0 ? timeRange(0, toFrames(media.durationUs), rate) : null,
            metadata: {},
            name: media.name,
            target_url: media.path,
        }
        : { OTIO_SCHEMA: "MissingReference.1", metadata: {}, name: media?.name ?? segment.material_id };
    return {
        OTIO_SCHEMA: "Clip.1",
        effects,
        markers: [],
        media_reference: mediaReference,
        metadata: {
            capcut: {
                material_id: segment.material_id,
                segment_id: segment.id,
                speed,
                volume: segment.volume,
            },
        },
        name: media?.name ?? segment.material_id,
        source_range: timeRange(toFrames(segment.source_timerange.start), toFrames(segment.source_timerange.duration), rate),
    };
}
/**
 * Timeline (Stack) markers: the ones export-timeline wrote for captions carry
 * metadata.capcut.kind === "caption" and come back as caption cues; any other
 * timeline marker (an editor's own notes) has no CapCut equivalent and is
 * reported once, with the count, like every other skip.
 */
function captionsFromStackMarkers(markers, rate, skipped) {
    const captions = [];
    if (!Array.isArray(markers))
        return captions;
    let foreign = 0;
    for (const marker of markers) {
        const m = marker;
        const capcut = m.metadata?.capcut;
        if (capcut?.kind !== "caption") {
            foreign++;
            continue;
        }
        const range = timeRangeUs(m.marked_range, rate, "caption marker marked_range");
        const text = typeof capcut.text === "string" && capcut.text ? capcut.text : typeof m.name === "string" ? m.name : "";
        if (!text) {
            skipped.push({ track: "(timeline)", type: "markers", reason: "a caption marker carries no text — skipped" });
            continue;
        }
        captions.push({
            text,
            startUs: range.startUs,
            durationUs: Math.max(1, range.durationUs),
            track: typeof capcut.track === "string" && capcut.track ? capcut.track : "captions",
        });
    }
    if (foreign > 0) {
        skipped.push({
            track: "(timeline)",
            type: "markers",
            reason: `${foreign} timeline marker(s) without capcut caption metadata have no CapCut equivalent`,
        });
    }
    return captions;
}
function schemaOf(node) {
    if (node && typeof node === "object" && typeof node.OTIO_SCHEMA === "string") {
        return node.OTIO_SCHEMA;
    }
    return "";
}
function usFromRationalTime(rt, fallbackRate, where) {
    if (schemaOf(rt) !== "RationalTime.1") {
        throw new Error(`import-timeline: ${where} is not a RationalTime.1`);
    }
    const { rate, value } = rt;
    const effectiveRate = typeof rate === "number" && Number.isFinite(rate) && rate > 0 ? rate : fallbackRate;
    if (typeof value !== "number" || !Number.isFinite(value)) {
        throw new Error(`import-timeline: ${where} has no numeric value`);
    }
    return Math.round((value / effectiveRate) * 1_000_000);
}
function timeRangeUs(range, fallbackRate, where) {
    if (schemaOf(range) !== "TimeRange.1") {
        throw new Error(`import-timeline: ${where} is not a TimeRange.1`);
    }
    const r = range;
    return {
        startUs: usFromRationalTime(r.start_time, fallbackRate, `${where}.start_time`),
        durationUs: usFromRationalTime(r.duration, fallbackRate, `${where}.duration`),
    };
}
const IMPORTABLE_TRACK_KINDS = { Video: "video", Audio: "audio" };
function clipPlan(node, rate, trackLabel, skipped) {
    const meta = node.metadata?.capcut;
    const fallbackName = typeof node.name === "string" && node.name ? node.name : "clip";
    if (node.source_range === null || node.source_range === undefined) {
        skipped.push({ track: trackLabel, type: "Clip.1", reason: `clip "${fallbackName}" has no source_range — skipped` });
        return null;
    }
    const source = timeRangeUs(node.source_range, rate, `clip "${fallbackName}" source_range`);
    // Speed: invert the exporter's LinearTimeWarp contract (time_scalar = speed;
    // timeline duration = source duration / time_scalar). Only the first valid
    // LinearTimeWarp counts; everything else on the effects list is reported.
    let speed = 1;
    let sawTimeWarp = false;
    for (const effect of Array.isArray(node.effects) ? node.effects : []) {
        const schema = schemaOf(effect);
        const scalar = effect.time_scalar;
        if (schema === "LinearTimeWarp.1" && !sawTimeWarp && typeof scalar === "number" && scalar > 0) {
            speed = scalar;
            sawTimeWarp = true;
        }
        else {
            skipped.push({
                track: trackLabel,
                type: schema || "effect",
                reason: `unsupported effect on clip "${fallbackName}" — only one LinearTimeWarp with a positive time_scalar maps to speed`,
            });
        }
    }
    let mediaPath = null;
    let mediaName = "";
    let mediaDurationUs = 0;
    const ref = node.media_reference;
    const refSchema = schemaOf(ref);
    if (refSchema === "ExternalReference.1") {
        const r = ref;
        mediaPath = typeof r.target_url === "string" && r.target_url ? r.target_url : null;
        mediaName = typeof r.name === "string" ? r.name : "";
        if (r.available_range !== null && r.available_range !== undefined) {
            mediaDurationUs = timeRangeUs(r.available_range, rate, `clip "${fallbackName}" available_range`).durationUs;
        }
    }
    else if (refSchema === "MissingReference.1") {
        mediaName = typeof ref.name === "string" ? ref.name : "";
    }
    else {
        skipped.push({
            track: trackLabel,
            type: refSchema || "media_reference",
            reason: `unsupported media reference on clip "${fallbackName}" — imported as a placeholder for replace-media`,
        });
    }
    if (Array.isArray(node.markers) && node.markers.length > 0) {
        skipped.push({
            track: trackLabel,
            type: "markers",
            reason: `markers on clip "${fallbackName}" have no CapCut equivalent`,
        });
    }
    const metaVolume = meta && typeof meta.volume === "number" && Number.isFinite(meta.volume) ? meta.volume : null;
    return {
        name: (typeof node.name === "string" && node.name) || mediaName || "clip",
        targetStartUs: 0, // stamped by the track walk
        targetDurationUs: Math.round(source.durationUs / speed),
        sourceStartUs: source.startUs,
        sourceDurationUs: source.durationUs,
        speed,
        volume: metaVolume,
        mediaPath,
        mediaDurationUs,
    };
}
export function otioToImportPlan(doc) {
    if (schemaOf(doc) !== "Timeline.1") {
        throw new Error(`import-timeline: not an OpenTimelineIO Timeline.1 document (OTIO_SCHEMA: ${schemaOf(doc) || "missing"})`);
    }
    const timeline = doc;
    const skipped = [];
    const globalStart = timeline.global_start_time;
    const rate = globalStart && typeof globalStart.rate === "number" && Number.isFinite(globalStart.rate) && globalStart.rate > 0
        ? globalStart.rate
        : 30;
    if (globalStart && typeof globalStart.value === "number" && globalStart.value !== 0) {
        skipped.push({
            track: "(timeline)",
            type: "global_start_time",
            reason: "non-zero timeline start is ignored — CapCut drafts start at 0",
        });
    }
    const stack = timeline.tracks;
    if (schemaOf(stack) !== "Stack.1" || !Array.isArray(stack.children)) {
        throw new Error("import-timeline: timeline has no Stack.1 tracks container");
    }
    const plan = {
        name: typeof timeline.name === "string" ? timeline.name : "",
        rate,
        tracks: [],
        clips: 0,
        gaps: 0,
        captions: captionsFromStackMarkers(stack.markers, rate, skipped),
        skipped,
    };
    const nestedLabel = (parent, node, fallback) => {
        const own = typeof node.name === "string" && node.name ? node.name : fallback;
        return parent ? `${parent} / ${own}` : own;
    };
    const declaredDuration = (node, where) => {
        if (node.source_range === null || node.source_range === undefined)
            return null;
        return timeRangeUs(node.source_range, rate, `${where} source_range`).durationUs;
    };
    const compositionDuration = (value) => {
        const node = value;
        const schema = schemaOf(node);
        if (schema === "Gap.1")
            return timeRangeUs(node.source_range, rate, "nested gap source_range").durationUs;
        if (schema === "Clip.1") {
            const source = timeRangeUs(node.source_range, rate, "nested clip source_range").durationUs;
            const warp = (Array.isArray(node.effects) ? node.effects : []).find((effect) => schemaOf(effect) === "LinearTimeWarp.1" &&
                typeof effect.time_scalar === "number" &&
                Number(effect.time_scalar) > 0);
            return Math.round(source / (warp?.time_scalar ?? 1));
        }
        if (schema === "Timeline.1")
            return compositionDuration(node.tracks);
        if (schema === "Track.1") {
            const rangeDuration = declaredDuration(node, "nested track");
            if (rangeDuration !== null)
                return rangeDuration;
            return (Array.isArray(node.children) ? node.children : []).reduce((total, child) => total + compositionDuration(child), 0);
        }
        if (schema === "Stack.1") {
            const rangeDuration = declaredDuration(node, "nested stack");
            if (rangeDuration !== null)
                return rangeDuration;
            return (Array.isArray(node.children) ? node.children : []).reduce((longest, child) => Math.max(longest, compositionDuration(child)), 0);
        }
        return 0;
    };
    const walkStack = (node, startUs, parent, root = false) => {
        const label = root ? parent : nestedLabel(parent, node, "stack");
        const rangeDuration = root ? null : declaredDuration(node, label);
        if (rangeDuration !== null) {
            skipped.push({
                track: label,
                type: "source_range",
                reason: "nested stack source_range trimming is not applied; its declared duration still positions following items",
            });
        }
        if (!root) {
            const nestedCaptions = captionsFromStackMarkers(node.markers, rate, skipped);
            plan.captions.push(...nestedCaptions.map((caption) => ({ ...caption, startUs: caption.startUs + startUs })));
        }
        if (Array.isArray(node.effects) && node.effects.length > 0) {
            skipped.push({ track: label, type: "effects", reason: "stack-level effects have no CapCut equivalent" });
        }
        let durationUs = 0;
        for (const child of Array.isArray(node.children) ? node.children : []) {
            const schema = schemaOf(child);
            if (schema === "Track.1") {
                durationUs = Math.max(durationUs, walkTrack(child, startUs, label));
            }
            else if (schema === "Stack.1") {
                durationUs = Math.max(durationUs, walkStack(child, startUs, label));
            }
            else if (schema === "Timeline.1") {
                durationUs = Math.max(durationUs, walkTimeline(child, startUs, label));
            }
            else {
                const childLabel = nestedLabel(label, child, schema || "unknown");
                skipped.push({
                    track: childLabel,
                    type: schema || "unknown",
                    reason: "unsupported stack child — expected Track.1, Stack.1, or Timeline.1",
                });
                durationUs = Math.max(durationUs, compositionDuration(child));
            }
        }
        return rangeDuration ?? durationUs;
    };
    const walkTimeline = (node, startUs, parent) => {
        const label = nestedLabel(parent, node, "timeline");
        const nestedStart = node.global_start_time;
        if (nestedStart &&
            schemaOf(nestedStart) === "RationalTime.1" &&
            typeof nestedStart.value === "number" &&
            nestedStart.value !== 0) {
            skipped.push({ track: label, type: "global_start_time", reason: "non-zero nested timeline start is ignored" });
        }
        const tracks = node.tracks;
        if (schemaOf(tracks) !== "Stack.1") {
            skipped.push({
                track: label,
                type: schemaOf(tracks) || "tracks",
                reason: "nested timeline has no Stack.1 tracks",
            });
            return 0;
        }
        return walkStack(tracks, startUs, label);
    };
    const walkTrack = (track, startUs, parent) => {
        const label = nestedLabel(parent, track, "track");
        const rangeDuration = declaredDuration(track, label);
        if (rangeDuration !== null) {
            skipped.push({
                track: label,
                type: "source_range",
                reason: "track source_range trimming is not applied; its declared duration still positions following items",
            });
        }
        const kind = IMPORTABLE_TRACK_KINDS[String(track.kind)];
        if (!kind) {
            skipped.push({
                track: label,
                type: String(track.kind ?? "unknown"),
                reason: "unsupported track kind — only Video and Audio tracks import",
            });
            return rangeDuration ?? compositionDuration(track);
        }
        if (Array.isArray(track.effects) && track.effects.length > 0) {
            skipped.push({ track: label, type: "effects", reason: "track-level effects have no CapCut equivalent" });
        }
        if (Array.isArray(track.markers) && track.markers.length > 0) {
            skipped.push({ track: label, type: "markers", reason: "track-level markers have no CapCut equivalent" });
        }
        const clips = [];
        let cursorUs = startUs;
        for (const item of Array.isArray(track.children) ? track.children : []) {
            const itemSchema = schemaOf(item);
            if (itemSchema === "Gap.1") {
                cursorUs += timeRangeUs(item.source_range, rate, `gap in track "${label}"`).durationUs;
                plan.gaps++;
                continue;
            }
            if (itemSchema === "Clip.1") {
                const clip = clipPlan(item, rate, label, skipped);
                if (!clip)
                    continue;
                clip.targetStartUs = cursorUs;
                cursorUs += clip.targetDurationUs;
                clips.push(clip);
                plan.clips++;
                continue;
            }
            if (itemSchema === "Stack.1") {
                cursorUs += walkStack(item, cursorUs, label);
                continue;
            }
            if (itemSchema === "Track.1") {
                cursorUs += walkTrack(item, cursorUs, label);
                continue;
            }
            if (itemSchema === "Timeline.1") {
                cursorUs += walkTimeline(item, cursorUs, label);
                continue;
            }
            skipped.push({
                track: label,
                type: itemSchema || "unknown",
                reason: "unsupported timeline item — clips, gaps, and nested timelines import (transitions overlap, they consume no time)",
            });
        }
        if (clips.length > 0 || (Array.isArray(track.children) && track.children.length === 0)) {
            plan.tracks.push({ kind, name: label, clips });
        }
        return rangeDuration ?? cursorUs - startUs;
    };
    walkStack(stack, 0, "", true);
    return plan;
}
export function draftToOtio(draft, options = {}) {
    const rate = typeof draft.fps === "number" && draft.fps > 0 ? draft.fps : 30;
    const captionMode = options.captions ?? "skip";
    // Issue #82: this used to be a local `Math.round((us / 1e6) * rate)`, a second
    // frame grid alongside time.ts. It differed in two ways that both reached the
    // exported file: a clip shorter than half a frame rounded to a zero-length OTIO
    // clip (an NLE either drops it or refuses the timeline), and small negative
    // gaps rounded to -0, which serialises as `-0` in JSON. framesFor keeps a
    // positive duration at one frame and returns a plain 0 for the zero case.
    const toFrames = (us) => framesFor(us, rate);
    const children = [];
    const stackMarkers = [];
    const stats = { tracks: 0, clips: 0, gaps: 0, captions: 0, skipped: [] };
    for (const track of draft.tracks) {
        const kind = EXPORTABLE_TRACK_KINDS[track.type];
        if (!kind) {
            if (track.type === "text" && captionMode === "markers") {
                const segments = [...track.segments].sort((a, b) => a.target_timerange.start - b.target_timerange.start);
                for (const segment of segments) {
                    const material = (draft.materials.texts ?? []).find((m) => m.id === segment.material_id);
                    const text = material && typeof material.content === "string" ? extractText(material.content) : "";
                    if (!text)
                        continue;
                    stackMarkers.push(captionMarker(text, toFrames(segment.target_timerange.start), Math.max(1, toFrames(segment.target_timerange.duration)), rate, { track: track.name, track_id: track.id, segment_id: segment.id, material_id: segment.material_id }));
                    stats.captions++;
                }
                continue;
            }
            stats.skipped.push({
                track: track.name,
                type: track.type,
                reason: track.type === "text"
                    ? "OTIO has no standard title schema — pass --captions markers to carry cues as timeline markers, or export them with `capcut export-srt` and re-attach them in the NLE"
                    : "no portable OTIO equivalent for this track type",
            });
            continue;
        }
        if (track.segments.length === 0)
            continue;
        const segments = [...track.segments].sort((a, b) => a.target_timerange.start - b.target_timerange.start);
        const items = [];
        let cursorUs = 0;
        for (const segment of segments) {
            const gapUs = segment.target_timerange.start - cursorUs;
            const gapFrames = toFrames(gapUs);
            if (gapFrames > 0) {
                items.push(gap(gapFrames, rate));
                stats.gaps++;
            }
            items.push(clipForSegment(draft, segment, rate, toFrames));
            stats.clips++;
            cursorUs = segment.target_timerange.start + segment.target_timerange.duration;
        }
        children.push({
            OTIO_SCHEMA: "Track.1",
            children: items,
            effects: [],
            kind,
            markers: [],
            metadata: { capcut: { track_id: track.id } },
            name: track.name,
            source_range: null,
        });
        stats.tracks++;
    }
    const doc = {
        OTIO_SCHEMA: "Timeline.1",
        global_start_time: rationalTime(0, rate),
        metadata: {
            capcut: {
                draft_id: draft.id,
                duration_us: draft.duration,
                exported_by: "capcut-cli export-timeline",
            },
        },
        name: draft.name || "capcut draft",
        tracks: {
            OTIO_SCHEMA: "Stack.1",
            children,
            effects: [],
            markers: stackMarkers,
            metadata: {},
            name: "tracks",
            source_range: null,
        },
    };
    return { doc, stats };
}
