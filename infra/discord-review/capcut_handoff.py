"""LOOM -> CapCut handoff: build a `capcut-cli compile` spec from LOOM edit state.

OPTIONAL FINISHING LANE (Phase 2B fixture). This module is PURE: it maps the edit
state LOOM already has for one candidate/version into a declarative spec that
`capcut-cli compile` turns into a valid, editable CapCut draft.

It intentionally does NOT:
  * render, transcode, re-acquire or duplicate media;
  * touch approval/publishing state, CI scores, acquisition or SourceWindow;
  * replace any existing LOOM render/edit/rerender path.

Donor: `renezander030/capcut-cli` — MIT (see NOTICE below). Only the SPEC FORMAT is
reused here; no donor source is copied into this module. The CLI itself stays
external and optional.

FIELD MAPPING (LOOM edit state -> compile spec)
------------------------------------------------
LOOM (editor bootstrap)          -> compile spec
canvas (persisted output format) -> width, height, ratio
fps (not held by LOOM)           -> 30 (declared default, see DEFAULT_FPS)
media_path (local file)          -> tracks[video].items[0].path
source_range.start               -> tracks[video].items[0].sourceStart   (the TRIM-IN)
source_range.end - start         -> tracks[video].items[0].duration
resolved visual state            -> canvas + per-scene items/keyframes, and the
                                    lane's own background-blur operation for a
                                    general composition (see visual_state.py)
renderable_text.hook             -> tracks[text].items[0].text (0 -> min(2s, dur))
renderable_text.title            -> tracks[text].items[1].text (if present)
captions / word blocks           -> the compile spec's `captions` operation (an SRT the
                                    CLI turns into editable caption items). See
                                    build_caption_operation(); timing is never
                                    invented and absence is reported, not faked.
scenes / framing / social_caption-> NOT REPRESENTABLE (reported)

THE CANVAS IS PERSISTED TRUTH, WITH A LABELLED FALLBACK
-------------------------------------------------------
An operator's chosen output format must survive to CapCut. So the canvas comes
from the RESOLVED visual state the renderer recorded (``canvas=`` below), and
``DEFAULT_ASPECT``/``DEFAULT_FPS`` are compatibility fallbacks for genuinely
legacy state — an asset that never went through the renderer. The spec records
WHICH of the two happened in ``canvas_source`` (``persisted`` vs
``legacy_default``) so a fallback can never be mistaken for the operator's
choice, in the spec or in the handoff report.

COMPOSITION IS CARRIED, NOT RE-DERIVED
--------------------------------------
``composition`` is the plan ``visual_state.plan_composition`` produced from the
RESOLVED state: per-scene items (the real crop windows, in source pixels,
translated onto the working asset's timebase exactly once), the keyframes that
keep a dynamic TRACK crop MOVING, and the lane operations a layered general
composition needs. This module does no reframing arithmetic of its own, and it
never manufactures resolved state from a legacy layout label.

RENDERABLE COPY IS AN EXPLICIT CONTRACT (why it is not `bootstrap["title"]`)
---------------------------------------------------------------------------
Only text the operator (or LOOM's own copy) intends to appear ON SCREEN may
become a CapCut text object. A bootstrap's `title`/`hook` keys are NOT that
contract: on the source-review lane `title` carries the candidate's SOURCE
metadata, and source metadata is provenance — it says where the clip came from.
Reading it as copy is how a raw source URL ended up rendered as an orange
timeline text object.

So renderable copy is read from ONE explicit place, `RENDERABLE_TEXT_KEY`
(``renderable_text``), and each value is validated against the shared title
contract's provenance rule before it is allowed to become a timeline object.
There is deliberately NO fallback to `title`/`hook`.

NOTICE: spec format adapted from capcut-cli (MIT, (c) 2026 Rene Zander).
"""

from __future__ import annotations

import json
import os
from typing import Any

# "Guaranteed-valid CapCut draft" inputs the compile spec supports.
SUPPORTED = ("canvas (persisted output format)",
             "video items with trim-in (sourceStart) + per-scene composition",
             "item duration", "item composition (scale/position/keyframes)",
             "general composition background blur (lane operation)",
             "hook text", "title text", "fps")

#: The ONE key on the bootstrap whose values are renderable copy.
RENDERABLE_TEXT_KEY = "renderable_text"

