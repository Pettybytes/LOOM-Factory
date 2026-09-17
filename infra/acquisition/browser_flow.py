"""Browser-assisted acquisition flow decisions (deterministic, testable).

Mirrored by the Chrome extension's background worker. Pure decision logic so the
repair can be proven without a browser:

  * navigation classification (expected same-content reload vs content-ID change)
  * media-type classification where Instagram's ``.mp4`` suffix is AMBIGUOUS —
    a ``.mp4`` path may be a standalone MP4 (``ftyp``) OR a fragmented-MP4
    fragment (``moof``/``styp``/``sidx``); only the byte signature is
    authoritative (checked by the helper).
  * exactly-once helper handoff: a manifest early-exits; an ambiguous ``.mp4`` or
    direct-video request does NOT early-exit — the collector waits for the
    fail-closed window, then hands off an assemblable fMP4 set.
  * ranged-request collection: distinct byte ranges from one base URL are kept
    DISTINCT and ordered; incomplete/invalid sets fail closed with
    ``INSUFFICIENT_FMP4_SET`` rather than forcing a partial assembly.
"""

from __future__ import annotations

import re

# Media types (mirrors media_filter.classify_media_url).
MANIFEST = "manifest"
DIRECT_VIDEO = "direct_video"   # unambiguous standalone container (.m4v / .webm)
MP4 = "mp4"                     # AMBIGUOUS: standalone (ftyp) OR fMP4 fragment (moof)
SEGMENT = "segment"             # DASH/HLS media segment (.m4s / .ts / .fmp4 / …)
AUDIO = "audio"

REPRESENTATION_VIDEO = "video"
REPRESENTATION_AUDIO = "audio"

INSUFFICIENT_FMP4_SET = "INSUFFICIENT_FMP4_SET"

# Selection priority when a single best candidate must be chosen (manifest first;
# an ambiguous .mp4 ranks before a bare segment so a full fetch is preferred).
TYPE_PRIORITY = {MANIFEST: 0, DIRECT_VIDEO: 1, MP4: 2, SEGMENT: 3, AUDIO: 4}

_IG_ID_RE = re.compile(r"instagram\.com/(?:reels?|p|tv|stories)/([A-Za-z0-9_-]+)")
_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")


def instagram_content_id(url: str) -> str:
    """Extract the Instagram post/reel content id from a URL, or '' if unknown."""
    m = _IG_ID_RE.search(url or "")
    return m.group(1) if m else ""


def classify_navigation(new_url: str, expected_content_id: str) -> str:
    """Classify a tab navigation relative to the selected source.

    Returns 'same_content' | 'different_content' | 'unknown'."""
    new_id = instagram_content_id(new_url)
    if not new_id or not expected_content_id:
        return "unknown"
    return "same_content" if new_id == expected_content_id else "different_content"


def parse_range_header(value: str) -> tuple[int, int | None] | None:
    """Parse a ``Range: bytes=start-end`` header -> (start, end_or_None)."""
    m = _RANGE_RE.match((value or "").strip())
    if not m:
        return None
    start = int(m.group(1))
    end = m.group(2)
    return (start, int(end) if end else None)


def representation_of(candidate: dict) -> str:
    """Best-effort video vs audio representation for a candidate.

    Prefers an explicit ``representation``, then the MIME type, then the URL
    class. Unknown media defaults to video (the video track is the required one).
    """
    r = candidate.get("representation")
    if r in (REPRESENTATION_VIDEO, REPRESENTATION_AUDIO):
        return r
    mt = (candidate.get("mime_type") or candidate.get("mimeType") or "").lower()
    if mt.startswith("audio/"):
        return REPRESENTATION_AUDIO
    if mt.startswith("video/"):
        return REPRESENTATION_VIDEO
    if candidate.get("type") == AUDIO:
        return REPRESENTATION_AUDIO
    return REPRESENTATION_VIDEO


def fragment_role(candidate: dict) -> str:
    """Deterministic role for a candidate: 'init' | 'media' | 'standalone' | 'manifest'.

    An explicit ``role`` wins. Otherwise the heuristic (best-effort; the helper's
    byte signature is authoritative at assembly time):

      * ``segment``            -> media fragment
      * ``direct_video``       -> standalone container
      * ``mp4`` + byte range   -> media fragment (ranged request)
      * ``mp4`` + no range     -> init segment (full fetch)
      * ``manifest``           -> manifest
    """
    if candidate.get("role"):
        return candidate["role"]
    t = candidate.get("type")
    if t == SEGMENT:
        return "media"
    if t == DIRECT_VIDEO:
        return "standalone"
    if t == MP4:
        if candidate.get("range_start") is not None or candidate.get("range"):
            return "media"
        return "init"
    if t == MANIFEST:
        return "manifest"
    return "unknown"


