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
canvas (LOOM 9:16 vertical)      -> width 1080, height 1920, ratio "9:16"
fps (not held by LOOM)           -> 30 (declared default, see DEFAULT_FPS)
media_path (local file)          -> tracks[video].items[0].path
source_range.start               -> tracks[video].items[0].sourceStart   (the TRIM-IN)
source_range.end - start         -> tracks[video].items[0].duration
hook                             -> tracks[text].items[0].text (0 -> min(2s, dur))
title                            -> tracks[text].items[1].text (if present)
captions / transcript            -> NOT REPRESENTABLE in the compile spec (reported,
                                    never invented) -- see UNSUPPORTED
scenes / framing / social_caption-> NOT REPRESENTABLE (reported)

NOTICE: spec format adapted from capcut-cli (MIT, (c) 2026 Rene Zander).
"""

from __future__ import annotations

import json
import os
from typing import Any

# "Guaranteed-valid CapCut draft" inputs the compile spec supports.
SUPPORTED = ("canvas (9:16 / 16:9)", "video item with trim-in (sourceStart)",
             "item duration", "hook text", "title text", "fps")

# Honestly unsupported by the compile spec: reported, never invented.
UNSUPPORTED = {
    "captions": "the compile spec has no caption-cue type; CapCut captions need the "
                "caption/SRT path, which this fixture does not wire",
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
DEFAULT_ASPECT = "9:16"
DEFAULT_FPS = 30
HOOK_MAX_SECONDS = 2.0


class CapCutHandoffUnavailable(RuntimeError):
    """Raised when LOOM state cannot produce a legitimate handoff (never corrupts)."""


def build_capcut_spec(bootstrap: dict[str, Any], *, media_path: str,
                      aspect: str = DEFAULT_ASPECT, fps: int = DEFAULT_FPS,
                      hook: str = "", title: str = "") -> dict[str, Any]:
    """Map LOOM edit state to a `capcut-cli compile` spec (seconds, not µs).

    Pure and side-effect free: raises `CapCutHandoffUnavailable` instead of guessing
    when the state is insufficient, so a failed handoff can never damage the
    candidate, its version, or any approval state.
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

    preset = ASPECT_PRESETS.get(aspect) or ASPECT_PRESETS[DEFAULT_ASPECT]
    spec: dict[str, Any] = {
        "name": (bootstrap.get("title") or title or "LOOM handoff")[:80],
        "width": preset["width"],
        "height": preset["height"],
        "ratio": preset["ratio"],
        "fps": int(fps or DEFAULT_FPS),
        "tracks": [
            {"type": "video", "items": [{
                "path": media_path,
                "start": 0,
                "duration": duration,
                "sourceStart": round(start, 3),   # LOOM's trim-in, in source seconds
            }]},
        ],
    }

    # Text track: only what LOOM genuinely holds. Absent -> no track at all.
    texts: list[dict[str, Any]] = []
    hook_text = (hook or bootstrap.get("hook") or "").strip()
    if hook_text:
        texts.append({"text": hook_text[:120], "start": 0,
                      "duration": round(min(HOOK_MAX_SECONDS, duration), 3)})
    title_text = (title or bootstrap.get("title") or "").strip()
    if title_text:
        texts.append({"text": title_text[:120], "start": 0, "duration": duration})
    if texts:
        spec["tracks"].append({"type": "text", "items": texts})
    return spec


def handoff_report(bootstrap: dict[str, Any]) -> dict[str, Any]:
    """What the handoff WILL and WILL NOT carry, for the operator and the record."""
    carried = []
    if (bootstrap.get("source_range") or {}).get("start") is not None:
        carried.append("selected trim (sourceStart + duration)")
    if (bootstrap.get("hook") or "").strip():
        carried.append("hook text")
    if (bootstrap.get("title") or "").strip():
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
