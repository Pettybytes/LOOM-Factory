import { randomUUID } from "node:crypto";
import { findMaterial } from "./draft.js";
import { findEnum, slugsFor } from "./enums.js";
import { jianyingMaskField, MASK_FIELDS, maskEntries } from "./mask-fields.js";
import { requireSegment } from "./segment-lookup.js";
import { storedTextLength, toStoredOffset } from "./text-offsets.js";
const PROPERTY_MAP = {
    position_x: "KFTypePositionX",
    position_y: "KFTypePositionY",
    rotation: "KFTypeRotation",
    scale_x: "KFTypeScaleX",
    scale_y: "KFTypeScaleY",
    uniform_scale: "UNIFORM_SCALE",
    alpha: "KFTypeAlpha",
    saturation: "KFTypeSaturation",
    contrast: "KFTypeContrast",
    brightness: "KFTypeBrightness",
    volume: "KFTypeVolume",
};
export function keyframeProperties() {
    return Object.keys(PROPERTY_MAP);
}
// IR-style property names. Every downstream integrator that drives keyframes
// from its own intermediate representation ends up re-implementing the same
// four-line translation table (vertir's PROP_MAP, qcut's spec.py: scale →
// uniform_scale, x/y → position_x/y, opacity → alpha). Accept the aliases
// wherever a property name is read — `keyframe`, `keyframe --batch`, compile's
// keyframe op — and store under the canonical name, so `lists`, `describe` and
// the on-disk property_type stay unambiguous. `scale` deliberately means the
// UNIFORM scale (the IR meaning), never scale_x/scale_y.
const PROPERTY_ALIASES = {
    scale: "uniform_scale",
    x: "position_x",
    y: "position_y",
    opacity: "alpha",
};
export function keyframePropertyAliases() {
    return { ...PROPERTY_ALIASES };
}
/** Canonical property name for `name`, resolving aliases; throws on anything else. */
export function resolveKeyframeProperty(name) {
    const key = String(name).trim();
    if (Object.hasOwn(PROPERTY_MAP, key))
        return key;
    if (Object.hasOwn(PROPERTY_ALIASES, key))
        return PROPERTY_ALIASES[key];
    const aliases = Object.entries(PROPERTY_ALIASES)
        .map(([alias, canonical]) => `${alias}=${canonical}`)
        .join(", ");
    throw new Error(`Unsupported keyframe property: ${name}. Supported: ${Object.keys(PROPERTY_MAP).join(", ")} (aliases: ${aliases})`);
}
/**
 * The on-disk `property_type` identifiers the keyframe machinery knows how to
 * write. Used by the `fixture` mask-keyframe harvest (#44) to split the
 * property types found in a real draft into known vs unknown — an unknown one
 * on a masked segment is exactly the ground-truth signal that issue needs.
 */