def _redact(url: str) -> str:
    """Strip query string + fragment (signed tokens). Origin + path only."""
    try:
        from urllib.parse import urlsplit
        p = urlsplit(url or "")
        return p.scheme + "://" + p.netloc + (p.path or "")
    except Exception:
        return "(unparseable)"


def candidate_identity(candidate: dict) -> tuple[str, str]:
    """Distinctness key: redacted base URL + byte range.

    Two ranged requests from the SAME base URL are DISTINCT when their byte
    ranges differ — they must NOT be deduplicated merely because the base URL
    matches.
    """
    url = candidate.get("url") or candidate.get("fullUrl") or ""
    base = _redact(url)
    rs = candidate.get("range_start")
    re_ = candidate.get("range_end")
    if rs is not None:
        rng = f"{rs}-{'' if re_ is None else re_}"
    else:
        rng = (candidate.get("range") or "")
    return (base, rng)


def distinct_candidates(candidates: list) -> list:
    """Keep DISTINCT candidates (base URL + range), preserving request order."""
    seen = set()
    out = []
    for c in candidates or []:
        key = candidate_identity(c)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def persisted_candidate(candidate: dict) -> dict:
    """Sanitized candidate for persistence: redacted URL + range metadata ONLY.

    Never includes the full signed URL, cookies, Authorization, or any other
    request/response header. This is what the extension persists to
    ``chrome.storage.session``.
    """
    url = candidate.get("url") or candidate.get("fullUrl") or ""
    out = {
        "host": candidate.get("host") or "",
        "type": candidate.get("type"),
        "representation": representation_of(candidate),
        "role": fragment_role(candidate),
        "redacted": _redact(url) if url else None,
        "range": candidate.get("range"),
        "range_start": candidate.get("range_start"),
        "range_end": candidate.get("range_end"),
        "range_total": candidate.get("range_total"),
        "order": candidate.get("order"),
    }
    # Defensive: drop any sensitive keys a caller might have attached.
    for k in ("url", "fullUrl", "cookies", "cookie", "authorization", "auth",
              "headers", "requestHeaders", "responseHeaders"):
        out.pop(k, None)
    return out


def select_best_candidate(candidates: list) -> dict | None:
    """Deterministic selection: media-type priority, then most-recent request."""
    if not candidates:
        return None

    def key(c):
        priority = TYPE_PRIORITY.get(c.get("type"), 9)
        order = c.get("order", 0)
        return (priority, -order)

    return min(candidates, key=key)


# ---------------------------------------------------------------------------
# Ranged fMP4 set building (collection -> validated payload)
# ---------------------------------------------------------------------------
def _most_recent(items: list) -> dict | None:
    if not items:
        return None
    return max(items, key=lambda c: (c.get("order", 0),))


def _order_and_validate_ranges(fragments: list) -> list | None:
    """Sort media fragments into byte order and validate contiguity.

    Returns the ordered fragment list, or None when the set is invalid:
      * a fragment missing its byte range (cannot establish order)
      * overlapping or duplicate ranges
      * a gap between ranges (missing fragment)
      * a malformed range (start > end) or an open-ended range that is not last
      * inconsistent Content-Range totals (mixed resource / representation)
      * fragments from different base URLs (mixed representations)
    """
    if not fragments:
        return None
    # All fragments must carry a byte range to establish a deterministic order.
    if any(f.get("range_start") is None for f in fragments):
        return None
    # All media fragments must belong to the SAME resource (base URL).
    base = _redact(fragments[0].get("url") or fragments[0].get("fullUrl") or "")
    if any(_redact(f.get("url") or f.get("fullUrl") or "") != base for f in fragments):
        return None

    ordered = sorted(fragments, key=lambda f: (f["range_start"], f.get("order", 0)))
    total = ordered[0].get("range_total")
    prev_end = None
    for idx, f in enumerate(ordered):
        start = f["range_start"]
        end = f.get("range_end")
        if end is None:
            # open-ended range is only valid as the final fragment
            if idx != len(ordered) - 1:
                return None
            continue
        if start > end:
            return None  # malformed range
        ft = f.get("range_total")
        if total is not None and ft is not None and ft != total:
            return None  # mixed resource / representation
        if prev_end is not None:
            if start <= prev_end:
                return None  # overlap or duplicate
            if start > prev_end + 1:
                return None  # gap (missing range)
        prev_end = end
    return ordered


