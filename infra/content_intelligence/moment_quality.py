"""Moment quality layer: type, boundaries, completeness, relative rank, dedup.

Structural, deterministic, evidence-driven. No new LLM stage, no invented virality
probability, no comparator weights (LOOM's own approval/performance history is the
future truth source). Ambiguity is represented honestly — ``unknown`` is a valid,
useful answer, never a failure to try.

Why it exists: the locator can find candidate zones but cannot yet tell that a
talk-zone and a physical-event zone imply DIFFERENT editing and ranking behaviour.
That is fixed structurally here, not by tuning fixture weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------- moment type

T_PHYSICAL = "physical_action"
T_FALL = "fall_accident"
T_CONFRONTATION = "confrontation"
T_PRANK = "prank"
T_FAN = "fan_interaction"
T_ANIMAL = "animal_behavior"
T_REACTION = "reaction"
T_REVEAL = "reveal"
T_FUNNY = "funny_awkward"
T_EMOTIONAL = "emotional"
T_STORY = "story_punchline"
T_HOT_TAKE = "hot_take_opinion"
T_INFO = "informational_insight"
T_UNKNOWN = "unknown"

MOMENT_TYPES: tuple[str, ...] = (
    T_PHYSICAL, T_FALL, T_CONFRONTATION, T_PRANK, T_FAN, T_ANIMAL, T_REACTION,
    T_REVEAL, T_FUNNY, T_EMOTIONAL, T_STORY, T_HOT_TAKE, T_INFO, T_UNKNOWN,
)

# Types whose visible event can precede the spoken reaction.
VISUAL_FIRST_TYPES = frozenset({T_PHYSICAL, T_FALL, T_PRANK, T_FAN, T_ANIMAL, T_REVEAL})
# Types driven by coherent speech: boundaries follow thought/sentence structure.
SPEECH_TYPES = frozenset({T_STORY, T_HOT_TAKE, T_INFO, T_CONFRONTATION})

_TYPE_CUES: dict[str, tuple[str, ...]] = {
    T_FALL: ("fell", "fall", "tripped", "slipped", "collapsed", "dropped"),
    T_PHYSICAL: ("hit", "crash", "impact", "threw", "jumped", "ran", "broke", "smash"),
    T_CONFRONTATION: ("confronted", "argument", "yelling", "called out", "beef",
                      "clapped back", "tension", "fight"),
    T_PRANK: ("prank", "pranked", "got him", "got her", "setup"),
    T_FAN: ("fan", "autograph", "signed", "meeting a fan", "recognized me"),
    T_ANIMAL: ("dog", "cat", "puppy", "kitten", "animal", "horse", "monkey"),
    T_REACTION: ("reacted", "reaction", "responded to", "his face", "her face"),
    T_REVEAL: ("revealed", "turned out", "secret", "exposed", "confession", "admitted"),
    T_FUNNY: ("awkward", "cringe", "embarrassing", "laughed", "hilarious"),
    T_EMOTIONAL: ("crying", "emotional", "tears", "heartbreaking", "furious", "terrified"),
    T_STORY: ("so i", "one time", "story", "then i", "and then"),
    T_HOT_TAKE: ("i think", "the truth is", "honestly", "unpopular opinion", "nobody talks about"),
    T_INFO: ("how to", "explains", "because", "the reason", "here's why", "tutorial"),
}

ACTIVITY_BASES = frozenset({"motion", "scene_change", "audio_spike", "cut_density", "novelty"})
PAYOFF_CUES = ("fell", "impact", "crash", "revealed", "confronted", "punchline", "landed",
               "reacted", "shocked", "surprised", "laughed", "lost it", "finally", "it happened")
SETUP_CUES = ("about to", "going to", "so i", "one time", "watch this", "here's", "the story",
              "let me explain", "before")
# The reaction/resolution is part of the moment for visual-first events, even when
# no "payoff" keyword is present.
REACTION_CUES = ("screamed", "gasped", "laughed", "cheered", "shocked", "surprised",
                 "reacted", "cried", "ran over", "everyone", "went silent", "jaw")


@dataclass
class MomentTypeResult:
    moment_type: str = T_UNKNOWN
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    ambiguous: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"moment_type": self.moment_type, "type_confidence": self.confidence,
                "type_evidence": list(self.evidence),
                "type_provenance": list(self.provenance), "type_ambiguous": self.ambiguous}


def classify_moment_type(*, transcript_text: str = "", signal_bases: Iterable[str] = (),
                         speaker_count: int | None = None,
                         prompt_titles: Iterable[str] = ()) -> MomentTypeResult:
    """Classify from EXISTING evidence. No single basis forces a type."""
    text = " ".join(str(x) for x in (transcript_text, *prompt_titles)).lower()
    bases = {str(b) for b in (signal_bases or ()) if b}
    visual_activity = len(bases & ACTIVITY_BASES)

    hits: dict[str, list[str]] = {}
    for mtype, cues in _TYPE_CUES.items():
        found = [c for c in cues if c in text]
        if found:
            hits[mtype] = found
    # Multimodal corroboration: a visual event with almost no speech is physical,
    # even when no keyword appears (no single basis forces it; two must agree).
    if visual_activity >= 2 and not hits:
        if bases & {"motion", "scene_change", "audio_spike"}:
            return MomentTypeResult(T_PHYSICAL, 0.5,
                                    tuple(f"signal:{b}" for b in sorted(bases & ACTIVITY_BASES)),
                                    ("deterministic_signals",), True)
    if not hits:
        return MomentTypeResult(T_UNKNOWN, 0.0, (), ("no_matching_evidence",), True)

    ranked = sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    best, best_hits = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    evidence = [f"cue:{c}" for c in best_hits[:4]]
    provenance = ["transcript_or_prompt", "deterministic_rules"]
    conf = min(1.0, 0.35 + 0.2 * len(best_hits))
    if visual_activity and best in VISUAL_FIRST_TYPES:
        conf = min(1.0, conf + 0.15)
        evidence.append(f"signals:{visual_activity}")
    ambiguous = bool(second and len(second[1]) == len(best_hits))
    if ambiguous:
        conf = round(conf * 0.6, 4)
        evidence.append(f"also:{second[0]}")
        if conf < 0.45:
            return MomentTypeResult(T_UNKNOWN, round(conf, 4), tuple(evidence),
                                    tuple(provenance), True)
    if speaker_count is not None and speaker_count >= 2 and best == T_CONFRONTATION:
        conf = min(1.0, conf + 0.1)
        evidence.append(f"speakers:{speaker_count}")
    return MomentTypeResult(best, round(conf, 4), tuple(evidence), tuple(provenance), ambiguous)


# ------------------------------------------------------------ boundaries

@dataclass
class StartAlternative:
    start: float
    rationale: str
    hook_strength: float
    hook_evidence: tuple[str, ...] = ()


@dataclass
class BoundaryPlan:
    event_timestamp: float
    recommended_clip_start: float
    recommended_clip_end: float
    start_reason: str
    end_reason: str
    moment_type: str = T_UNKNOWN
    start_alternatives: tuple[StartAlternative, ...] = ()
    natural_event_onset: float | None = None
    start_anchors: tuple[dict[str, Any], ...] = ()
    search_telemetry: dict[str, Any] = field(default_factory=dict)
    provenance: tuple[str, ...] = ()

    @property
    def lead_seconds(self) -> float:
        """DERIVED telemetry. Never an input, never a constraint."""
        return round(max(0.0, self.event_timestamp - self.recommended_clip_start), 3)

    @property
    def duration(self) -> float:
        """Derived. A stored-but-unset duration silently broke the mid-action rule."""
        return round(self.recommended_clip_end - self.recommended_clip_start, 3)

    def as_dict(self) -> dict[str, Any]:
        return {"event_timestamp": self.event_timestamp,
                "recommended_clip_start": self.recommended_clip_start,
                "recommended_clip_end": self.recommended_clip_end,
                "start_reason": self.start_reason, "end_reason": self.end_reason,
                "duration": round(self.recommended_clip_end - self.recommended_clip_start, 3),
                "moment_type": self.moment_type,
                "natural_event_onset": self.natural_event_onset,
                "candidate_start_anchors": [dict(a) for a in self.start_anchors],
                "lead_seconds": self.lead_seconds,
                "search_telemetry": dict(self.search_telemetry),
                "start_alternatives": [
                    {"start": a.start, "rationale": a.rationale,
                     "hook_strength": a.hook_strength, "hook_evidence": list(a.hook_evidence)}
                    for a in self.start_alternatives],
                "provenance": list(self.provenance)}


def _seg_texts_between(segments: Sequence[dict[str, Any]], start: float, end: float) -> list[str]:
    out = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        s = float(seg.get("start_s", 0.0) or 0.0)
        if start - 0.001 <= s <= end:
            out.append(str(seg.get("text") or ""))
    return out


def _hook_at(segments: Sequence[dict[str, Any]], start: float, window: float = 3.0) -> tuple[float, tuple[str, ...]]:
    text = " ".join(_seg_texts_between(segments, start, start + window)).lower()
    if not text:
        return 0.0, ()
    ev: list[str] = []
    if any(c in text for c in PAYOFF_CUES):
        ev.append("event")
    if any(c in text for c in ("oh my god", "no way", "what the", "look at this", "watch this")):
        ev.append("hook")
    if any(c in text for c in SETUP_CUES):
        ev.append("anticipation")
    if any(c in text for c in ("welcome back", "thanks for watching", "subscribe", "sponsor")):
        ev.append("dead_opening")
    strength = min(1.0, len(ev) / 3.0)
    if "dead_opening" in ev:
        strength = max(0.0, strength - 0.5)
    return round(strength, 4), tuple(ev)


def type_aware_start(moment_type: str, event_timestamp: float, segments: Sequence[dict[str, Any]], *,
                     signals: Iterable[dict[str, Any]] = (),
                     duration_hint: float | None = None,
                     ) -> tuple[float, str, tuple[StartAlternative, ...]]:
    """DELEGATES start discovery to ``start_anchors`` (no fixed lead ladder)."""
    from .start_anchors import recommend_start
    sel = recommend_start(event_timestamp, segments, signals=signals, moment_type=moment_type)
    _LAST_START_TELEMETRY.clear()
    _LAST_START_TELEMETRY.update(sel.telemetry)
    alts = tuple(StartAlternative(float(a.get("start")), str(a.get("kind") or ""),
                                  float(a.get("score") or 0.0),
                                  tuple(a.get("hook_evidence") or ()))
                 for a in sel.alternatives)
    return sel.start, sel.reason, alts


_LAST_START_TELEMETRY: dict[str, Any] = {}


def _first_at_or_after(values: Any, floor: float) -> float | None:
    """The FIRST qualifying boundary at/after ``floor`` - never the source's last."""
    vals = sorted(float(v) for v in values if v is not None and float(v) >= floor - 0.001)
    return vals[0] if vals else None