# Honestly unsupported by the compile spec: reported, never invented.
UNSUPPORTED = {
    # MEASURED 2026-09-28 against the pinned donor (src/compile.ts): the spec's
    # TRACKS have no caption-cue type, but the spec DOES support captions via
    # operations[].op="captions", which parses an SRT into editable caption
    # items. build_capcut_spec() therefore still emits no caption track (its
    # behaviour is unchanged); captions travel through build_caption_operation().
    "captions": "not expressible as a TRACK item; supported via the spec's "
                "`captions` operation (see build_caption_operation) -- never "
                "fabricated, and reported as unavailable when absent",
    "transcript": "same as captions",
    "scenes": "no scene-marker primitive in the spec",
    "framing": "reframe/auto-framing is a render concern, not a spec field",
    "social_caption": "publishing copy, not timeline state",
    "clean_source_url": "lineage/render concern; not representable",
}

ASPECT_PRESETS = {  # matches capcut-cli's own canvas presets
    "9:16": {"width": 1080, "height": 1920, "ratio": "9:16"},
    "16:9": {"width": 1920, "height": 1080, "ratio": "16:9"},
    "1:1": {"width": 1080, "height": 1080, "ratio": "1:1"},
}
# Compatibility fallbacks ONLY: used when an asset genuinely carries no
# persisted canvas (legacy state), and always flagged as such in the spec via
# ``canvas_source``. They must never override a persisted format.
DEFAULT_ASPECT = "9:16"
DEFAULT_FPS = 30
HOOK_MAX_SECONDS = 2.0

#: How the spec's canvas was decided. Reported so a fallback is distinguishable
#: from the operator's persisted choice at every layer (spec + handoff report).
CANVAS_SOURCE_PERSISTED = "persisted"
CANVAS_SOURCE_LEGACY = "legacy_default"


class CapCutHandoffUnavailable(RuntimeError):
    """Raised when LOOM state cannot produce a legitimate handoff (never corrupts)."""


def _title_contract():
    """The shared human-title contract (provenance vs. human copy), or None.

    Reached through ``human_title``, which loads it by path so the resolution
    cannot be shadowed by this repo's ``acquisition.py`` module. Returning None
    is honest rather than fatal: the caller then applies a conservative fallback
    instead of silently trusting the value.
    """
    try:
        import human_title  # type: ignore
        return human_title
    except ImportError:
        return None


def is_renderable_copy(value: Any) -> bool:
    """Whether a value is copy that MAY be drawn on the timeline.

    Provenance is never copy: a source URL/pointer identifies where the clip came
    from, so drawing it is a category error, not a cosmetic one. Validation goes
    through the shared title contract so there is one definition of "this is a
    pointer, not a title"; if that contract cannot be imported the fallback still
    refuses an explicit scheme, which is the shape that actually reached a
    timeline.
    """
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    contract = _title_contract()
    if contract is not None:
        return not contract.is_provenance_reference(text)
    return "://" not in text


def renderable_copy(bootstrap: dict[str, Any],
                    renderable: dict[str, Any] | None = None
                    ) -> dict[str, str]:
    """The operator/renderable copy LOOM genuinely holds: ``{title, hook}``.

    Read from ONE explicit place — ``renderable`` when supplied, else the
    bootstrap's ``renderable_text`` mapping. A bootstrap's ``title``/``hook``
    keys are NOT consulted: on the source-review lane ``title`` carries the
    candidate's source metadata, and reading metadata as copy is exactly how a
    Kick source URL became an orange timeline text object.

    Each value must also pass ``is_renderable_copy``; a pointer tucked into the
    renderable slot is dropped rather than drawn.
    """
    source = renderable if isinstance(renderable, dict) else (
        bootstrap.get(RENDERABLE_TEXT_KEY)
        if isinstance(bootstrap, dict) else None)
    if not isinstance(source, dict):
        return {"title": "", "hook": ""}
    out = {}
    for key in ("title", "hook"):
        value = source.get(key)
        out[key] = str(value).strip()[:120] if is_renderable_copy(value) else ""
    return out


