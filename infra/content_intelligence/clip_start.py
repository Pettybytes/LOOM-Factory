"""Clip-start optimisation — where should the eventual Short BEGIN?

The first ~3 seconds are a BOUNDARY signal: if the clip starts at this point, is
there an immediate reason not to swipe? **The event timestamp is NOT automatically
the clip start.** Starting a second or two early can show the thing already going
wrong (anticipation), while starting exactly on impact loses the setup.

This runs AFTER a moment has been located and understood; it never scans a source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .moment_locator import CUE_GROUPS

DEFAULT_LEADS: tuple[float, ...] = (5.0, 3.0, 1.0, 0.0)
HOOK_WINDOW_SECONDS = 3.0
DEFAULT_MIN_DURATION = 8.0

ANTICIPATION_TERMS = ("about to", "going to", "wobbl", "teeter", "slowly", "starting to",
                      "before", "watch", "here we go", "uh oh")
# Openings that give the viewer no reason to stay.
DEAD_OPENING_TERMS = ("welcome back", "thanks for watching", "subscribe", "intro",
                      "sponsor", "in this video", "let's get started", "as i was saying")


@dataclass
class StartAlternative:
    start: float
    rationale: str
    hook_strength: float
    hook_evidence: tuple[str, ...] = ()


@dataclass
class ClipStartPlan:
    event_timestamp: float
    recommended_start: float
    recommended_end: float
    start_reason: str
    hook_evidence_0_3: tuple[str, ...] = ()
    hook_strength: float = 0.0
    alternatives: tuple[StartAlternative, ...] = ()
    lead_seconds: float = 0.0

    @property
    def start_offset_from_event(self) -> float:
        return round(self.event_timestamp - self.recommended_start, 3)

    def as_dict(self) -> dict[str, Any]:
        return {"event_timestamp": self.event_timestamp,
                "recommended_clip_start": self.recommended_start,
                "recommended_clip_end": self.recommended_end,
                "start_reason": self.start_reason,
                "hook_evidence_0_3": list(self.hook_evidence_0_3),
                "hook_strength": self.hook_strength,
                "lead_seconds": self.lead_seconds,
                "alternatives": [{"start": a.start, "rationale": a.rationale,
                                  "hook_strength": a.hook_strength,
                                  "hook_evidence": list(a.hook_evidence)} for a in self.alternatives]}


def _window_text(segments: Sequence[dict[str, Any]], start: float, end: float) -> str:
    """Text that would actually be HEARD if the clip began at ``start``.

    A segment must BEGIN inside the prospective clip window. Using mere overlap
    would count dialogue that finished before the clip started, which makes every
    candidate start look identical and defeats boundary optimisation entirely.
    """
    out: list[str] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        s = float(seg.get("start_s", 0.0) or 0.0)
        if start - 0.001 <= s <= end:
            out.append(str(seg.get("text") or ""))
    return " ".join(out).strip()


def score_prospective_hook(segments: Sequence[dict[str, Any]], start: float,
                           window: float = HOOK_WINDOW_SECONDS) -> tuple[float, tuple[str, ...]]:
    """Strength of the FIRST ~3s if the clip starts at ``start`` (0..1 + evidence)."""
    text = _window_text(segments, start, start + window).lower()
    if not text:
        return 0.0, ()
    hits: list[str] = []
    for group, terms in CUE_GROUPS.items():
        if any(t in text for t in terms):
            hits.append(group)
    if any(t in text for t in ANTICIPATION_TERMS):
        hits.append("anticipation")
    dead = any(t in text for t in DEAD_OPENING_TERMS)
    strength = min(1.0, len(hits) / 4.0)
    if dead:
        strength = max(0.0, strength - 0.5)
        hits.append("dead_opening")
    return round(strength, 4), tuple(hits)


def optimize_clip_start(event_timestamp: float, segments: Sequence[dict[str, Any]], *,
                        leads: Iterable[float] = DEFAULT_LEADS,
                        min_duration: float = DEFAULT_MIN_DURATION,
                        duration_hint: float | None = None) -> ClipStartPlan:
    """Choose the clip start, preferring an opening with immediate reason to stay.

    Never blindly sets ``clip_start = event_timestamp``: the event timestamp is
    scored alongside the earlier leads, and the choice plus alternatives are
    recorded.
    """
    event = float(event_timestamp)
    alts: list[StartAlternative] = []
    for lead in leads:
        start = max(0.0, event - float(lead))
        strength, evidence = score_prospective_hook(segments, start)
        has_anticipation = "anticipation" in evidence
        rationale = ("event_immediate" if lead == 0 else
                     "anticipation_before_event" if has_anticipation else
                     "setup_lead_in")
        # Prefer a lead-in that shows something already under way; otherwise the
        # event itself. Pure setup with no evidence scores lowest.
        bonus = 0.15 if has_anticipation else 0.0
        alts.append(StartAlternative(round(start, 3), rationale,
                                     round(min(1.0, strength + bonus), 4), tuple(evidence)))

    def _score(a: StartAlternative) -> tuple[float, float]:
        immediate = 0.1 if a.rationale == "event_immediate" else 0.0
        return (a.hook_strength + immediate, -a.start)

    best = max(alts, key=_score)
    end = event + min_duration
    if duration_hint is not None:
        end = min(end, float(duration_hint))
    return ClipStartPlan(
        event_timestamp=event, recommended_start=best.start, recommended_end=round(end, 3),
        start_reason=best.rationale, hook_evidence_0_3=best.hook_evidence,
        hook_strength=best.hook_strength, alternatives=tuple(alts),
        lead_seconds=round(event - best.start, 3))