def _last_at_or_before(values: Any, ceiling: float) -> float | None:
    """The nearest qualifying boundary at/before ``ceiling`` (a REAL boundary)."""
    vals = sorted(float(v) for v in values if v is not None and float(v) <= ceiling + 0.001)
    return vals[-1] if vals else None


def _contiguous_cue_run_end(after: Sequence[tuple[float, float, str]], anchor: float, *,
                            resolution_window: float, max_gap: float = 2.5) -> float | None:
    """End of the payoff/reaction run, BOUNDED by the moment's own timescale.

    A reaction resolves within a small multiple of the action's own duration - that
    is evidence about THIS moment, not a global duration cap. Without this bound, a
    long compilation with near-continuous caption cues extended a single moment by
    130+ seconds because "cue follows cue" never closes.
    """
    started = False
    run_end: float | None = None
    prev_end: float | None = None
    for st, en, tx in after:
        if en < anchor - 0.001:
            continue
        if st > anchor + resolution_window:
            break                          # beyond plausible resolution for this moment
        low = tx.lower()
        has_cue = bool(tx) and any(c in low for c in (*PAYOFF_CUES, *REACTION_CUES))
        if has_cue:
            if started and prev_end is not None and (st - prev_end) > max_gap:
                break                      # a real gap ends the run
            run_end, prev_end, started = en, en, True
        elif started:
            break                          # a cue-less segment closes the run
    return run_end


