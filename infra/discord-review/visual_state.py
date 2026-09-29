"""LOOM resolved visual state -> CapCut composition (the CONSUMER half).

This is the narrow module ``capcut_handoff`` has always referred to. It READS
the resolved visual state LOOM's renderer persisted beside a clip's output
(``openshorts/visual_state.py`` is the producer half) and turns it into the
composition a CapCut project needs. It is deliberately not a second reframing
engine: it never re-derives a layout, never runs a detector, never looks at a
pixel. Every number it uses was resolved by the render that produced the asset.

Two things it refuses to do, because both are ways to hand the operator a draft
that silently shows the wrong picture:

* **Trust stale geometry.** The block is tied to the output it describes
  (filename + sha256) and optionally a LOOM asset version. If the asset being
  handed over is not the asset the block was written for, the block is not
  authoritative — ``assess()`` says so and the caller must obtain real state.
* **Flatten a composition it cannot express.** A dynamic TRACK crop is a moving
  window, not a crop; a general composition is a layered bed + foreground, not a
  crop. Where the pinned CapCut lane genuinely cannot carry one of those, this
  raises ``UnrepresentableVisualState`` naming the exact representation instead
  of quietly downgrading it to something that would open and look wrong.

MAPPING (resolved scene -> CapCut)

  canvas            -> project/canvas width, height, ratio, fps (persisted truth)
  crop_window       -> item uniform scale + centred position, and for a moving
                       window, keyframed position_x (and uniform_scale when a
                       punch-in changes the window size), stepped like the
                       render's own sendcmd holds
  general           -> item scaled so the foreground fills the persisted
                       content height, centred, with the render's blurred cover
                       bed carried as the lane's own background-blur operation
  passthrough       -> item at native framing (no crop, no scale)
  split/screencast/ -> REFUSED (see above)
  camera_inset

TRANSFORM BASIS: CapCut's ``clip.transform`` is the donor's documented
``-1 .. 1`` position space — 0 is the canvas centre and 1 is the edge measured
toward. The renderer works in SOURCE pixels, so a window centre is converted to
canvas pixels, then normalised against the half-canvas. The persisted block
records this basis (``coordinate_basis.position``) so a future renderer change
cannot make these numbers mean something else silently.
"""

from __future__ import annotations

import json
import os
from typing import Any

SCHEMA = "loom.visual_state/1"
RESOLVED = "resolved"

#: The sidecar the renderer writes beside a clip's output, and the ADDITIVE key
#: inside it that holds the resolved block. Both are part of the JSON contract
#: (see ``openshorts/layout_ranges.py``), restated here so this side of the seam
#: needs no import from the renderer's tree.
SIDECAR_SUFFIX = ".layout.json"
VISUAL_STATE_KEY = "visual_state"

#: The persisted position basis this module's maths assumes.
POSITION_BASIS = "canvas_centre_to_edge"

#: Vertical/horizontal tolerance when checking that a crop window's aspect
#: matches the canvas. A window that does not fill the canvas under a uniform
#: scale cannot be represented as scale+position, and pretending otherwise
#: stretches the picture.
ASPECT_TOLERANCE = 0.02

#: The lane's own background-blur level used for a general composition's cover
#: bed. CapCut exposes 1-4 and the render applies a gaussian (sigma recorded in
#: the state); level 2 is the mid-point of the lane's own scale. Recorded in the
#: handoff report next to the sigma so the mapping is reviewable rather than
#: implied.
BACKGROUND_BLUR_LEVEL = 2

#: Strategies this module can carry faithfully, and the representation each one
#: needs. Anything else is refused by name.
SUPPORTED_STRATEGIES = ("TRACK", "ALTERNATE", "GENERAL", "WIDE", "PASSTHROUGH")


class UnrepresentableVisualState(RuntimeError):
    """The persisted composition cannot be carried faithfully by the lane."""


class MissingVisualState(RuntimeError):
    """No authoritative resolved state is available for this asset."""


# ---------------------------------------------------------------------------
# Reading and ownership
# ---------------------------------------------------------------------------
def is_resolved(state: Any) -> bool:
    """Whether a block is a RESOLVED composition from a render.

    An unresolved (carried-forward) block describes geometry for a file that no
    longer exists, so it is never treated as authoritative — that is the whole
    difference between "we know the composition" and "we know an older one".
    """
    return (isinstance(state, dict) and state.get("schema") == SCHEMA
            and state.get("resolution") == RESOLVED)