export function keyframePropertyTypes() {
    return Object.values(PROPERTY_MAP);
}
const EASING_PROFILES = {
    "ease-in": { startRightXRatio: 0.42, endLeftXRatio: 0 },
    "ease-out": { startRightXRatio: 0.32, endLeftXRatio: -0.4 },
    "ease-in-out": { startRightXRatio: 0.42, endLeftXRatio: -0.42 },
};
// CapCut "Cubic Out" Δ-scales the outgoing handle: right_control.y =
// round(0.94 × (nextValue − value), 6). A fixed y would only match the UI for
// one specific value delta.
const EASE_OUT_RIGHT_Y_RATIO = 0.94;
export function keyframeEasings() {
    return ["linear", ...Object.keys(EASING_PROFILES), "hold"];
}
// Exported so compile's spec validation pre-flights the exact same check
// (and error message) the real keyframe write performs. Object.hasOwn, not
// `in`: inherited Object.prototype names ("hasOwnProperty", "toString",
// "constructor", ...) must NOT pass — EASING_PROFILES[name] would resolve to
// an inherited function whose ratios are undefined and NaN-corrupt the draft.
export function resolveEasing(easing) {
    if (easing === undefined)
        return "linear";
    if (easing !== "linear" && easing !== "hold" && !Object.hasOwn(EASING_PROFILES, easing)) {
        throw new Error(`Unsupported keyframe easing: ${easing}. Supported: ${keyframeEasings().join(", ")}`);
    }
    return easing;
}
export function parseKeyframeValue(propertyOrAlias, value) {
    const property = resolveKeyframeProperty(propertyOrAlias);
    const v = value.trim();
    if (property === "position_x" || property === "position_y") {
        const n = parseFloat(v);
        if (!Number.isFinite(n) || n < -10 || n > 10)
            throw new Error(`${property} must be a finite number in [-10, 10], got: ${value}`);
        return n;
    }
    if (property === "rotation") {
        const raw = v.endsWith("deg") ? v.slice(0, -3) : v;
        const n = parseFloat(raw);
        if (!Number.isFinite(n))
            throw new Error(`Invalid rotation value: ${value}`);
        return n;
    }
    if (property === "alpha" || property === "volume") {
        if (v.endsWith("%")) {
            const n = parseFloat(v.slice(0, -1));
            if (!Number.isFinite(n))
                throw new Error(`Invalid ${property} percentage: ${value}`);
            return n / 100;
        }
        const n = parseFloat(v);
        if (!Number.isFinite(n))
            throw new Error(`Invalid ${property} value: ${value}`);
        return n;
    }
    if (property === "saturation" || property === "contrast" || property === "brightness") {
        if (v.startsWith("+")) {
            const n = parseFloat(v.slice(1));
            if (!Number.isFinite(n))
                throw new Error(`Invalid ${property} value: ${value}`);
            return n;
        }
        if (v.startsWith("-")) {
            const n = parseFloat(v.slice(1));
            if (!Number.isFinite(n))
                throw new Error(`Invalid ${property} value: ${value}`);
            return -n;
        }
        const n = parseFloat(v);
        if (!Number.isFinite(n))
            throw new Error(`Invalid ${property} value: ${value}`);
        return n;
    }
    const n = parseFloat(v);
    if (!Number.isFinite(n))
        throw new Error(`Invalid ${property} value: ${value}`);
    return n;
}
export function uuidHex() {
    return randomUUID().replace(/-/g, "");
}
// Nearest existing keyframes strictly before/after the given time — the two
// segment neighbours whose facing handles an insert at that time affects.
function findNeighbours(list, timeOffset) {
    let prev;
    let next;
    for (const k of list) {
        if (k.time_offset < timeOffset && (!prev || k.time_offset > prev.time_offset))
            prev = k;
        if (k.time_offset > timeOffset && (!next || k.time_offset < next.time_offset))
            next = k;
    }
    return { prev, next };
}
const isZeroControl = (c) => c.x === 0 && c.y === 0;
// Inserting a LINEAR keyframe between two keyframes invalidates the handles
// that faced across the old prev→next segment: prev's outgoing and next's
// incoming handles were computed against an interval/Δ that no longer exists,
// so left as-is they can point past the inserted keyframe (overshoot + snap
// back in CapCut). Clear the facing handles — the inserted keyframe's easing
// wins, mirroring applyEasing — and drop a neighbour back to "Line" when it is
// left with no handles at all, so curveType never contradicts the handles.
function clearFacingHandles(list, timeOffset) {
    const { prev, next } = findNeighbours(list, timeOffset);
    if (prev) {
        prev.right_control = { x: 0, y: 0 };
        if (isZeroControl(prev.left_control))
            prev.curveType = "Line";
    }
    if (next) {
        next.left_control = { x: 0, y: 0 };
        if (isZeroControl(next.right_control))
            next.curveType = "Line";
    }
}
// Stamp the inserted keyframe and retro-update the facing handles of its two
// neighbours so the easing applies to both adjacent segments — the easing of
// the inserted keyframe wins, matching CapCut UI behaviour when a preset is
// applied to a keyframe. Returns false (leaving the entry as "Line") when the
// list has no neighbour to ease against: a lone keyframe encodes no curve, and
// stamping FreeCurveInOut with zero-length handles would write a curve-type
// marker its handles contradict. The stamp happens later instead, when the
// pair's second keyframe is inserted with an easing and retro-updates this one.
function applyEasing(list, entry, easing) {
    const profile = EASING_PROFILES[easing];
    const rightY = (fromValue, toValue) => easing === "ease-out" ? Math.round(EASE_OUT_RIGHT_Y_RATIO * (toValue - fromValue) * 1e6) / 1e6 : 0;
    const { prev, next } = findNeighbours(list, entry.time_offset);
    if (!prev && !next)
        return false;
    entry.curveType = "FreeCurveInOut";
    if (prev) {
        const interval = entry.time_offset - prev.time_offset;
        prev.curveType = "FreeCurveInOut";
        prev.right_control = {
            x: Math.round(profile.startRightXRatio * interval),
            y: rightY(prev.values[0], entry.values[0]),
        };
        entry.left_control = { x: Math.round(profile.endLeftXRatio * interval), y: 0 };
    }
    if (next) {
        const interval = next.time_offset - entry.time_offset;
        next.curveType = "FreeCurveInOut";
        next.left_control = { x: Math.round(profile.endLeftXRatio * interval), y: 0 };
        entry.right_control = {
            x: Math.round(profile.startRightXRatio * interval),
            y: rightY(entry.values[0], next.values[0]),
        };
    }
    return true;
}
/**
 * Emulate `hold` after every requested keyframe is in place (so a batch may
 * name the held keyframe before the one it holds until): for each held entry,
 * insert a helper keyframe carrying the same value one frame before the next
 * keyframe on that property. The app then interpolates the whole change inside
 * one frame — a step, as far as any viewer can tell. A hold with no later
 * keyframe, or one whose neighbour is already within a frame, has nothing to
 * hold until and is reported as a warning instead of inventing a keyframe.
 */