def _plan_representation(fragments: list) -> dict | None:
    """Build {init_url, segments} for one representation, or None if invalid."""
    if not fragments:
        return None
    inits = [f for f in fragments if fragment_role(f) == "init"]
    medias = [f for f in fragments if fragment_role(f) == "media"]
    standalones = [f for f in fragments if fragment_role(f) == "standalone"]

    # An unambiguous standalone (.m4v/.webm) is a complete single item.
    if standalones and not inits and not medias:
        best = _most_recent(standalones)
        return {"init_url": "", "segments": [best.get("url") or best.get("fullUrl")], "standalone": True}

    if not inits:
        return None  # no init -> video requires initialization data
    if not medias:
        return None  # no media fragments -> nothing to assemble

    init = _most_recent(inits)
    ordered = _order_and_validate_ranges(medias)
    if ordered is None:
        return None

    return {
        "init_url": init.get("url") or init.get("fullUrl"),
        "segments": [f.get("url") or f.get("fullUrl") for f in ordered],
    }


def build_fmp4_set(candidates: list) -> tuple[dict | None, str | None]:
    """Build an assemblable fMP4 payload from ranged candidates, or fail closed.

    Returns (payload, error_code). payload is None on failure; error_code is
    ``INSUFFICIENT_FMP4_SET`` when the set is incomplete or invalid. A timeout
    must NOT force assembly of an incomplete set.
    """
    media = [c for c in (candidates or []) if c.get("type") != MANIFEST]
    if not media:
        return None, INSUFFICIENT_FMP4_SET

    video, audio = [], []
    for c in media:
        (video if representation_of(c) == REPRESENTATION_VIDEO else audio).append(c)

    video_plan = _plan_representation(video)
    if video_plan is None:
        return None, INSUFFICIENT_FMP4_SET

    audio_plan = None
    if audio:
        # Audio evidence was captured -> audio is "proven"; it must be complete.
        audio_plan = _plan_representation(audio)
        if audio_plan is None:
            return None, INSUFFICIENT_FMP4_SET

    payload = {
        "init_url": video_plan["init_url"],
        "segments": video_plan["segments"],
        "audio_init_url": (audio_plan or {}).get("init_url", ""),
        "audio_segments": (audio_plan or {}).get("segments", []),
        "has_audio": bool(audio_plan and audio_plan.get("init_url") and audio_plan.get("segments")),
    }
    return payload, None


def decide_handoff(candidates: list, already_sent: bool = False, timed_out: bool = False):
    """Decide whether to hand off now. Returns (handoff, payload, reason).

    A manifest early-exits (it fully describes init + ordered segments). An
    ambiguous ``.mp4`` / ``direct_video`` request does NOT early-exit — it waits
    for the bounded window. At timeout we hand off an assemblable fMP4 set, a
    single candidate (helper byte-signature decides), or ``INSUFFICIENT_FMP4_SET``.
    """
    if already_sent:
        return (False, None, "already_sent")
    if not candidates:
        return (False, None, "no_candidates")

    manifest = _most_recent([c for c in candidates if c.get("type") == MANIFEST])
    if manifest:
        return (True, {"manifest_url": manifest.get("url") or manifest.get("fullUrl")}, "manifest_found")

    if not timed_out:
        return (False, None, "collecting")

    # Timed out: try to build a validated assemblable fMP4 set.
    payload, err = build_fmp4_set(candidates)
    if payload:
        return (True, payload, "assemblable_fmp4_set")

    # Fallback: a single ambiguous/standalone candidate -> helper byte-signature
    # classifies ftyp (standalone, keep) vs moof (fragment, reject).
    singles = [c for c in candidates if c.get("type") in (MP4, DIRECT_VIDEO)]
    if len(singles) == 1:
        return (True, {"candidate_url": singles[0].get("url") or singles[0].get("fullUrl")}, "single_candidate_signature_check")

    return (False, None, INSUFFICIENT_FMP4_SET)