def build_capcut_spec(bootstrap: dict[str, Any], *, media_path: str,
                      aspect: str | None = None, fps: int | None = None,
                      renderable: dict[str, Any] | None = None,
                      canvas: dict[str, Any] | None = None,
                      composition: dict[str, Any] | None = None,
                      visual_state: dict[str, Any] | None = None,
                      clip_start: float = 0.0) -> dict[str, Any]:
    """Map LOOM edit state to a `capcut-cli compile` spec (seconds, not µs).

    Pure and side-effect free: raises `CapCutHandoffUnavailable` instead of guessing
    when the state is insufficient, so a failed handoff can never damage the
    candidate, its version, or any approval state.

    ``canvas`` is the PERSISTED canvas from the resolved visual state and wins
    over ``aspect``/``fps``; those are compatibility fallbacks for legacy state
    and the spec says which one was used (``canvas_source``). ``composition`` is
    the plan from ``visual_state.plan_composition`` and replaces the single
    whole-clip item with the scenes the render actually produced. ``visual_state``
    is the persisted block itself, echoed into the spec so the immutable handoff
    artifact records the state it was built from.
    """
    if not isinstance(bootstrap, dict):
        raise CapCutHandoffUnavailable("no edit state supplied")

    # Media: CapCut needs a REAL local file. A URL is not a media path.
    if not media_path or not os.path.isfile(media_path):
        raise CapCutHandoffUnavailable(
            f"no local media for the handoff (got {media_path!r}); CapCut cannot "
            "reference a remote URL")

    source_range = bootstrap.get("source_range") or {}
    try:
        start = float(source_range.get("start"))
        end = float(source_range.get("end"))
    except (TypeError, ValueError):
        raise CapCutHandoffUnavailable(
            "edit state has no usable source_range (trim is unknown)") from None
    if not (end > start):
        raise CapCutHandoffUnavailable(
            f"invalid source_range {start!r}..{end!r}: end must exceed start")
    duration = round(end - start, 3)

    persisted = canvas if isinstance(canvas, dict) and canvas.get("width") else None
    if persisted:
        canvas_fields = {"width": int(persisted["width"]),
                         "height": int(persisted["height"]),
                         "ratio": str(persisted.get("ratio") or ""),
                         "fps": int(persisted.get("fps") or DEFAULT_FPS)}
        canvas_source = CANVAS_SOURCE_PERSISTED
    else:
        preset = ASPECT_PRESETS.get(aspect or "") or ASPECT_PRESETS[DEFAULT_ASPECT]
        canvas_fields = {"width": preset["width"], "height": preset["height"],
                         "ratio": preset["ratio"],
                         "fps": int(fps or DEFAULT_FPS)}
        canvas_source = CANVAS_SOURCE_LEGACY

    copy = renderable_copy(bootstrap, renderable)
    spec: dict[str, Any] = {
        # The project name is VISIBLE in CapCut. It comes from renderable copy or
        # falls back to a neutral base label — never from source metadata. The
        # base is unprefixed because ``capcut_runner.safe_draft_name`` adds the
        # "LOOM " prefix exactly once.
        "name": (copy["title"] or "handoff")[:80],
        "width": canvas_fields["width"],
        "height": canvas_fields["height"],
        "ratio": canvas_fields["ratio"],
        "fps": canvas_fields["fps"],
        # Where the canvas came from: the operator's persisted format, or the
        # compatibility fallback. Never inferred by a consumer.
        "canvas_source": canvas_source,
        "tracks": [],
    }

    if composition and composition.get("items"):
        items = []
        for planned in composition["items"]:
            item = {"path": media_path,
                    "start": planned.get("start", 0),
                    "duration": planned.get("duration", duration),
                    "sourceStart": planned.get("sourceStart", start),
                    "ref": planned["ref"]}
            for key in ("scale", "x", "y", "rotation", "opacity"):
                if planned.get(key) is not None:
                    item[key] = planned[key]
            items.append(item)
        spec["tracks"].append({"type": "video", "items": items})

        # The persisted block travels IN the spec: the immutable handoff
        # artifact must record the actual visual state it was built from.
        spec["visual_state"] = visual_state or {}
        spec["composition"] = {"scenes": composition.get("scenes") or [],
                                "canvas_source": canvas_source}
        # Lane operations the compile spec itself cannot express (a layered
        # general composition's background blur). Applied by the runner through
        # the lane's own commands, never re-derived.
        if composition.get("lane_ops"):
            spec["lane_operations"] = list(composition["lane_ops"])
        operations = []
        for keyframe in composition.get("keyframes") or []:
            op = {"op": "keyframe", "target": keyframe["target"],
                  "property": keyframe["property"],
                  "time": keyframe["time"], "value": keyframe["value"]}
            if keyframe.get("easing"):
                op["easing"] = keyframe["easing"]
            operations.append(op)
        if operations:
            spec["operations"] = operations
    else:
        spec["tracks"].append({"type": "video", "items": [{
            "path": media_path,
            "start": 0,
            "duration": duration,
            "sourceStart": round(start, 3),   # LOOM's trim-in, in source seconds
        }]})

    # Text track: only copy LOOM genuinely intends to render. Absent -> no track.
    texts: list[dict[str, Any]] = []
    if copy["hook"]:
        texts.append({"text": copy["hook"], "start": 0,
                      "duration": round(min(HOOK_MAX_SECONDS, duration), 3)})
    if copy["title"]:
        texts.append({"text": copy["title"], "start": 0, "duration": duration})
    if texts:
        spec["tracks"].append({"type": "text", "items": texts})
    return spec