def _resolution_window(after: Sequence[tuple[float, float, str]], anchor: float) -> float:
    """Evidence-derived: how long may THIS moment's reaction plausibly take?

    Scaled from the action segment's own duration, floor 6s, ceiling 20s.
    """
    action_dur = 4.0
    for st, en, tx in after:
        if st <= anchor + 0.001 and en >= anchor - 0.001:
            action_dur = max(0.5, en - st)
            break
    return min(20.0, max(6.0, action_dur * 3.0))


def natural_end(moment_type: str, event_timestamp: float, clip_start: float,
                segments: Sequence[dict[str, Any]], *, signals: Iterable[dict[str, Any]] = (),
                duration_hint: float | None = None) -> tuple[float, str]:
    """Where does THIS moment naturally finish?

    Contract: inspect FORWARD from the event and select the FIRST natural boundary
    that completes the moment. Never the last boundary in the source, never an
    unrelated later segment. No arbitrary duration cap - evidence decides.
    """
    event = float(event_timestamp)
    texts = [(float(x.get("start_s", 0.0) or 0.0), float(x.get("end_s", 0.0) or 0.0),
              str(x.get("text") or "")) for x in (segments or []) if isinstance(x, dict)]
    after = [t for t in texts if t[0] >= clip_start - 0.001]

    payoff_at = [st for st, _en, tx in after if tx and any(c in tx.lower() for c in PAYOFF_CUES)]
    payoff_start = _first_at_or_after(payoff_at, event)
    anchor = payoff_start if payoff_start is not None else event

    sentence_ends = [en for _st, en, tx in after if tx.strip().endswith((".", "!", "?"))]
    window = _resolution_window(after, anchor)
    peak_raw = _first_at_or_after(
        [float(w.get("end_s", 0.0) or 0.0) for w in (signals or []) if isinstance(w, dict)], anchor)
    peak_end = peak_raw if (peak_raw is not None and peak_raw <= anchor + window) else None

    if moment_type in VISUAL_FIRST_TYPES:
        run_end = _contiguous_cue_run_end(after, anchor, resolution_window=window)
        base = max(event + 3.0, anchor + 1.0, run_end or 0.0, peak_end or 0.0)
        reason = "resolved_after_impact"
    else:
        # Speech: complete the sentence that carries the payoff. If no boundary
        # follows, snap to the nearest REAL boundary at/before it.
        sentence_end = (_first_at_or_after(sentence_ends, anchor)
                        or _last_at_or_before(sentence_ends, anchor + 0.001))
        if moment_type == T_STORY:
            base = max(event + 4.0, anchor + 3.0, sentence_end or 0.0)
            reason = "punchline_and_sentence_complete"
        elif moment_type in SPEECH_TYPES:
            base = max(event + 1.0, sentence_end if sentence_end is not None else event + 2.0)
            reason = "sentence_boundary"
        else:
            base = max(event + 1.0, sentence_end if sentence_end is not None else event + 3.0)
            reason = "natural_close"
    end = min(base, float(duration_hint)) if duration_hint else base
    return round(end, 3), reason