function applyHolds(holds, fps, warnings) {
    const frameUs = Math.round(1_000_000 / fps);
    let inserted = 0;
    for (const { list, entry, property } of holds) {
        const { next } = findNeighbours(list.keyframe_list, entry.time_offset);
        if (!next) {
            warnings.push(`hold for ${property} at ${entry.time_offset}us has no later keyframe to hold until; wrote a plain keyframe`);
            continue;
        }
        // A next keyframe that already carries the held value IS the hold (this is
        // also what a previous run's helper looks like) — nothing to add.
        if (next.values.length === entry.values.length && next.values.every((v, k) => v === entry.values[k]))
            continue;
        const holdAt = next.time_offset - frameUs;
        if (holdAt <= entry.time_offset) {
            warnings.push(`hold for ${property} at ${entry.time_offset}us: the next keyframe is within one frame, nothing to hold`);
            continue;
        }
        if (list.keyframe_list.some((k) => k.time_offset === holdAt))
            continue; // already stepped
        list.keyframe_list.push({
            curveType: "Line",
            graphID: "",
            left_control: { x: 0.0, y: 0.0 },
            right_control: { x: 0.0, y: 0.0 },
            id: uuidHex(),
            time_offset: holdAt,
            values: [...entry.values],
        });
        list.keyframe_list.sort((a, b) => a.time_offset - b.time_offset);
        inserted++;
    }
    return inserted;
}
export function addKeyframes(draft, segmentId, keyframes, easing) {
    resolveEasing(easing); // fail fast even when every keyframe overrides it
    const found = requireSegment(draft, segmentId);
    const seg = found.segment;
    if (!Array.isArray(seg.common_keyframes)) {
        seg.common_keyframes = [];
    }
    const commonKeyframes = seg.common_keyframes;
    const warnings = [];
    const holds = [];
    for (const kf of keyframes) {
        const property = resolveKeyframeProperty(kf.property);
        const propEnum = PROPERTY_MAP[property];
        const kfEasing = resolveEasing(kf.easing ?? easing);
        let list = commonKeyframes.find((l) => l.property_type === propEnum);
        if (!list) {
            list = {
                id: uuidHex(),
                keyframe_list: [],
                material_id: "",
                property_type: propEnum,
            };
            commonKeyframes.push(list);
        }
        const entry = {
            curveType: "Line",
            graphID: "",
            left_control: { x: 0.0, y: 0.0 },
            right_control: { x: 0.0, y: 0.0 },
            id: uuidHex(),
            time_offset: kf.timeUs,
            values: [kf.value],
        };
        if (kfEasing === "hold") {
            // A held keyframe is a plain linear one; the step is added by applyHolds
            // once every keyframe of this call is in place.
            clearFacingHandles(list.keyframe_list, entry.time_offset);
            holds.push({ list, entry, property });
        }
        else if (kfEasing !== "linear") {
            if (!applyEasing(list.keyframe_list, entry, kfEasing)) {
                warnings.push(`easing '${kfEasing}' for ${kf.property} at ${kf.timeUs}us has no adjacent keyframe to ease against; ` +
                    `wrote a linear keyframe — it picks up the curve when its pair keyframe is added with an easing`);
            }
        }
        else {
            clearFacingHandles(list.keyframe_list, entry.time_offset);
        }
        list.keyframe_list.push(entry);
        list.keyframe_list.sort((a, b) => a.time_offset - b.time_offset);
    }
    const fps = typeof draft.fps === "number" && Number.isFinite(draft.fps) && draft.fps > 0 ? draft.fps : 30;
    const holdKeyframes = holds.length > 0 ? applyHolds(holds, fps, warnings) : 0;
    const summary = commonKeyframes.map((l) => {
        const prop = Object.entries(PROPERTY_MAP).find(([, v]) => v === l.property_type)?.[0] ?? l.property_type;
        return { property: prop, count: l.keyframe_list.length };
    });
    return { segmentId: seg.id, added: keyframes.length, lists: summary, warnings, holdKeyframes };
}
// --- Phase 1: transition / mask / bg-blur / text-style / text-anim ---
// Phase 3: TRANSITIONS / MASKS / TEXT_ANIMS now source metadata from enums.json
// (enums.ts). IMAGE_ANIMS and VIDEO_EFFECTS keep inline maps because those
// catalogues carry empirically-verified effect_ids from live CapCut projects
// (knossos-recon) that do NOT match upstream pyJianYingDraft metadata.
export function addTransition(draft, segmentId, slug, durationUs, namespace = "capcut") {
    const meta = findEnum("transitions", slug, namespace);
    if (!meta?.name || !meta.effect_id || !meta.resource_id) {
        const hint = namespace === "jianying" ? " --jianying" : "";
        throw new Error(`Unknown transition: ${slug}. Run 'capcut enums --transitions${hint}' for the full list.`);
    }
    const found = requireSegment(draft, segmentId);
    const seg = found.segment;
    draft.materials.transitions ??= [];
    const transitions = draft.materials.transitions;
    // Refuse to stack transitions on one segment.
    const existing = (seg.extra_material_refs || []).find((r) => transitions.some((t) => t.id === r));
    if (existing)
        throw new Error(`Segment already has a transition (material ${existing}). Remove it first.`);
    const id = randomUUID();
    const dur = durationUs ?? meta.default_duration ?? 500000;
    transitions.push({
        category_id: "",
        category_name: "",
        duration: dur,
        effect_id: meta.effect_id,
        id,
        is_overlap: meta.is_overlap ?? false,
        name: meta.name,
        platform: "all",
        resource_id: meta.resource_id,
        type: "transition",
    });
    seg.extra_material_refs ||= [];
    seg.extra_material_refs.push(id);
    return { segmentId: seg.id, transition_id: id, name: meta.name, duration_us: dur };
}
// Legacy slug aliases: pre-Phase-3 the CLI exposed linear/mirror/star while
// CapCut's own names are split/filmstrip/stars. Keep aliasing so scripts don't break.
const MASK_ALIASES = {
    linear: "split",
    mirror: "filmstrip",
    star: "stars",
};
export function maskSlugs(namespace = "capcut") {
    return [...Object.keys(MASK_ALIASES), ...slugsFor("masks", namespace)];
}
/**
 * Which materials array a new mask should be written into. Three variants
 * exist: `masks` (legacy JianYing 5.9 era), `common_masks` (JianYing 9.6+ —
 * masks left in the legacy array silently no-op there, pyJianYingDraft#160),
 * and `common_mask` (the struct this CLI writes, verified against CapCut).
 * Selection: an explicit override wins; a versioned JianYing draft follows
 * its version evidence (a populated variant is NOT trusted there — it may be
 * exactly the #160 failure: a legacy-array mask an ecosystem tool wrote that
 * this app build does not read; lint flags that mismatch); otherwise an
 * already-populated variant wins (stay consistent with what is there,
 * newest-era array first), then the app-source default. Markerless
 * CLI-created drafts keep writing `common_mask`, byte-identical to before.
 */