def handoff_report(bootstrap: dict[str, Any],
                   renderable: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the handoff WILL and WILL NOT carry, for the operator and the record."""
    copy = renderable_copy(bootstrap, renderable)
    carried = []
    if (bootstrap.get("source_range") or {}).get("start") is not None:
        carried.append("selected trim (sourceStart + duration)")
    if copy["hook"]:
        carried.append("hook text")
    if copy["title"]:
        carried.append("title text")
    return {
        "carried": carried or ["canvas + video item"],
        "unsupported": dict(UNSUPPORTED),
        "note": "fixture: one-way handoff only; no round-trip import from CapCut",
    }


def write_spec(spec: dict[str, Any], out_path: str) -> str:
    """Write the spec next to the media so relative paths resolve as documented."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)
    return out_path


CAPTION_TRACK_NAME = "captions"


def normalize_caption_blocks(blocks: Any) -> list[dict[str, Any]]:
    """Durable caption/word blocks -> ordered cues, ignoring what is unusable.

    NEVER invents timing: a block without BOTH a numeric start and end is
    dropped rather than estimated, so a half-timed block cannot reach the
    draft. Ordering is by start time so the emitted SRT is monotonic.
    """
    cues: list[dict[str, Any]] = []
    for block in (blocks or []):
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or block.get("word") or "").strip()
        raw_start = block.get("startMs", block.get("start_ms"))
        raw_end = block.get("endMs", block.get("end_ms"))
        try:
            start = float(raw_start)
            end = float(raw_end)
        except (TypeError, ValueError):
            continue
        if not text or start < 0 or not (end > start):
            continue
        cues.append({"text": text, "startMs": start, "endMs": end})
    cues.sort(key=lambda c: (c["startMs"], c["endMs"]))
    return cues


def srt_timestamp(ms: float) -> str:
    """Milliseconds -> SRT `HH:MM:SS,mmm`."""
    total = int(round(float(ms)))
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def build_srt(cues: list[dict[str, Any]]) -> str:
    """Render cues as SRT text (pure; no I/O)."""
    parts = []
    for index, cue in enumerate(cues, start=1):
        parts.append(f"{index}\n{srt_timestamp(cue['startMs'])} --> "
                     f"{srt_timestamp(cue['endMs'])}\n{cue['text']}\n")
    return "\n".join(parts)


def write_srt(cues: list[dict[str, Any]], out_path: str) -> str:
    """Write an SRT next to the spec so the caption operation resolves."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(build_srt(cues))
    return out_path


def build_caption_operation(blocks: Any, srt_path: str) -> dict[str, Any] | None:
    """The spec `captions` operation for durable word blocks, or None.

    MEASURED against the pinned donor (src/compile.ts): `operations[].op="captions"`
    parses the SRT at `path` into EDITABLE caption text items on their own track.

    Timing passes through in the WORKING timebase with a zero offset, because
    LOOM's durable caption blocks are already recorded clip-relative (relative to
    the same working asset CapCut receives). Applying the source timebase origin
    here would double-offset every caption.

    Returns None when there are no durable cues — the caller reports
    "captions unavailable for this version" rather than inventing timing.
    """
    cues = normalize_caption_blocks(blocks)
    if not cues:
        return None
    return {"op": "captions", "path": srt_path,
            "trackName": CAPTION_TRACK_NAME, "timeOffset": 0}


def capcut_cli_path() -> str:
    """Where the optional, external `capcut-cli` lives (never a hard dependency).

    Resolution order: explicit env -> a vendored checkout in the repo -> PATH.
    Returns "" when absent, which the caller must treat as "lane unavailable".
    """
    explicit = os.environ.get("LOOM_CAPCUT_CLI", "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in ("vendor/capcut-cli", "../../vendor/capcut-cli"):
        path = os.path.abspath(os.path.join(here, candidate))
        if os.path.isdir(path):
            return path
    for on_path in os.environ.get("PATH", "").split(os.pathsep):
        binary = os.path.join(on_path, "capcut")
        if os.path.isfile(binary) and os.access(binary, os.X_OK):
            return binary
    return ""