def build_boundary_plan(moment_type: str, event_timestamp: float,
                        segments: Sequence[dict[str, Any]], *, signals: Iterable[dict[str, Any]] = (),
                        duration_hint: float | None = None) -> BoundaryPlan:
    start, start_reason, alts = type_aware_start(moment_type, event_timestamp, segments,
                                                 signals=signals,
                                                 duration_hint=duration_hint)
    end, end_reason = natural_end(moment_type, event_timestamp, start, segments,
                                  signals=signals, duration_hint=duration_hint)
    telemetry = dict(_LAST_START_TELEMETRY)
    return BoundaryPlan(float(event_timestamp), start, end, start_reason, end_reason,
                        moment_type=moment_type, start_alternatives=alts,
                        natural_event_onset=telemetry.get("earliest_anchor_s"),
                        start_anchors=tuple({"timestamp": a.start,
                                             "kind": a.rationale,
                                             "score": a.hook_strength,
                                             "evidence": list(a.hook_evidence)}
                                            for a in alts),
                        search_telemetry=telemetry,
                        provenance=("evidence_anchors", "natural_end"))


# ------------------------------------------------------------ completeness

C_INCOMPLETE_SENTENCE = "INCOMPLETE_SENTENCE"
C_MISSING_SETUP = "MISSING_SETUP"
C_MISSING_PAYOFF = "MISSING_PAYOFF"
C_EVENT_UNRESOLVED = "EVENT_UNRESOLVED"
C_CUT_MID_ACTION = "CUT_MID_ACTION"
C_REACTION_TRUNCATED = "REACTION_TRUNCATED"
C_INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
C_MID_THOUGHT_START = "MID_THOUGHT_START"