export function maskTargetField(draft, override) {
    if (override)
        return override;
    const source = draft.platform?.app_source;
    const version = draft.platform?.app_version ?? null;
    if (source === "lv" && version)
        return jianyingMaskField(version);
    const populated = MASK_FIELDS.find((field) => maskEntries(draft, field).length > 0);
    if (populated)
        return populated;
    return source === "lv" ? "masks" : "common_mask";
}
export function addMask(draft, segmentId, slug, opts, namespace = "capcut") {
    const meta = findEnum("masks", slug, namespace, MASK_ALIASES);
    if (!meta?.name || !meta.effect_id || !meta.resource_id || !meta.resource_type) {
        const hint = namespace === "jianying" ? " --jianying" : "";
        throw new Error(`Unknown mask: ${slug}. Run 'capcut enums --masks${hint}' for the full list.`);
    }
    const found = requireSegment(draft, segmentId);
    const seg = found.segment;
    const field = maskTargetField(draft, opts.field);
    draft.materials[field] ??= [];
    const masks = draft.materials[field];
    // Refuse to stack masks on one segment — checked across every variant array,
    // not only the write target.
    const allMaskMaterials = MASK_FIELDS.flatMap((variant) => maskEntries(draft, variant));
    const existing = (seg.extra_material_refs || []).find((r) => allMaskMaterials.some((m) => m.id === r));
    if (existing)
        throw new Error(`Segment already has a mask (material ${existing}). Remove it first.`);
    const resolvedSlug = (MASK_ALIASES[slug] ?? slug).toLowerCase();
    const isRect = resolvedSlug === "rectangle";
    if ((opts.rectWidth !== undefined || opts.roundCorner !== undefined) && !isRect) {
        throw new Error(`--rect-width / --round-corner only valid for rectangle mask`);
    }
    const aspectRatio = meta.default_aspect_ratio ?? 1.0;
    const size = opts.size ?? 0.5;
    const canvasW = draft.canvas_config?.width ?? 1920;
    const canvasH = draft.canvas_config?.height ?? 1080;
    const width = isRect ? (opts.rectWidth ?? size) : (size * canvasH * aspectRatio) / canvasW;
    const height = size;
    const id = randomUUID();
    masks.push({
        config: {
            aspectRatio,
            centerX: opts.centerX ?? 0,
            centerY: opts.centerY ?? 0,
            feather: (opts.feather ?? 0) / 100,
            height,
            invert: opts.invert ?? false,
            rotation: opts.rotation ?? 0,
            roundCorner: (opts.roundCorner ?? 0) / 100,
            width,
        },
        category: "video",
        category_id: "",
        category_name: "",
        id,
        name: meta.name,
        platform: "all",
        position_info: "",
        resource_type: meta.resource_type,
        resource_id: meta.resource_id,
        type: "mask",
    });
    seg.extra_material_refs ||= [];
    seg.extra_material_refs.push(id);
    return { segmentId: seg.id, mask_id: id, name: meta.name, field };
}
// CapCut's four-step UI blur maps to these four values (upstream comment).
const BLUR_LEVELS = [0.0625, 0.375, 0.75, 1.0];
export function setBgBlur(draft, segmentId, level) {
    const found = requireSegment(draft, segmentId);
    const seg = found.segment;
    draft.materials.canvases ??= [];
    const canvases = draft.materials.canvases;
    // Remove any existing canvas reference on this segment.
    const before = (seg.extra_material_refs || []).length;
    seg.extra_material_refs = (seg.extra_material_refs || []).filter((r) => {
        const c = canvases.find((cc) => cc.id === r);
        return !c;
    });
    if (level === "off") {
        return { segmentId: seg.id, canvas_id: null, blur: null };
    }
    const idx = level - 1;
    const blur = BLUR_LEVELS[idx];
    const id = randomUUID();
    canvases.push({
        album_image: "",
        blur,
        color: "",
        id,
        image: "",
        image_id: "",
        image_name: "",
        source_platform: 0,
        team_id: "",
        type: "canvas_blur",
    });
    seg.extra_material_refs.push(id);
    void before; // kept to silence unused if tooling lint turns back on
    return { segmentId: seg.id, canvas_id: id, blur };
}
/**
 * Resolve the text material behind a segment — the opening move of every
 * text-editing command. Pass `label` to additionally require a text *track*;
 * it names the command in that error ("bubble-text only applies to ...").
 * Commands that happily edit a text material on any track omit it.
 */
