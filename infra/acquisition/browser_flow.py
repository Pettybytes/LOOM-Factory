"""Browser-assisted acquisition flow decisions (deterministic, testable).

Mirrored by the Chrome extension's background worker. Pure decision logic so the
repair can be proven without a browser: navigation classification (expected
same-content reload vs unexpected navigation vs content-ID change), deterministic
candidate selection, and exactly-once helper handoff.
"""

from __future__ import annotations

import re

TYPE_PRIORITY = {"direct_video": 0, "manifest": 1, "segment": 2, "audio": 3}

_IG_ID_RE = re.compile(r"instagram\.com/(?:reel|p|tv|stories)/([A-Za-z0-9_-]+)")


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


def select_best_candidate(candidates):
    """Deterministic selection: media-type priority, then most-recent request."""
    if not candidates:
        return None

    def key(c):
        priority = TYPE_PRIORITY.get(c.get("type"), 9)
        order = c.get("order", 0)
        return (priority, -order)

    return min(candidates, key=key)


def decide_handoff(candidates, already_sent: bool, timed_out: bool = False):
    """Decide whether to hand off now. Returns (handoff, candidate, reason).

    Handoff happens exactly once: the ``already_sent`` flag short-circuits.
    A direct_video candidate triggers immediate handoff (fast path, no reload);
    otherwise we wait until the fail-closed timeout for a best-effort selection."""
    if already_sent:
        return (False, None, "already_sent")
    if not candidates:
        return (False, None, "no_candidates")
    best = select_best_candidate(candidates)
    if best.get("type") == "direct_video":
        return (True, best, "direct_video_found")
    if timed_out:
        return (True, best, "timeout_best_effort")
    return (False, best, "waiting_for_direct_video")