@dataclass
class CompletenessResult:
    verdict: str                       # PASS | FAIL | AMBIGUOUS
    reasons: tuple[str, ...] = ()
    detail: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    @property
    def failed(self) -> bool:
        return self.verdict == "FAIL"

    def as_dict(self) -> dict[str, Any]:
        return {"completeness_verdict": self.verdict, "completeness_reasons": list(self.reasons),
                "completeness_detail": list(self.detail)}


def completeness_gate(plan: BoundaryPlan, segments: Sequence[dict[str, Any]], *,
                      signals: Iterable[dict[str, Any]] = ()) -> CompletenessResult:
    """Is the proposed clip a coherent standalone asset? Separate from FIRE."""
    reasons: list[str] = []
    detail: list[str] = []
    lo, hi = plan.recommended_clip_start, plan.recommended_clip_end
    allsegs = [(float(seg.get("start_s", 0.0) or 0.0), float(seg.get("end_s", 0.0) or 0.0),
                str(seg.get("text") or ""))
               for seg in (segments or []) if isinstance(seg, dict)]
    # CONTAINMENT, not start-time: a segment that begins inside the clip but runs
    # past its end means the clip stops MID-UTTERANCE. Selecting by start alone
    # made a half-covered sentence look complete.
    inside = [t for t in allsegs if lo - 0.001 <= t[0] and t[1] <= hi + 0.001]
    cut_tail = [t for t in allsegs if t[0] <= hi - 0.001 and t[1] > hi + 0.001]
    text = " ".join(t[2] for t in inside).lower()
    before = [t for t in (segments or []) if isinstance(t, dict)
              and float(t.get("start_s", 0.0) or 0.0) < plan.recommended_clip_start]

    # sentence cut off at the END (either a half-covered segment, or no terminal punctuation)
    if cut_tail:
        reasons.append(C_INCOMPLETE_SENTENCE); detail.append("clip ends mid-utterance")
    elif inside and not inside[-1][2].strip().endswith((".", "!", "?")):
        reasons.append(C_INCOMPLETE_SENTENCE); detail.append("ends without terminal punctuation")
    # mid-thought START (speech types)
    if plan.moment_type in SPEECH_TYPES:
        first = inside[0][2].strip().lower() if inside else ""
        if first.startswith(("and ", "but ", "so ", "because ", "then ")):
            reasons.append(C_MID_THOUGHT_START); detail.append("starts mid-thought")
    # setup for types that need it
    if plan.moment_type in ({T_STORY, T_REVEAL, T_PRANK} | SPEECH_TYPES):
        if not any(c in f"{text} {' '.join(str(b.get('text', '')).lower() for b in before)}"
                   for c in SETUP_CUES) and not before:
            reasons.append(C_MISSING_SETUP); detail.append("no setup evidence before/inside")
    # payoff
    if not any(c in text for c in PAYOFF_CUES):
        reasons.append(C_MISSING_PAYOFF); detail.append("no payoff cue inside the clip")
    # unresolved event: activity still above baseline at the clip end
    active_at_end = any(isinstance(w, dict) and float(w.get("end_s", 0.0) or 0.0) >= plan.recommended_clip_end - 0.5
                        for w in (signals or ()))
    if active_at_end and plan.moment_type in VISUAL_FIRST_TYPES:
        reasons.append(C_EVENT_UNRESOLVED); detail.append("signals still active at clip end")
    elif active_at_end:
        reasons.append(C_REACTION_TRUNCATED); detail.append("reaction may be cut")
    # mid-action cut for physical types
    if plan.moment_type in VISUAL_FIRST_TYPES and (plan.duration < 4.0 or cut_tail):
        reasons.append(C_CUT_MID_ACTION); detail.append("physical action cut before resolution")
    # context
    if len(text.split()) < 3:
        reasons.append(C_INSUFFICIENT_CONTEXT); detail.append("too little context")

    verdict = "PASS" if not reasons else "FAIL"
    if reasons and len(reasons) <= 1 and not inside:
        verdict = "AMBIGUOUS"
    return CompletenessResult(verdict, tuple(dict.fromkeys(reasons)), tuple(detail))