export function requireTextMaterial(draft, segmentId, label) {
    const found = requireSegment(draft, segmentId);
    if (label !== undefined && found.track.type !== "text") {
        throw new Error(`${label} only applies to text segments (track type: ${found.track.type})`);
    }
    const seg = found.segment;
    const text = findMaterial(draft.materials.texts, seg.material_id);
    if (!text)
        throw new Error(`Text material not found for segment ${segmentId}`);
    return { seg, text };
}
export function setTextStyle(draft, segmentId, opts) {
    const { text } = requireTextMaterial(draft, segmentId);
    const applied = [];
    const t = text;
    if (opts.color !== undefined && !/^#[0-9a-fA-F]{6}$/.test(opts.color)) {
        throw new Error(`--color must be #RRGGBB, got: ${opts.color}`);
    }
    if (opts.fontSize !== undefined || opts.color !== undefined) {
        if (opts.fontSize !== undefined) {
            t.font_size = opts.fontSize;
            applied.push("font_size");
        }
        if (opts.color !== undefined) {
            t.text_color = opts.color;
            applied.push("color");
        }
        // CapCut renders from `content`'s style blocks, not the material fields, and
        // these flags cover the whole text, so stamp every block.
        let content = null;
        try {
            content = JSON.parse(text.content);
        }
        catch {
            content = null;
        }
        if (content?.styles) {
            for (const block of content.styles) {
                if (opts.fontSize !== undefined)
                    block.size = opts.fontSize;
                if (opts.color !== undefined) {
                    block.fill = solidFill(hexToRgb01(opts.color));
                }
            }
            text.content = JSON.stringify(content);
        }
    }
    if (opts.alignment !== undefined) {
        t.alignment = opts.alignment;
        applied.push("alignment");
    }
    if (opts.alpha !== undefined) {
        t.text_alpha = opts.alpha;
        applied.push("alpha");
    }
    if (opts.vertical !== undefined) {
        t.typesetting = opts.vertical ? 1 : 0;
        applied.push("vertical");
    }
    if (opts.fixedWidth !== undefined) {
        t.fixed_width = opts.fixedWidth;
        applied.push("fixed_width");
    }
    if (opts.fixedHeight !== undefined) {
        t.fixed_height = opts.fixedHeight;
        applied.push("fixed_height");
    }
    if (opts.shadow) {
        t.has_shadow = true;
        if (opts.shadowAlpha !== undefined)
            t.shadow_alpha = opts.shadowAlpha;
        if (opts.shadowAngle !== undefined)
            t.shadow_angle = opts.shadowAngle;
        if (opts.shadowColor !== undefined)
            t.shadow_color = opts.shadowColor;
        if (opts.shadowDistance !== undefined)
            t.shadow_distance = opts.shadowDistance;
        if (opts.shadowSmoothing !== undefined)
            t.shadow_smoothing = opts.shadowSmoothing;
        applied.push("shadow");
    }
    else if (opts.shadow === false) {
        t.has_shadow = false;
        applied.push("shadow-off");
    }
    if (opts.borderWidth !== undefined || opts.borderColor !== undefined || opts.borderAlpha !== undefined) {
        t.border_width = opts.borderWidth ?? t.border_width ?? 0;
        t.border_color = opts.borderColor ?? t.border_color ?? "#000000";
        t.border_alpha = opts.borderAlpha ?? t.border_alpha ?? 1;
        t.has_border = true;
        applied.push("border");
    }
    if (opts.bgColor !== undefined ||
        opts.bgAlpha !== undefined ||
        opts.bgStyle !== undefined ||
        opts.bgRoundRadius !== undefined ||
        opts.bgWidth !== undefined ||
        opts.bgHeight !== undefined ||
        opts.bgHOffset !== undefined ||
        opts.bgVOffset !== undefined) {
        t.has_text_shadow_config = true;
        if (opts.bgColor !== undefined)
            t.background_color = opts.bgColor;
        if (opts.bgAlpha !== undefined)
            t.background_alpha = opts.bgAlpha;
        if (opts.bgStyle !== undefined)
            t.background_style = opts.bgStyle;
        if (opts.bgRoundRadius !== undefined)
            t.background_round_radius = opts.bgRoundRadius;
        if (opts.bgWidth !== undefined)
            t.background_width = opts.bgWidth;
        if (opts.bgHeight !== undefined)
            t.background_height = opts.bgHeight;
        if (opts.bgHOffset !== undefined)
            t.background_horizontal_offset = opts.bgHOffset;
        if (opts.bgVOffset !== undefined)
            t.background_vertical_offset = opts.bgVOffset;
        applied.push("bg");
    }
    return { materialId: text.id, applied };
}
// Legacy aliases for text-anim slugs that don't match upstream enum member names.
const TEXT_ANIM_ALIASES = {
    "blur-text-in": "blur",
    "zoom-in-text": "zoom-in",
};
// Kept-small set of known slugs documented in SKILL.md — the full catalogue
// (70+ intros, 60+ outros) is discoverable via `capcut enums --text-intros/--text-outros`.
const TEXT_ANIM_FEATURED = ["fade-in", "fade-out", "typewriter", "pop-up", "throw-out", "blur-text-in", "zoom-in-text"];
export function textAnimSlugs() {
    return TEXT_ANIM_FEATURED;
}
const IMAGE_ANIMS = {
    "fade-in": {
        name: "Fade In",
        effect_id: "6798320778182922760",
        resource_id: "6798320778182922760",
        md5: "883ad04bd79b502aaa55b5d9b87175ea",
        default_duration_us: 500000,
        category_id: "2037708296",
        third_resource_id: "6798320778182922760",
        type: "in",
    },
    "flash-in": {
        name: "Flash In",
        effect_id: "7211044701367964162",
        resource_id: "7211044701367964162",
        md5: "6a680c49cd11a05f3eb0e5a3fed165f7",
        default_duration_us: 433333,
        category_id: "2037708312",
        third_resource_id: "7211044701367964162",
        type: "in",
    },
    "pulsing-zooms": {
        name: "Pulsing Zooms",
        effect_id: "7530463994486820097",
        resource_id: "7530463994486820097",
        md5: "c2223de4486ee5b2a5900d707e9a362b",
        default_duration_us: 3000000,
        category_id: "2037708296",
        third_resource_id: "0",
        type: "in",
    },
    "scroll-up": {
        name: "Scroll up",
        effect_id: "7315336764636271105",
        resource_id: "7315336764636271105",
        md5: "cb8899ed512a2d40bd27d9e03d039ec0",
        default_duration_us: 1200000,
        category_id: "in_fav",
        third_resource_id: "7315336764636271105",
        type: "in",
    },
    "stripe-merge": {
        name: "Stripe Merge",
        effect_id: "7570497406203251973",
        resource_id: "7570497406203251973",
        md5: "fc08875f779dae706387fb160dbaa898",
        default_duration_us: 633333,
        category_id: "2037708296",
        third_resource_id: "0",
        type: "in",
    },
    "zoom-out": {
        name: "Zoom Out",
        effect_id: "6798332584276267527",
        resource_id: "6798332584276267527",
        md5: "0c736f993d36a7b1ef00cc73d2ba656f",
        default_duration_us: 2000000,
        category_id: "",
        third_resource_id: "",
        type: "in",
    },
    "fade-out": {
        name: "Fade Out",
        effect_id: "6798320902548230669",
        resource_id: "6798320902548230669",
        md5: "c6f05ce62355b537be762550040bfc08",
        default_duration_us: 500000,
        category_id: "2037708296",
        third_resource_id: "0",
        type: "out",
    },
    "blur-out": {
        name: "Blur Out",
        effect_id: "7507514531212479761",
        resource_id: "7507514531212479761",
        md5: "78d0826a4aba60259f37acb30149b258",
        default_duration_us: 1000000,
        category_id: "out_fav",
        third_resource_id: "0",
        type: "out",
    },
    smoke: {
        name: "Smoke",
        effect_id: "7229983825080619522",
        resource_id: "7229983825080619522",
        md5: "e70e26e7aa770d0deedca54e3eac0323",
        default_duration_us: 900000,
        category_id: "out_fav",
        third_resource_id: "7229983825080619522",
        type: "out",
    },
};
export function imageAnimSlugs() {
    return Object.keys(IMAGE_ANIMS);
}
// Exposed so lint's unknown-effect-slug check recognises the inline starter
// catalogue: these effect_ids are knossos-verified but absent from enums.json.
export function imageAnimCatalogue() {
    return Object.entries(IMAGE_ANIMS).map(([slug, meta]) => ({
        slug,
        member: meta.name,
        name: meta.name,
        effect_id: meta.effect_id,
        resource_id: meta.resource_id,
    }));
}
// Find or create the per-segment sticker_animation container. Text and image
// animations share it: the first extra_material_ref that resolves into
// materials.material_animations wins, otherwise a fresh container is appended
// and linked. Key order is load-bearing — drafts are compared byte-for-byte.
function ensureAnimContainer(draft, seg) {
    draft.materials.material_animations ??= [];
    const animsArr = draft.materials.material_animations;
    const animsById = Object.fromEntries(animsArr.map((a) => [a.id, a]));
    for (const ref of seg.extra_material_refs || []) {
        const m = animsById[ref];
        if (m)
            return m;
    }
    const id = randomUUID();
    const fresh = {
        animations: [],
        id,
        multi_language_current: "none",
        type: "sticker_animation",
    };
    animsArr.push(fresh);
    seg.extra_material_refs ||= [];
    seg.extra_material_refs.push(id);
    return fresh;
}
/**
 * Put one animation into a segment's animation container. Each slot type
 * (in / out / group) holds one animation per segment, an outro is anchored to
 * the segment end, and the duration must fit the segment. The entry carries
 * the fixed fields every CapCut material_animations entry has; the caller
 * supplies what differs between video and text animations.
 */
