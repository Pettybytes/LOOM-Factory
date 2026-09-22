"""CHEAP whole-source moment locator — coarse to fine.

THE JOB: find WHERE the interesting moments are across the WHOLE source. This is
NOT the 0-3s clip-opening question, and it must never assume a moment is at source
start. LOOM scans for moments, period; the first 3 seconds matter later, when
choosing where the eventual clip should BEGIN.

Primary evidence is the cheapest timestamped evidence LOOM already retrieves:
transcript/caption segments (no media transfer at all). The existing Content
Intelligence ``candidate_timestamps`` are consumed when a model assessment has
already produced them. Media-level signal peaks are a bounded, later refinement —
never a whole-source visual scan.

Three distinct jobs, kept distinct:
  A. MOMENT DISCOVERY      (this module)    - where are the interesting areas?
  B. MOMENT UNDERSTANDING  (inspection)     - what actually happens there?
  C. CLIP BOUNDARY OPTIMISATION (clip_start)- where should the clip start/end?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

SHORT_FORM_MAX_SECONDS = 180.0        # <= 3 min: the source IS the moment
DEFAULT_MAX_ZONES = 25
DEFAULT_PROMOTE = 8

MODE_SOURCE_AS_MOMENT = "source_as_moment"
MODE_LONG_FORM = "long_form_container"

CUE_GROUPS: dict[str, tuple[str, ...]] = {
    "hook": ("oh my god", "what the", "no way", "look at this", "watch this",
             "are you serious", "wait", "why would you", "he's gonna", "she's gonna"),
    "event": ("fell", "fall", "impact", "crash", "hit", "collision", "dropped",
              "slipped", "tripped", "jumped", "ran", "grabbed", "threw", "broke"),
    "escalation": ("then", "suddenly", "all of a sudden", "started", "began",
                   "got worse", "kept", "again", "louder", "faster"),
    "emotional": ("screamed", "shocked", "surprised", "gasped", "laughed",
                  "crying", "furious", "angry", "terrified", "excited"),
    "conflict": ("confronted", "argument", "yelling", "fight", "called out",
                 "beef", "clapped back", "tension"),
    "reveal": ("revealed", "turned out", "actually", "secret", "confession",
               "admitted", "exposed"),
    "payoff": ("payoff", "landed", "nailed it", "did it", "finally", "worked",
               "it happened", "response", "reacted"),
    "chaos": ("chaos", "everything", "panic", "unhinged", "insane", "wild",
              "meltdown", "blew up"),
}
# Significance that exists ONLY in packaging is not moment evidence.
TITLE_ONLY_MARKERS = ("clickbait",)


def source_mode(source_duration: float | None) -> str:
    """Short-form = the source IS the moment; long-form = a moment CONTAINER."""
    if source_duration is None:
        return MODE_LONG_FORM          # unknown duration: assume we must scan
    return MODE_SOURCE_AS_MOMENT if float(source_duration) <= SHORT_FORM_MAX_SECONDS else MODE_LONG_FORM


@dataclass
class MomentZone:
    """One candidate moment located inside a source (never assumed at 0)."""

    source_id: str
    event_timestamp: float
    provisional_start: float
    provisional_end: float
    locator_reason: str
    locator_evidence: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    locator_confidence: float = 0.0
    transcript_excerpt: str = ""
    source_duration: float | None = None
    source_url: str = ""
    rank: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "source_url": self.source_url,
                "source_duration": self.source_duration,
                "candidate_event_timestamp": self.event_timestamp,
                "provisional_moment_start": self.provisional_start,
                "provisional_moment_end": self.provisional_end,
                "locator_reason": self.locator_reason,
                "locator_evidence": list(self.locator_evidence),
                "locator_confidence": self.locator_confidence,
                "provenance": list(self.provenance),
                "transcript_excerpt": self.transcript_excerpt, "rank": self.rank}


def _seg_text(seg: dict[str, Any]) -> str:
    return str(seg.get("text") or "").strip()


def _score_segment(text: str) -> tuple[float, tuple[str, ...]]:
    low = text.lower()
    hits = tuple(g for g, terms in CUE_GROUPS.items() if any(t in low for t in terms))
    if not hits:
        return 0.0, ()
    # Diversity matters more than raw repetition: one strong cue plus another
    # category beats the same word five times.
    return float(len(hits)), hits


def _cluster(scored: Sequence[tuple[int, float, tuple[str, ...]]],
             segments: Sequence[dict[str, Any]], *, gap_s: float = 12.0) -> list[list[int]]:
    """Group nearby scored segments into zones (coarse pass)."""
    zones: list[list[int]] = []
    for idx, _score, _hits in scored:
        if zones and float(segments[idx].get("start_s", 0.0)) - float(
                segments[zones[-1][-1]].get("start_s", 0.0)) <= gap_s:
            zones[-1].append(idx)
        else:
            zones.append([idx])
    return zones


def locate_moments(segments: Iterable[dict[str, Any]], *, source_id: str = "",
                   source_url: str = "", source_duration: float | None = None,
                   model_timestamps: Iterable[dict[str, Any]] | None = None,
                   max_zones: int = DEFAULT_MAX_ZONES) -> list[MomentZone]:
    """Cheaply locate candidate moments across the whole source.

    Returns ranked zones. For short-form sources the whole asset is one zone (the
    source IS the moment); for long-form the source is scanned and **the first
    zone is never assumed to be at 00:00 unless the evidence actually says so**.
    """
    segs = [s for s in (segments or []) if isinstance(s, dict)]
    mode = source_mode(source_duration)

    if mode == MODE_SOURCE_AS_MOMENT:
        start = float(segs[0].get("start_s", 0.0)) if segs else 0.0
        end = float(source_duration) if source_duration else (
            float(segs[-1].get("end_s", 0.0)) if segs else 0.0)
        text = " ".join(_seg_text(s) for s in segs)[:600]
        return [MomentZone(source_id=source_id, event_timestamp=start,
                           provisional_start=start, provisional_end=end,
                           locator_reason="short_form_source_is_the_moment",
                           locator_evidence=("short_form",), provenance=("duration<=180s",),
                           locator_confidence=0.5, transcript_excerpt=text,
                           source_duration=source_duration, source_url=source_url, rank=1)]

    scored: list[tuple[int, float, tuple[str, ...]]] = []
    for i, seg in enumerate(segs):
        score, hits = _score_segment(_seg_text(seg))
        if score > 0:
            scored.append((i, score, hits))
    zones: list[MomentZone] = []
    for grp in _cluster(scored, segs):
        idxs = grp
        starts = [float(segs[i].get("start_s", 0.0)) for i in idxs]
        ends = [float(segs[i].get("end_s", starts[k])) for k, i in enumerate(idxs)]
        hits: list[str] = []
        for i in idxs:
            for g in _score_segment(_seg_text(segs[i]))[1]:
                if g not in hits:
                    hits.append(g)
        event_ts = min(starts)
        confidence = round(min(1.0, (len(hits) / 4.0) * 0.7 + min(len(idxs), 5) / 5.0 * 0.3), 4)
        zones.append(MomentZone(
            source_id=source_id, event_timestamp=event_ts,
            provisional_start=event_ts, provisional_end=max(ends),
            locator_reason=f"cue_density:{'+'.join(hits[:3])}",
            locator_evidence=tuple(hits), provenance=("timestamped_transcript",),
            locator_confidence=confidence,
            transcript_excerpt=" ".join(_seg_text(segs[i]) for i in idxs)[:400],
            source_duration=source_duration, source_url=source_url))

    # Consume already-produced model timestamps as additional zones (no new model call).
    for ts in (model_timestamps or []):
        if not isinstance(ts, dict):
            continue
        s = ts.get("start_s")
        if s is None:
            continue
        zones.append(MomentZone(
            source_id=source_id, event_timestamp=float(s),
            provisional_start=float(s), provisional_end=float(ts.get("end_s") or s),
            locator_reason=f"content_intelligence_timestamp:{ts.get('basis') or 'model'}",
            locator_evidence=("model_timestamp",), provenance=("existing_assessment",),
            locator_confidence=0.6, source_duration=source_duration, source_url=source_url))

    zones.sort(key=lambda z: (-z.locator_confidence, z.event_timestamp))
    for i, z in enumerate(zones[:max_zones], start=1):
        z.rank = i
    return zones[:max_zones]


def promote_zones(zones: Sequence[MomentZone], *, keep: int = DEFAULT_PROMOTE) -> list[MomentZone]:
    """Rank locally and promote only the best zones to expensive inspection."""
    return list(zones[:keep])