def repair_boundaries(plan: BoundaryPlan, gate: CompletenessResult,
                      segments: Sequence[dict[str, Any]], *, signals: Iterable[dict[str, Any]] = (),
                      max_attempts: int = 3, max_extend_s: float = 12.0,
                      duration_hint: float | None = None) -> tuple[BoundaryPlan, CompletenessResult, tuple[dict[str, Any], ...]]:
    """Bounded repair: extend/retract a little, then re-evaluate. Never unbounded."""
    attempts: list[dict[str, Any]] = []
    best_plan, best_gate = plan, gate
    if gate.passed:
        return best_plan, best_gate, tuple(attempts)
    current = plan
    for i in range(1, max_attempts + 1):
        step = min(max_extend_s, 4.0 * i)
        cand = BoundaryPlan(
            event_timestamp=current.event_timestamp,
            recommended_clip_start=current.recommended_clip_start,
            recommended_clip_end=(min(current.recommended_clip_end + 4.0, float(duration_hint))
                                  if duration_hint else current.recommended_clip_end + 4.0),
            start_reason=current.start_reason + f"+repair_end_{i}",
            end_reason=current.end_reason + f"+repair_end_{i}",
            moment_type=current.moment_type,
            start_alternatives=current.start_alternatives,
            natural_event_onset=current.natural_event_onset,
            start_anchors=current.start_anchors,
            search_telemetry=dict(current.search_telemetry,
                                  repaired=f"extend_end_{i}"),
            provenance=tuple(current.provenance) + (f"bounded_repair_end_+4s_try{i}",))
        g = completeness_gate(cand, segments, signals=signals)
        attempts.append({"attempt": i, "action": "extend_end", "extended_s": 4.0,
                         "new_end": cand.recommended_clip_end, "verdict": g.verdict,
                         "reasons": list(g.reasons)})
        current, best_gate = cand, g
        if g.passed:
            best_plan = cand
            break
    return current, best_gate, tuple(attempts)


# ------------------------------------------------------- rank + dedup

@dataclass
class RankInput:
    fingerprint: str
    event_timestamp: float
    moment_type: str = T_UNKNOWN
    market: int = 0
    content: int = 0
    selection: int = 0
    confidence: int = 0
    hook: bool = False
    activity: bool = False
    payoff: bool = False
    completeness: bool = False
    reaction_angle: bool = False
    overlap_start: float | None = None
    overlap_end: float | None = None