function placeAnimation(container, targetDur, anim) {
    if (container.animations.some((a) => a.type === anim.type)) {
        throw new Error(`segment already has a ${anim.type} ${anim.materialType} animation`);
    }
    const dur = anim.durationUs;
    if (dur > targetDur)
        throw new Error(`duration (${dur}us) exceeds segment duration (${targetDur}us)`);
    const start = anim.type === "out" ? targetDur - dur : 0;
    container.animations.push({
        anim_adjust_params: null,
        category_id: anim.categoryId,
        category_name: anim.categoryId,
        duration: dur,
        id: anim.effectId,
        material_type: anim.materialType,
        name: anim.name,
        panel: anim.panel,
        path: anim.path,
        platform: "all",
        request_id: "",
        resource_id: anim.resourceId,
        source_platform: 1,
        start,
        third_resource_id: anim.thirdResourceId,
        type: anim.type,
    });
    return { type: anim.type, name: anim.name, duration_us: dur, start_us: start };
}
export function addImageAnim(draft, segmentId, opts, namespace = "capcut") {
    if (!opts.intro && !opts.outro && !opts.combo) {
        throw new Error("at least one of --intro, --outro, --combo is required");
    }
    const found = requireSegment(draft, segmentId);
    const seg = found.segment;
    const container = ensureAnimContainer(draft, seg);
    const added = [];
    const targetDur = seg.target_timerange.duration;
    const CACHE_BASE = `${process.env.HOME ?? ""}/Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut/User Data/Cache/effect`;
    const addOne = (slug, overrideDur, animType) => {
        const inline = IMAGE_ANIMS[slug];
        // Inline (knossos-verified) entry wins; fall back to enums.json for any
        // other slug so the full CapCut catalogue is reachable.
        let name;
        let effectId;
        let resourceId;
        let md5;
        let defaultDur;
        let categoryId;
        let thirdResourceId;
        if (inline) {
            name = inline.name;
            effectId = inline.effect_id;
            resourceId = inline.resource_id;
            md5 = inline.md5;
            defaultDur = inline.default_duration_us;
            categoryId = inline.category_id;
            thirdResourceId = inline.third_resource_id;
        }
        else {
            const slot = ANIM_SLOTS[animType];
            const meta = findEnum(slot.image, slug, namespace);
            if (!meta?.effect_id || !meta.resource_id) {
                const hint = namespace === "jianying" ? " --jianying" : "";
                throw new Error(`Unknown image ${animType} animation: ${slug}. Run 'capcut enums --image-${slot.listWord}${hint}' for the full list.`);
            }
            name = meta.title ?? meta.name ?? slug;
            effectId = meta.effect_id;
            resourceId = meta.resource_id;
            md5 = meta.md5 ?? "";
            defaultDur = meta.duration ?? meta.default_duration ?? 500000;
            categoryId = slot.favCategory;
            thirdResourceId = "0";
        }
        added.push(placeAnimation(container, targetDur, {
            type: animType,
            materialType: "video",
            durationUs: overrideDur ?? defaultDur,
            name,
            effectId,
            resourceId,
            categoryId,
            panel: "video",
            path: md5 ? `${CACHE_BASE}/${effectId}/${md5}` : "",
            thirdResourceId,
        }));
    };
    if (opts.intro)
        addOne(opts.intro, opts.introDurationUs, "in");
    if (opts.outro)
        addOne(opts.outro, opts.outroDurationUs, "out");
    if (opts.combo)
        addOne(opts.combo, opts.comboDurationUs, "group");
    return { segmentId: seg.id, added, material_id: container.id };
}
/**
 * Per animation slot: the catalogue it is looked up in (image and text
 * catalogues are separate; text has no combo slot), the word `capcut enums`
 * lists it under, and the favourites category CapCut files a catalogue pick in.
 */
