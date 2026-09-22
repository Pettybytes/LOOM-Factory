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

MODE_SOURCE_AS_MOMENT = "source_as_moment"
MODE_LONG_FORM = "long_form_container"

# Deterministic locator weights. These RANK candidates; they are NOT claims of
# statistical virality. Configurable, documented, never presented as probabilities.
SIGNAL_BASIS_WEIGHTS: dict[str, float] = {
    "audio_spike": 0.85, "motion": 0.6, "scene_change": 0.5, "novelty": 0.65,
    "audio_energy": 0.45, "speech_onset": 0.4, "cut_density": 0.4,
}
# A single isolated low-weight blip must not become a candidate (comparator lesson:
# "a single spike is not a moment"). Several aligned signals should.
MIN_PROMOTE_CONFIDENCE = 0.45
DEFAULT_PROMOTE = 3
CONTINUITY_GAP_SECONDS = 12.0

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


# Asset characteristics decide the mode - NEVER the platform name. Twitch and Kick
# both publish clips (source-as-moment) AND VODs/streams (containers); YouTube
# publishes Shorts and long uploads; Reddit may host either.
CLIP_ASSET_KINDS = frozenset({"clip", "clips", "short", "shorts", "reel", "reels",
                              "tiktok", "video_short", "short_form"})
CONTAINER_ASSET_KINDS = frozenset({"vod", "stream", "live", "livestream", "podcast",
                                   "interview", "upload", "long_video", "video",
                                   "long_form", "broadcast"})
# Unambiguous CLIP-shaped URLs.
CLIP_URL_HINTS = ("/shorts/", "clips.twitch.tv", "clips.kick.com", "/clip/", "clip=",
                  "tiktok.com/", "instagram.com/reel", "/reels/")
# Unambiguous CONTAINER-shaped URLs.
CONTAINER_URL_HINTS = ("twitch.tv/videos/", "kick.com/video/", "youtube.com/live/",
                       "/watch?", "youtu.be/", "/videos/")


def _norm_kind(kind: Any) -> str:
    return str(kind or "").strip().lower().replace(" ", "_")


def source_mode(source_duration: float | None = None, *, asset_kind: Any = None,
                url: str | None = None, media_type: Any = None) -> str:
    """Is this asset ALREADY a discrete moment, or a CONTAINER holding many?

    Order of evidence: explicit asset kind, then URL shape, then duration. An
    unknown asset is treated as a container so it is scanned rather than assumed
    to be a single moment.
    """
    kind = _norm_kind(asset_kind)
    if kind in CLIP_ASSET_KINDS:
        return MODE_SOURCE_AS_MOMENT
    if kind in CONTAINER_ASSET_KINDS:
        return MODE_LONG_FORM

    mt = _norm_kind(media_type)
    if mt in CLIP_ASSET_KINDS:
        return MODE_SOURCE_AS_MOMENT
    if mt in CONTAINER_ASSET_KINDS:
        return MODE_LONG_FORM

    u = str(url or "").lower()
    # An unambiguous CLIP URL is strong evidence, and it is checked before duration
    # because clip URLs are distinctive.
    if u and any(h in u for h in CLIP_URL_HINTS):
        return MODE_SOURCE_AS_MOMENT

    # DURATION next: a short asset is a moment even when it is served from a
    # container-shaped URL (Shorts are routinely reachable via /watch), so a weak
    # container URL must not override a genuinely short duration.
    if source_duration is not None:
        return (MODE_SOURCE_AS_MOMENT if float(source_duration) <= SHORT_FORM_MAX_SECONDS
                else MODE_LONG_FORM)

    # Container URL is the weakest remaining hint; unknown falls through to scanning.
    if u and any(h in u for h in CONTAINER_URL_HINTS):
        return MODE_LONG_FORM
    return MODE_LONG_FORM


def _asset_kind_of(record: Any) -> str | None:
    """Provider asset type when the connector exposes one (never inferred from platform)."""
    for attr in ("asset_kind", "provider_asset_type", "asset_type", "content_kind",
                 "source_asset_type"):
        v = getattr(record, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if getattr(record, "is_clip", None) is True:
        return "clip"
    if getattr(record, "is_live", None) is True:
        return "stream"
    metrics = getattr(record, "observed_metrics", None)
    if isinstance(metrics, dict):
        for key in ("asset_kind", "asset_type", "is_clip", "is_live", "type"):
            v = metrics.get(key)
            if isinstance(v, bool) and v:
                return "clip" if key == "is_clip" else "stream"
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _duration_of(record: Any) -> float | None:
    for attr in ("duration_seconds", "duration", "source_duration"):
        v = getattr(record, attr, None)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return float(v)
    metrics = getattr(record, "observed_metrics", None)
    if isinstance(metrics, dict):
        for key in ("duration", "duration_seconds", "length_seconds"):
            v = metrics.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                return float(v)
    return None


def source_mode_for_record(record: Any) -> str:
    """Resolve the mode for a discovery record from ITS OWN characteristics."""
    return source_mode(_duration_of(record),
                       asset_kind=_asset_kind_of(record),
                       url=str(getattr(record, "canonical_url", "") or ""),
                       media_type=getattr(record, "content_type", None))


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
                   signal_peaks: Iterable[dict[str, Any]] | None = None,
                   asset_kind: Any = None, url: str | None = None,
                   max_zones: int = DEFAULT_MAX_ZONES) -> list[MomentZone]:
    """Cheaply locate candidate moments across the whole source.

    Returns ranked zones. For short-form sources the whole asset is one zone (the
    source IS the moment); for long-form the source is scanned and **the first
    zone is never assumed to be at 00:00 unless the evidence actually says so**.
    """
    segs = [s for s in (segments or []) if isinstance(s, dict)]
    mode = source_mode(source_duration, asset_kind=asset_kind, url=url)

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

    # Deterministic signal peaks join the same zone system, so a NONVERBAL event
    # (weak transcript, strong action) can still be promoted.
    zones.extend(_peak_zones(signal_peaks or [], source_id=source_id, source_url=source_url,
                             source_duration=source_duration))
    zones = _merge_aligned(zones)
    zones.sort(key=lambda z: (-z.locator_confidence, z.event_timestamp))
    for i, z in enumerate(zones[:max_zones], start=1):
        z.rank = i
    return zones[:max_zones]