@dataclass
class Ranking:
    fingerprint: str
    source_relative_rank: int
    source_candidate_count: int
    rank_evidence: dict[str, Any] = field(default_factory=dict)
    duplicate_state: str = "DISTINCT"

    def as_dict(self) -> dict[str, Any]:
        return {"fingerprint": self.fingerprint,
                "source_relative_rank": self.source_relative_rank,
                "source_candidate_count": self.source_candidate_count,
                "rank_evidence": dict(self.rank_evidence),
                "duplicate_state": self.duplicate_state}


def _rank_score(c: RankInput) -> tuple:
    """Evidence-driven composite. No fake 'viral probability'.

    Physical events may outrank talk-only candidates WHEN the evidence justifies
    it -- not because of their type.
    """
    completeness = 1 if c.completeness else 0
    bools = sum(1 for b in (c.hook, c.activity, c.payoff, c.reaction_angle) if b)
    primary = (c.selection * 0.35 + c.content * 0.25 + c.confidence * 0.20 + c.market * 0.20)
    return (completeness, bools, round(primary, 4), -c.event_timestamp, c.fingerprint)


def relative_rank(candidates: Sequence[RankInput]) -> list[Ranking]:
    """Rank surviving candidates WITHIN one source. Does not replace FIRE."""
    ordered = sorted(candidates, key=_rank_score, reverse=True)
    total = len(ordered)
    out: list[Ranking] = []
    for i, cand in enumerate(ordered, start=1):
        out.append(Ranking(cand.fingerprint, i, total,
                           {"selection": cand.selection, "content": cand.content,
                            "confidence": cand.confidence, "market": cand.market,
                            "hook": cand.hook, "activity": cand.activity, "payoff": cand.payoff,
                            "reaction_angle": cand.reaction_angle,
                            "completeness": cand.completeness,
                            "moment_type": cand.moment_type}))
    return out


def _overlap_ratio(a: RankInput, b: RankInput) -> float:
    if None in (a.overlap_start, a.overlap_end, b.overlap_start, b.overlap_end):
        return 0.0
    lo = max(a.overlap_start, b.overlap_start)
    hi = min(a.overlap_end, b.overlap_end)
    if hi <= lo:
        return 0.0
    shortest = min(a.overlap_end - a.overlap_start, b.overlap_end - b.overlap_start)
    return round((hi - lo) / shortest, 4) if shortest > 0 else 0.0


def dedup(candidates: Sequence[RankInput], *, overlap_threshold: float = 0.6,
          gap_s: float = 6.0) -> list[Ranking]:
    """Collapse near-duplicate cuts of the SAME event; keep distinct events.

    One strong card per real moment -- not one card per source. Lineage is kept:
    suppressed candidates are marked, never deleted.
    """
    ordered = sorted(candidates, key=_rank_score, reverse=True)
    rankings = relative_rank(candidates)
    by_fp = {r.fingerprint: r for r in rankings}
    primary_at: list[RankInput] = []
    for cand in ordered:
        merged = False
        for prim in primary_at:
            ratio = _overlap_ratio(cand, prim)
            close = (cand.overlap_start is not None and prim.overlap_start is not None
                     and abs(cand.overlap_start - prim.overlap_start) <= gap_s)
            if ratio >= overlap_threshold or (close and ratio > 0.0):
                by_fp[cand.fingerprint].duplicate_state = "DUPLICATE_SUPPRESSED"
                by_fp[cand.fingerprint].rank_evidence["suppressed_by"] = prim.fingerprint
                by_fp[cand.fingerprint].rank_evidence["overlap_ratio"] = ratio
                merged = True
                break
        if not merged:
            by_fp[cand.fingerprint].duplicate_state = "PRIMARY"
            primary_at.append(cand)
    # A primary that suppressed nothing is a DISTINCT moment in its own right;
    # one that absorbed duplicates is the PRIMARY representative of its event.
    suppressed = {r.rank_evidence.get("suppressed_by") for r in by_fp.values()
                  if r.duplicate_state == "DUPLICATE_SUPPRESSED"}
    for r in by_fp.values():
        if r.duplicate_state == "PRIMARY" and r.fingerprint not in suppressed:
            r.duplicate_state = "DISTINCT"
    return [by_fp[r.fingerprint] for r in rankings]