def digest_of(path: str) -> str:
    """sha256 of a file's bytes — the identity a resolved block is tied to."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(media_path: str) -> dict[str, Any] | None:
    """The resolved block persisted beside this media, or None.

    The sidecar is a JSON CONTRACT between the renderer's tree and this one, so
    it is read as data rather than by importing the producer's module: the
    CapCut lane must work in a process where the renderer's package is not on
    the path (that is the normal deployment), and a missing reader must never be
    mistaken for "no state".

    Read-only and side-effect free. None means LOOM's renderer recorded nothing
    here — an older asset, a v1 render, or a file that never went through the
    renderer — which is a different situation from a block that exists but does
    not own this asset (see ``assess``).
    """
    if not media_path:
        return None
    try:
        with open(media_path + SIDECAR_SUFFIX, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    state = payload.get(VISUAL_STATE_KEY)
    if not isinstance(state, dict) or state.get("schema") != SCHEMA:
        return None
    return state


def assess(state: dict[str, Any] | None, *, media_path: str = "",
           digest: str = "", asset_version: str = "") -> dict[str, Any]:
    """Whether a block is authoritative for the asset about to be handed over.

    Returns ``{"usable": bool, "reason": str, ...}``. A block that cannot be
    USED is reported with the exact reason, never silently ignored: the caller
    decides between resolving the composition properly and failing closed.
    """
    if not isinstance(state, dict) or state.get("schema") != SCHEMA:
        return {"usable": False, "reason": "no_resolved_state"}
    if state.get("resolution") != RESOLVED:
        return {"usable": False,
                "reason": state.get("unresolved_reason") or "unresolved_state"}
    own = state.get("ownership") or {}
    recorded = str(own.get("sha256") or "")
    if not recorded:
        return {"usable": False, "reason": "no_recorded_identity"}
    actual = digest or (digest_of(media_path) if media_path and os.path.isfile(media_path) else "")
    if not actual:
        return {"usable": False, "reason": "no_asset_identity"}
    if actual != recorded:
        return {"usable": False, "reason": "asset_identity_mismatch"}
    recorded_version = str(own.get("asset_version") or "")
    current_version = str(asset_version or "")
    if recorded_version and current_version and recorded_version != current_version:
        return {"usable": False, "reason": "asset_version_mismatch"}
    return {"usable": True, "reason": "ok"}


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------
def canvas(state: dict[str, Any]) -> dict[str, Any] | None:
    """The persisted canvas, or None when the block does not state one.

    Persisted truth only — this never substitutes a default, because a default
    here is exactly how an operator's chosen format gets silently replaced.
    """
    block = state.get("canvas") if isinstance(state, dict) else None
    if not isinstance(block, dict):
        return None
    try:
        width, height = int(block["width"]), int(block["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    out: dict[str, Any] = {"width": width, "height": height,
                           "ratio": str(block.get("ratio") or ""),
                           "format": str(block.get("format") or "")}
    try:
        fps = float(block.get("fps"))
    except (TypeError, ValueError):
        fps = 0.0
    out["fps"] = int(round(fps)) if fps > 0 else None
    return out


# ---------------------------------------------------------------------------
# Composition -> CapCut item fields
# ---------------------------------------------------------------------------
def _position_unit(offset_px: float, canvas_extent: int) -> float:
    """Canvas pixels -> the lane's normalized position space (0 centre, 1 edge)."""
    half = float(canvas_extent) / 2.0
    if half <= 0:
        raise UnrepresentableVisualState("canvas has no extent to position within")
    return round(offset_px / half, 6)