def _peak_zones(peaks: Iterable[dict[str, Any]], *, source_id: str, source_url: str,
                source_duration: float | None, gap_s: float = CONTINUITY_GAP_SECONDS) -> list[MomentZone]:
    """Zones from deterministic signal peaks, with CONTINUITY clustering.

    Nearby peaks (movement begins -> event -> reaction -> resolution) are treated
    as ONE coherent moment rather than several unrelated candidates.
    """
    rows: list[tuple[float, str]] = []
    for pk in (peaks or []):
        if not isinstance(pk, dict):
            continue
        st = pk.get("start_s")
        basis = pk.get("basis")
        if st is None or not basis:
            continue
        rows.append((float(st), str(basis)))
    rows.sort(key=lambda r: r[0])
    groups: list[list[tuple[float, str]]] = []
    for st, basis in rows:
        if groups and st - groups[-1][-1][0] <= gap_s:
            groups[-1].append((st, basis))
        else:
            groups.append([(st, basis)])

    zones: list[MomentZone] = []
    for grp in groups:
        bases: list[str] = []
        for _st, b in grp:
            if b not in bases:
                bases.append(b)
        density = len(grp)                                   # action density
        alignment = len(bases)                               # distinct signal kinds
        weight = max(SIGNAL_BASIS_WEIGHTS.get(b, 0.3) for b in bases)
        # ALIGNMENT and DENSITY both raise confidence; one isolated weak blip does not.
        conf = min(1.0, weight * 0.5 + min(alignment, 3) / 3.0 * 0.3 + min(density, 4) / 4.0 * 0.2)
        start = min(st for st, _ in grp)
        end = max(st for st, _ in grp) + 4.0
        zones.append(MomentZone(
            source_id=source_id, event_timestamp=start, provisional_start=start,
            provisional_end=end,
            locator_reason=f"signal_alignment:{'+'.join(bases[:3])}",
            locator_evidence=tuple(f"signal:{b}" for b in bases),
            provenance=("deterministic_signals",),
            locator_confidence=round(conf, 4), source_duration=source_duration,
            source_url=source_url))
    return zones


def _merge_aligned(zones: list[MomentZone], *,
                   gap_s: float = CONTINUITY_GAP_SECONDS) -> list[MomentZone]:
    """Continuity: a transcript zone and a signal zone describing the same event
    become ONE zone, with the union of their evidence (alignment raises quality)."""
    zones = sorted(zones, key=lambda z: z.event_timestamp)
    merged: list[MomentZone] = []
    for z in zones:
        if merged and z.event_timestamp - merged[-1].provisional_end <= gap_s:
            prev = merged[-1]
            bases = list(prev.locator_evidence)
            for e in z.locator_evidence:
                if e not in bases:
                    bases.append(e)
            kinds = len({b.split(":", 1)[-1] for b in bases})
            conf = min(1.0, max(prev.locator_confidence, z.locator_confidence)
                       + 0.1 * max(0, kinds - 1))
            merged[-1] = MomentZone(
                source_id=prev.source_id, event_timestamp=min(prev.event_timestamp, z.event_timestamp),
                provisional_start=min(prev.provisional_start, z.provisional_start),
                provisional_end=max(prev.provisional_end, z.provisional_end),
                locator_reason=f"aligned:{'+'.join(sorted({b.split(':', 1)[-1] for b in bases})[:3])}",
                locator_evidence=tuple(bases),
                provenance=tuple(dict.fromkeys(prev.provenance + z.provenance)),
                locator_confidence=round(conf, 4),
                transcript_excerpt=prev.transcript_excerpt or z.transcript_excerpt,
                source_duration=prev.source_duration, source_url=prev.source_url)
        else:
            merged.append(z)
    return merged


def promote_zones(zones: Sequence[MomentZone], *, keep: int = DEFAULT_PROMOTE,
                  min_confidence: float = MIN_PROMOTE_CONFIDENCE) -> list[MomentZone]:
    """Rank locally and promote only zones that clear minimum confidence.

    A zone built from one isolated low-weight blip (no alignment, no density, no
    transcript cue) does NOT clear the bar, so it never consumes inspection budget.
    """
    eligible = [z for z in zones if z.locator_confidence >= min_confidence]
    return list(eligible[:keep])