const ANIM_SLOTS = {
    in: { image: "image_intros", text: "text_intros", listWord: "intros", label: "intro", favCategory: "in_fav" },
    out: { image: "image_outros", text: "text_outros", listWord: "outros", label: "outro", favCategory: "out_fav" },
    group: { image: "image_combos", listWord: "combos", favCategory: "" },
};
export function addTextAnim(draft, segmentId, opts, namespace = "capcut") {
    if (!opts.intro && !opts.outro)
        throw new Error("at least one of --intro or --outro is required");
    const found = requireSegment(draft, segmentId);
    const seg = found.segment;
    const container = ensureAnimContainer(draft, seg);
    const added = [];
    const targetDur = seg.target_timerange.duration;
    const addOne = (slug, overrideDur, animType) => {
        const slot = ANIM_SLOTS[animType];
        const meta = findEnum(slot.text, slug, namespace, TEXT_ANIM_ALIASES);
        if (!meta?.effect_id || !meta.resource_id) {
            const hint = namespace === "jianying" ? " --jianying" : "";
            throw new Error(`Unknown text ${slot.label}: ${slug}. Run 'capcut enums --text-${slot.listWord}${hint}' for the full list.`);
        }
        added.push(placeAnimation(container, targetDur, {
            type: animType,
            materialType: "text",
            durationUs: overrideDur ?? meta.duration ?? meta.default_duration ?? 500000,
            name: meta.title ?? meta.name ?? slug,
            effectId: meta.effect_id,
            resourceId: meta.resource_id,
            categoryId: slot.favCategory,
            panel: "",
            path: "",
            thirdResourceId: "",
        }));
    };
    if (opts.intro)
        addOne(opts.intro, opts.introDurationUs, "in");
    if (opts.outro)
        addOne(opts.outro, opts.outroDurationUs, "out");
    return { segmentId: seg.id, added, material_id: container.id };
}
// --- Text colour as CapCut stores it ---
// A style block's colour is three channels in 0..1 inside a solid fill; the CLI
// speaks "#RRGGBB". These three are the only places that know either encoding.
export function hexToRgb01(hex) {
    const h = hex.replace("#", "");
    return [parseInt(h.slice(0, 2), 16) / 255, parseInt(h.slice(2, 4), 16) / 255, parseInt(h.slice(4, 6), 16) / 255];
}
/** Stored 0..1 channels back to "#RRGGBB" (upper case), each channel clamped to a byte. */
export function rgb01ToHex(rgb) {
    const byte = (c) => Math.max(0, Math.min(255, Math.round(c * 255)))
        .toString(16)
        .padStart(2, "0")
        .toUpperCase();
    return `#${byte(rgb[0])}${byte(rgb[1])}${byte(rgb[2])}`;
}
/** The fill a style block stores for one solid colour (0..1 channels) at `alpha`. */
export function solidFill(color, alpha = 1) {
    return { alpha: 1, content: { render_type: "solid", solid: { alpha, color } } };
}
/** The solid colour and alpha of a stored fill, when it has one. */
export function fillSolid(fill) {
    return fill?.content
        ?.solid;
}
export function setTextRanges(draft, segmentId, ranges) {
    if (ranges.length === 0)
        throw new Error("at least one range required");
    const { seg, text } = requireTextMaterial(draft, segmentId);
    const content = JSON.parse(text.content);
    const full = content.text ?? "";
    const fullLen = storedTextLength(full);
    // Baseline style inherited for fields the user didn't override.
    const base = content.styles?.[0] ?? {};
    const baseFill = fillSolid(base.fill);
    const defaultColor = baseFill?.color ?? [1, 1, 1];
    const defaultAlpha = baseFill?.alpha ?? 1;
    const defaultSize = base.size ?? 15;
    const defaultBold = base.bold ?? false;
    const defaultItalic = base.italic ?? false;
    const defaultUnderline = base.underline ?? false;
    // Validate the caller's ranges. Both sides of the conversion are code-unit
    // indices, so this needs no assumption about which plane the characters are
    // in — an emoji is two code units to CapCut exactly as it is to JS.
    const sorted = [...ranges].sort((a, b) => a.start - b.start);
    const maxCodeUnits = full.length;
    for (const r of sorted) {
        if (!Number.isInteger(r.start) || !Number.isInteger(r.end))
            throw new Error(`range {start,end} must be integers`);
        if (r.start < 0 || r.end > maxCodeUnits)
            throw new Error(`range [${r.start},${r.end}) out of bounds (text length=${maxCodeUnits})`);
        if (r.end <= r.start)
            throw new Error(`range [${r.start},${r.end}) must have end > start`);
    }
    for (let i = 1; i < sorted.length; i++) {
        if (sorted[i].start < sorted[i - 1].end) {
            throw new Error(`overlapping ranges: [${sorted[i - 1].start},${sorted[i - 1].end}) and [${sorted[i].start},${sorted[i].end})`);
        }
    }
    const toStored = (codeUnitIdx) => toStoredOffset(full, codeUnitIdx);
    const makeStyle = (start, end, r) => {
        const color = r?.font_color ? hexToRgb01(r.font_color) : defaultColor;
        const alpha = r?.font_alpha ?? defaultAlpha;
        return {
            range: [start, end],
            size: r?.font_size ?? defaultSize,
            bold: r?.bold ?? defaultBold,
            italic: r?.italic ?? defaultItalic,
            underline: r?.underline ?? defaultUnderline,
            fill: solidFill(color, alpha),
        };
    };
    // Fill gaps with a plain block (default style) so CapCut renders the whole text.
    const styles = [];
    let cursor = 0;
    for (const r of sorted) {
        if (r.start > cursor) {
            styles.push(makeStyle(toStored(cursor), toStored(r.start)));
        }
        styles.push(makeStyle(toStored(r.start), toStored(r.end), r));
        cursor = r.end;
    }
    if (cursor < maxCodeUnits) {
        styles.push(makeStyle(toStored(cursor), fullLen));
    }
    content.styles = styles;
    text.content = JSON.stringify(content);
    return { segmentId: seg.id, material_id: text.id, styles: styles.length, text_length: maxCodeUnits };
}
// --- v0.14: per-word keyword emphasis + colour cycling ---
// Both `caption` and `import-srt` accept --highlight-words / --keyword-color /
// --keyword-size / --color-cycle. The offset scheme is the one setTextRanges
// already defines: JS string code-unit indices in, code units in the draft.
// No second offset scheme — findKeywordRanges only ever produces code-unit
// indices that feed straight into TextRangeInput.
/** The gold `caption --karaoke` paints the active word with; --keyword-color defaults to it. */
export const KARAOKE_HIGHLIGHT_COLOR = "#FFD700";
/** Default --keyword-size multiplier when --highlight-words is present. */
export const DEFAULT_KEYWORD_SIZE = 1.2;
/**
 * Case-insensitive whole-word matches of `words` (words or phrases) in `text`.
 * Offsets are JS code-unit indices — exactly what TextRangeInput.start/end
 * take. "Whole word" is Unicode-aware: a match may not butt against a letter
 * or digit, so `für` matches inside `Grüße für alle` but `cap` never matches
 * inside `capcut`. Overlapping matches (e.g. "New York" + "York") keep the
 * earlier one, matching setTextRanges' no-overlap contract.
 */