def _crop_window_fields(window: dict[str, Any], *, canvas: dict[str, Any],
                        source: dict[str, Any]) -> dict[str, Any]:
    """Item scale + position for one SOURCE-pixel crop window filling the canvas."""
    try:
        x, w, h = (float(window["x"]), float(window["width"]),
                   float(window["height"]))
    except (KeyError, TypeError, ValueError):
        raise UnrepresentableVisualState(
            "crop window is missing x/width/height") from None
    y = float(window.get("y") or 0.0)
    sw, sh = float(source.get("width") or 0), float(source.get("height") or 0)
    if w <= 0 or h <= 0 or sw <= 0 or sh <= 0:
        raise UnrepresentableVisualState("crop window has no usable geometry")

    scale = canvas["width"] / w
    # The window must fill the canvas under a UNIFORM scale. If its aspect does
    # not match the canvas there is no uniform scale that does both axes, and
    # the honest answer is to refuse rather than stretch the picture.
    expected_h = scale * h
    if abs(expected_h - canvas["height"]) > ASPECT_TOLERANCE * canvas["height"]:
        raise UnrepresentableVisualState(
            f"crop window {w:g}x{h:g} does not match canvas "
            f"{canvas['width']}x{canvas['height']} — a uniform scale cannot "
            "fill it")

    dx = -(x + w / 2.0 - sw / 2.0) * scale
    dy = -(y + h / 2.0 - sh / 2.0) * scale
    return {
        "scale": round(scale, 6),
        "x": _position_unit(dx, canvas["width"]),
        "y": _position_unit(dy, canvas["height"]),
    }


def _scene_geometry(scene: dict[str, Any]) -> dict[str, Any]:
    composition = scene.get("composition") or {}
    if not isinstance(composition, dict):
        composition = {}
    return composition


def plan_scene(scene: dict[str, Any], *, canvas: dict[str, Any],
               source: dict[str, Any], ref: str) -> dict[str, Any]:
    """The CapCut representation of one resolved scene.

    ``ref`` is the compile-spec reference the item (and any keyframe target)
    uses. Raises ``UnrepresentableVisualState`` naming the exact representation
    this lane cannot carry — never a downgraded stand-in.
    """
    strategy = str(scene.get("strategy") or "").upper()
    composition = _scene_geometry(scene)
    representation = str(composition.get("representation") or strategy.lower())

    if strategy == "PASSTHROUGH" or representation == "passthrough":
        # Native framing: the canvas IS the source, so no scale and no offset.
        return {"ref": ref, "representation": "passthrough",
                "item": {}, "keyframes": [], "lane_ops": []}

    if representation == "crop_window":
        points = [p for p in (composition.get("change_points") or [])
                  if isinstance(p, dict)]
        if not points:
            raise UnrepresentableVisualState(
                "crop window carries no change-points, so its position is "
                "unknown")
        first = _crop_window_fields(points[0], canvas=canvas, source=source)
        item: dict[str, Any] = {"scale": first["scale"], "x": first["x"],
                                "y": first["y"]}
        if len(points) == 1:
            return {"ref": ref, "representation": "static_crop", "item": item,
                    "keyframes": [], "lane_ops": []}

        # A moving window stays MOVING: position (and, under a punch-in, size)
        # are keyframed at the render's own change-points. Never collapsed to
        # the first frame's crop.
        keyframes: list[dict[str, Any]] = []
        scale_varies = len({p.get("width") for p in points}) > 1
        for point in points:
            fields = _crop_window_fields(point, canvas=canvas, source=source)
            time = float(point.get("time") or 0.0)
            keyframes.append({"property": "position_x", "time": time,
                              "value": fields["x"], "easing": "hold"})
            if scale_varies:
                keyframes.append({"property": "uniform_scale", "time": time,
                                  "value": fields["scale"], "easing": "hold"})
        return {"ref": ref, "representation": "dynamic_crop", "item": item,
                "keyframes": keyframes, "lane_ops": []}

    if representation == "general":
        try:
            content_h = float(composition["content_height"])
        except (KeyError, TypeError, ValueError):
            raise UnrepresentableVisualState(
                "general composition has no content height") from None
        sh = float(source.get("height") or 0)
        if content_h <= 0 or sh <= 0:
            raise UnrepresentableVisualState(
                "general composition has no usable content geometry")
        # Foreground: source scaled so the content reaches the persisted height,
        # centred. Overflow past the canvas width is clipped by the frame, which
        # is what the render's own crop-to-frame-width did — so this stays a
        # LAYERED general composition and never becomes crop geometry.
        item = {"scale": round(content_h / sh, 6), "x": 0.0, "y": 0.0}
        bed = {"op": "bg-blur", "ref": ref, "level": BACKGROUND_BLUR_LEVEL,
               "renderer_background": composition.get("background"),
               "renderer_blur_sigma": composition.get("background_blur_sigma")}
        return {"ref": ref, "representation": "general", "item": item,
                "keyframes": [], "lane_ops": [bed]}

    raise UnrepresentableVisualState(
        f"the pinned CapCut lane cannot carry the '{strategy or representation}' "
        "composition faithfully (it is a multi-region/layered layout, not a "
        "single croppable window)")