export function findKeywordRanges(text, words) {
    const matches = [];
    for (const word of words) {
        const needle = word.trim();
        if (!needle)
            continue;
        const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const re = new RegExp(`(?<![\\p{L}\\p{N}])${escaped}(?![\\p{L}\\p{N}])`, "giu");
        for (const m of text.matchAll(re)) {
            matches.push({ start: m.index, end: m.index + m[0].length });
        }
    }
    matches.sort((a, b) => a.start - b.start || a.end - b.end);
    const kept = [];
    for (const m of matches) {
        const last = kept[kept.length - 1];
        if (last && m.start < last.end)
            continue;
        kept.push(m);
    }
    return kept;
}
/**
 * Build the non-overlapping range list for one cue: keyword emphasis ranges,
 * any preset (karaoke) ranges that no keyword overlaps, and — when baseColor
 * is given — explicit base-styled gap ranges covering the rest of the text.
 * Follows the v0.13 "explicit flags beat preset ranges" contract: a keyword
 * match overrides the overlapped preset word's colour/size but inherits its
 * bold/italic/underline. Returns zero ranges when there is nothing to style.
 */
export function buildEmphasisRanges(text, opts) {
    const keywordRanges = findKeywordRanges(text, opts.words).map((m) => ({
        start: m.start,
        end: m.end,
        font_color: opts.color,
        font_size: opts.baseSize * opts.sizeMultiplier,
    }));
    const merged = [...keywordRanges];
    for (const preset of opts.presetRanges ?? []) {
        const overlapping = keywordRanges.filter((k) => k.start < preset.end && preset.start < k.end);
        if (overlapping.length === 0) {
            merged.push(preset);
            continue;
        }
        for (const k of overlapping) {
            if (k.bold === undefined)
                k.bold = preset.bold;
            if (k.italic === undefined)
                k.italic = preset.italic;
            if (k.underline === undefined)
                k.underline = preset.underline;
        }
    }
    merged.sort((a, b) => a.start - b.start);
    if (merged.length === 0 || opts.baseColor === undefined)
        return { ranges: merged, matches: keywordRanges.length };
    const baseProps = {
        font_color: opts.baseColor,
        font_size: opts.baseSize,
        ...(opts.baseBold === undefined ? {} : { bold: opts.baseBold }),
    };
    const covered = [];
    let cursor = 0;
    for (const r of merged) {
        if (r.start > cursor)
            covered.push({ start: cursor, end: r.start, ...baseProps });
        covered.push(r);
        cursor = r.end;
    }
    if (cursor < text.length)
        covered.push({ start: cursor, end: text.length, ...baseProps });
    return { ranges: covered, matches: keywordRanges.length };
}
const BUBBLES = {
    // Starter catalogue. The exact effect_id / resource_id pairs are CapCut-version-
    // specific; the slugs below are common bubble shapes. Override via
    // --effect-id / --resource-id when you have ids from your own draft.
    rectangle: { name: "Rectangle", effect_id: "7137268628230638087", resource_id: "7137268628230638087" },
    rounded: { name: "Rounded Rectangle", effect_id: "7137268898998568967", resource_id: "7137268898998568967" },
    cloud: { name: "Cloud", effect_id: "7137269184932778510", resource_id: "7137269184932778510" },
    oval: { name: "Oval", effect_id: "7137269466232116231", resource_id: "7137269466232116231" },
    star: { name: "Star", effect_id: "7137269743886750214", resource_id: "7137269743886750214" },
    heart: { name: "Heart", effect_id: "7137270031716044302", resource_id: "7137270031716044302" },
    burst: { name: "Burst", effect_id: "7137270320304885262", resource_id: "7137270320304885262" },
};
export function bubbleSlugs() {
    return Object.keys(BUBBLES);
}
export function bubbleCatalogue() {
    return Object.entries(BUBBLES).map(([slug, meta]) => ({
        slug,
        member: meta.name,
        name: meta.name,
        effect_id: meta.effect_id,
        resource_id: meta.resource_id,
    }));
}
export function setBubble(draft, segmentId, opts) {
    const { seg, text } = requireTextMaterial(draft, segmentId, "bubble-text");
    let effectId = opts.effectId;
    let resourceId = opts.resourceId;
    if ((!effectId || !resourceId) && opts.slug) {
        const meta = BUBBLES[opts.slug.toLowerCase()];
        if (!meta) {
            throw new Error(`Unknown bubble slug: ${opts.slug}. Run 'capcut enums --bubbles' or pass --effect-id / --resource-id directly.`);
        }
        effectId = effectId ?? meta.effect_id;
        resourceId = resourceId ?? meta.resource_id;
    }
    if (!effectId || !resourceId) {
        throw new Error(`bubble-text requires either --bubble <slug> or both --effect-id and --resource-id`);
    }
    // Drop existing bubble refs on this segment (we replace, don't stack).
    if (!Array.isArray(draft.materials.filters)) {
        draft.materials.filters = [];
    }
    const filters = draft.materials.filters;
    seg.extra_material_refs = (seg.extra_material_refs || []).filter((r) => !filters.some((f) => f.id === r && f.type === "text_shape"));
    const bubbleId = randomUUID().replace(/-/g, "");
    filters.push({
        id: bubbleId,
        apply_target_type: 0,
        effect_id: effectId,
        resource_id: resourceId,
        type: "text_shape",
        value: 1.0,
    });
    seg.extra_material_refs ||= [];
    seg.extra_material_refs.push(bubbleId);
    // Also stamp the bubble_* fields on the text material itself (some CapCut
    // versions read from there in addition to the filters[] entry).
    const t = text;
    t.bubble_effect_id = effectId;
    t.bubble_resource_id = resourceId;
    return { segmentId: seg.id, bubble_id: bubbleId, effect_id: effectId, resource_id: resourceId };
}