def is_baked(state: dict[str, Any]) -> bool:
    """Whether the asset this block describes ALREADY contains the composition.

    A render writes a composed file: the crop, the scale and the joins are in
    its pixels. Applying the recorded geometry to that same file would crop it a
    second time, so a consumer must know which situation it is in — and the
    default for a block that does not say is "assume baked", because that is the
    only safe assumption (a needless identity transform shows the right picture;
    a needless second crop does not).
    """
    return state.get("output_baked") is not False


def plan_composition(state: dict[str, Any], *, canvas: dict[str, Any],
                     source: dict[str, Any], clip_start: float = 0.0,
                     clip_duration: float | None = None,
                     baked_identity: bool = False) -> dict[str, Any]:
    """The whole composition: per-scene CapCut items, keyframes and lane ops.

    ``clip_start`` is where the delivered clip begins INSIDE the working asset.
    The persisted scene times are clip-relative (that is the timeline the render
    wrote them on), so each item's ``sourceStart`` is ``clip_start + scene
    start`` — one translation into the asset's own timebase, applied exactly
    once, in the same convention the whole-clip item already uses.

    ``baked_identity`` means the media being handed over IS the composed render
    output (its identity matches the block's own record AND the block says the
    composition is in its pixels). Then there is nothing to apply: the clip is
    handed as ONE item at its native framing, and the recorded scenes travel as
    provenance describing what is already in the picture. This is the case that
    must never double-apply the framing.
    """
    scenes = [s for s in (state.get("scenes") or []) if isinstance(s, dict)]
    if not scenes:
        raise MissingVisualState("the resolved visual state records no scenes")
    if canvas is None:
        raise MissingVisualState("the resolved visual state records no canvas")

    if baked_identity:
        records = []
        last_end = 0.0
        for scene in scenes:
            try:
                start, end = float(scene["start"]), float(scene["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if clip_duration is not None:
                start, end = max(start, 0.0), min(end, float(clip_duration))
            if end - start <= 0:
                continue
            records.append({
                "strategy": str(scene.get("strategy") or ""),
                "representation": "already_baked",
                "start": round(start, 3), "end": round(end, 3),
                "manual": bool(scene.get("manual")),
            })
            last_end = max(last_end, end)
        if clip_duration is not None and clip_duration > 0:
            last_end = float(clip_duration)
        if not records:
            raise MissingVisualState("no resolved scene falls inside the clip")
        item = {"start": 0.0, "duration": round(last_end, 3),
                "sourceStart": round(float(clip_start), 3), "ref": "clip"}
        return {"items": [item], "keyframes": [], "lane_ops": [],
                "scenes": records, "duration": round(last_end, 3),
                "identity": True}

    items: list[dict[str, Any]] = []
    keyframes: list[dict[str, Any]] = []
    lane_ops: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    total = 0.0
    for index, scene in enumerate(scenes):
        try:
            start, end = float(scene["start"]), float(scene["end"])
        except (KeyError, TypeError, ValueError):
            raise MissingVisualState("a resolved scene has no usable time range") from None
        if clip_duration is not None:
            start, end = max(start, 0.0), min(end, float(clip_duration))
        if end - start <= 0:
            continue
        ref = f"scene{index}"
        plan = plan_scene(scene, canvas=canvas, source=source, ref=ref)
        item = {"start": round(start, 3),
                "duration": round(end - start, 3),
                "sourceStart": round(float(clip_start) + start, 3),
                "ref": ref}
        item.update(plan["item"])
        items.append(item)
        for keyframe in plan["keyframes"]:
            keyframes.append({**keyframe, "target": ref})
        lane_ops.extend(plan["lane_ops"])
        records.append({
            "strategy": str(scene.get("strategy") or ""),
            "representation": plan["representation"],
            "start": round(start, 3), "end": round(end, 3),
            "manual": bool(scene.get("manual")),
        })
        total = max(total, end)

    if not items:
        raise MissingVisualState("no resolved scene falls inside the clip")
    return {"items": items, "keyframes": keyframes, "lane_ops": lane_ops,
            "scenes": records, "duration": round(total, 3)}
