"""Evidence-derived natural clip STARTS.

This module owns START discovery ONLY. It deliberately does not define or import a
boundary plan, natural-end logic, the completeness gate, or FIRE — those stay in
``moment_quality`` and are called by the existing flow exactly as before.

LOOM does not ask "how many seconds before the event should I start?". It asks
WHERE the useful version of the moment naturally begins, then checks whether the
first ~3 seconds of that proposed clip actually hook.

There is NO fixed lead ladder. ``lead_seconds`` is DERIVED telemetry, never an
input and never a constraint.

Root-cause note: a located zone's ``provisional_start`` is NOT used as a search
floor. For locator-produced zones it equals the event timestamp, which would
eliminate every earlier anchor. This module computes its own bounded search
region from the event plus evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# --- technical bounds, NOT editorial rules -------------------------------
INITIAL_HORIZON_SECONDS = 90.0        # first-pass search window (efficiency only)
MAX_HORIZON_EXPANSIONS = 3            # each expansion is deliberate and observable
HARD_SAFETY_HORIZON_SECONDS = 1800.0  # absolute stop; never traverse a whole source
HOOK_WINDOW_SECONDS = 3.0             # first-3s opening QUALITY test
CONTIGUITY_GAP_SECONDS = 20.0         # a "next unit" must nearly touch the horizon

# --- anchor cue sets ------------------------------------------------------
SETUP_CUES = ("about to", "going to", "so i", "one time", "watch this", "here's",
              "the story", "let me explain", "before", "we were")
ESCALATION_CUES = ("then", "suddenly", "all of a sudden", "started", "began",
                   "got worse", "kept going", "again", "louder", "faster")
CONFLICT_CUES = ("confronted", "argument", "yelling", "called out", "beef",
                 "tension", "fight", "clapped back")
PRANK_CUES = ("prank", "pranked", "got him", "got her", "camera")
REVEAL_CUES = ("reveal", "revealed", "turns out", "turned out", "secret", "confession")
ANTICIPATION_CUES = ("about to", "going to", "wobbl", "teeter", "slowly",
                     "here we go", "uh oh")
THOUGHT_OPENERS = ("so ", "one time", "i was", "we were", "let me", "here's")
STORY_CUES = ("story", "one time", "when i", "so we")
REACTION_CUES = ("screamed", "gasped", "laughed", "cheered", "shocked", "surprised",
                 "reacted", "cried", "ran over", "went silent", "jaw")
PAYOFF_CUES = ("fell", "fall", "impact", "crash", "revealed", "confronted", "punchline",
               "landed", "reacted", "shocked", "surprised", "laughed", "lost it",
               "finally", "it happened")
HOOK_PHRASES = ("oh my god", "no way", "what the", "look at this", "watch this")
DEAD_OPENING = ("welcome back", "thanks for watching", "subscribe", "sponsor",
                "in this video", "let's get started", "as i was saying")
ACTIVITY_BASES = frozenset({"motion", "scene_change", "audio_spike", "cut_density", "novelty"})

# Moment type guides WHICH boundaries matter. It never maps to a duration.
ANCHOR_PRIORITY: dict[str, tuple[str, ...]] = {
    "physical_action": ("action_onset", "activity_rise", "anticipation", "interaction_start",
                        "scene_transition", "sentence_start", "thought_start", "payoff"),
    "fall_accident": ("action_onset", "activity_rise", "anticipation", "sentence_start", "payoff"),
    "confrontation": ("interaction_start", "escalation_start", "speaker_turn",
                      "sentence_start", "payoff"),
    "prank": ("prank_setup", "setup_start", "anticipation", "sentence_start", "payoff"),
    "fan_interaction": ("interaction_start", "setup_start", "sentence_start", "payoff"),
    "animal_behavior": ("action_onset", "activity_rise", "sentence_start", "payoff"),
    "reaction": ("reaction_trigger", "sentence_start", "thought_start", "payoff"),
    "reveal": ("reveal_setup", "setup_start", "anticipation", "sentence_start", "payoff"),
    "funny_awkward": ("action_onset", "sentence_start", "setup_start", "payoff"),
    "emotional": ("sentence_start", "thought_start", "setup_start", "payoff"),
    "story_punchline": ("thought_start", "sentence_start", "setup_start", "story_beat", "payoff"),
    "hot_take_opinion": ("thought_start", "sentence_start", "speaker_turn", "payoff"),
    "informational_insight": ("thought_start", "sentence_start", "payoff"),
}
DEFAULT_ANCHOR_PRIORITY = ("sentence_start", "thought_start", "action_onset",
                           "activity_rise", "scene_transition", "setup_start", "payoff")

# Types where a truncated setup destroys the payoff, so expansion is more likely.
CONTEXT_SENSITIVE_TYPES = frozenset({
    "story_punchline", "reveal", "prank", "confrontation", "hot_take_opinion",
    "informational_insight", "unknown",
})


@dataclass
class StartAnchor:
    """A real editorial boundary discovered from evidence (never event-minus-N)."""

    timestamp: float
    kind: str
    reason: str
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"timestamp": self.timestamp, "kind": self.kind,
                "reason": self.reason, "evidence": list(self.evidence)}


@dataclass
class AnchorSelection:
    """Recommended start + everything needed to explain and debug it."""

    start: float
    reason: str
    moment_type: str = "unknown"
    anchors: tuple[dict[str, Any], ...] = ()
    alternatives: tuple[dict[str, Any], ...] = ()
    telemetry: dict[str, Any] = field(default_factory=dict)

    @property
    def lead_seconds(self) -> float:
        return float(self.telemetry.get("lead_seconds") or 0.0)

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "reason": self.reason, "moment_type": self.moment_type,
                "anchors": [dict(a) for a in self.anchors],
                "alternatives": [dict(a) for a in self.alternatives],
                "telemetry": dict(self.telemetry)}


# ------------------------------------------------------------------ internals

def _segs(segments: Sequence[dict[str, Any]]) -> list[tuple[float, float, str]]:
    return [(float(x.get("start_s", 0.0) or 0.0), float(x.get("end_s", 0.0) or 0.0),
             str(x.get("text") or ""))
            for x in (segments or []) if isinstance(x, dict)]


def _text_between(segments: Sequence[dict[str, Any]], start: float, end: float) -> str:
    """Text a clip starting at ``start`` would actually contain (segments BEGINNING inside)."""
    return " ".join(tx for st, _en, tx in _segs(segments) if start - 0.001 <= st <= end).strip()


def hook_at(segments: Sequence[dict[str, Any]], start: float,
            window: float = HOOK_WINDOW_SECONDS) -> tuple[float, tuple[str, ...]]:
    """First-3s OPENING QUALITY test for a proposed start (0..1 + evidence)."""
    text = _text_between(segments, start, start + window).lower()
    if not text:
        return 0.0, ()
    ev: list[str] = []
    if any(c in text for c in PAYOFF_CUES):
        ev.append("event")
    if any(c in text for c in HOOK_PHRASES):
        ev.append("hook")
    if any(c in text for c in ANTICIPATION_CUES):
        ev.append("anticipation")
    if any(c in text for c in DEAD_OPENING):
        ev.append("dead_opening")
    strength = min(1.0, len(ev) / 3.0)
    if "dead_opening" in ev:
        strength = max(0.0, strength - 0.5)
    return round(strength, 4), tuple(ev)


def dead_ratio(start: float, event: float, segments: Sequence[dict[str, Any]]) -> float:
    """Proportion of [start, event) that carries no cue at all."""
    rows = [(st, tx) for st, _en, tx in _segs(segments) if start - 0.001 <= st < event]
    if not rows:
        return 0.0
    cues = (*SETUP_CUES, *ESCALATION_CUES, *PAYOFF_CUES, *REACTION_CUES, *ANTICIPATION_CUES)
    return round(sum(1 for _st, tx in rows if not any(c in tx.lower() for c in cues))
                 / len(rows), 4)


def _collect(event: float, segs: Sequence[tuple[float, float, str]],
             signals: Sequence[dict[str, Any]], floor: float) -> dict[tuple[float, str], StartAnchor]:
    found: dict[tuple[float, str], StartAnchor] = {}

    def add(ts: float, kind: str, reason: str, ev: tuple[str, ...] = ()) -> None:
        ts = round(max(0.0, float(ts)), 3)
        if ts < floor - 0.001 or ts > event + 0.001:
            return
        found.setdefault((ts, kind), StartAnchor(ts, kind, reason, ev))

    prev_complete = True
    for st, _en, tx in segs:
        if st > event:
            break
        low = tx.lower().strip()
        if st >= floor and low:
            # A segment that OPENS with a continuation word is not a new sentence,
            # even when the previous segment ended with punctuation. Without this,
            # "and that is when it happened." outranked the real setup anchor.
            continuation = low.startswith(("and ", "but ", "so ", "then ", "because "))
            if prev_complete and not continuation:
                add(st, "sentence_start", "a sentence begins here", ("terminal_punctuation_before",))
            if any(c in low for c in SETUP_CUES):
                add(st, "setup_start", "setup/context language begins here", ("setup_cue",))
            if any(c in low for c in ESCALATION_CUES):
                add(st, "escalation_start", "escalation language begins here", ("escalation_cue",))
            if any(c in low for c in CONFLICT_CUES):
                add(st, "interaction_start", "the interaction/conflict begins here", ("conflict_cue",))
            if any(c in low for c in PRANK_CUES):
                add(st, "prank_setup", "prank setup begins here", ("prank_cue",))
            if any(c in low for c in REVEAL_CUES):
                add(st, "reveal_setup", "reveal setup begins here", ("reveal_cue",))
            if "?" in low:
                add(st, "question_start", "a question is posed before the response", ("question",))
            if any(c in low for c in ANTICIPATION_CUES):
                add(st, "anticipation", "anticipation becomes visible/audible", ("anticipation_cue",))
            if low.startswith(THOUGHT_OPENERS):
                add(st, "thought_start", "a complete thought begins here", ("thought_opener",))
            if any(c in low for c in STORY_CUES):
                add(st, "story_beat", "a story beat begins here", ("story_cue",))
        if any(c in low for c in REACTION_CUES) and st >= floor:
            prior = [x for x in segs if x[0] < st]
            if prior:
                add(prior[-1][0], "reaction_trigger", "the trigger for the reaction",
                    ("before_reaction",))
        prev_complete = tx.strip().endswith((".", "!", "?"))

    running = False
    for pk in sorted((x for x in signals if isinstance(x, dict) and x.get("start_s") is not None),
                     key=lambda x: float(x["start_s"])):
        st = float(pk["start_s"])
        if st > event or st < floor:
            continue
        basis = str(pk.get("basis") or "")
        if basis in ("motion", "novelty"):
            add(st, "action_onset", "meaningful movement begins here", (f"signal:{basis}",))
            running = True
        elif basis in ACTIVITY_BASES and running:
            add(st, "activity_rise", "sustained activity continues above baseline",
                (f"signal:{basis}",))
        elif basis == "scene_change":
            add(st, "scene_transition", "a relevant scene transition occurs here",
                (f"signal:{basis}",))
    return found


# ------------------------------------------------------------------- public API

def discover_anchors(event_timestamp: float, segments: Sequence[dict[str, Any]], *,
                     signals: Iterable[dict[str, Any]] = (),
                     moment_type: str = "unknown",
                     initial_horizon: float = INITIAL_HORIZON_SECONDS,
                     allow_expansion: bool = True,
                     ) -> tuple[list[StartAnchor], dict[str, Any]]:
    """Find natural boundaries inside a bounded window, expanding only on evidence."""
    event = float(event_timestamp)
    segs = _segs(segments)
    sigs = [x for x in (signals or []) if isinstance(x, dict)]
    floor = max(0.0, event - max(0.0, initial_horizon))
    tel: dict[str, Any] = {"event_timestamp": round(event, 3),
                           "initial_horizon_s": round(initial_horizon, 3),
                           "search_floor_s": round(floor, 3),
                           "horizon_expanded": False, "expansion_reason": "",
                           "expansions": 0}

    found = _collect(event, segs, sigs, floor)

    if allow_expansion:
        for _ in range(MAX_HORIZON_EXPANSIONS):
            # Expand ONLY for a coherent unit that CROSSES the horizon, or to the
            # immediately preceding unit when it is CONTIGUOUS with it. Jumping to
            # the earliest segment in the source is forbidden: that silently walks
            # backward through a long source instead of acquiring minimum context.
            straddling = [x for x in segs if x[0] < floor and x[1] > floor]
            prior = [x for x in segs if x[1] <= floor + 0.001 and x[0] <= event]
            contiguous = [x for x in prior if floor - x[1] <= CONTIGUITY_GAP_SECONDS]
            if straddling:
                new_floor = max(0.0, min(x[0] for x in straddling))
                reason = "coherent unit crosses the horizon"
            elif contiguous and moment_type in CONTEXT_SENSITIVE_TYPES:
                new_floor = max(0.0, max(x[0] for x in contiguous))
                reason = f"adjacent setup unit precedes the horizon for {moment_type}"
            else:
                break
            new_floor = max(new_floor, event - HARD_SAFETY_HORIZON_SECONDS)
            if new_floor >= floor - 0.001:
                break
            floor = new_floor
            tel["horizon_expanded"] = True
            tel["expansions"] += 1
            tel["expansion_reason"] = reason
            tel["search_floor_s"] = round(floor, 3)
            found = _collect(event, segs, sigs, floor)

    # If no earlier boundary exists, the bounded floor itself is the honest start,
    # and uncertainty is represented rather than a fabricated offset.
    found.setdefault((round(max(0.0, floor), 3), "search_floor"),
                     StartAnchor(round(max(0.0, floor), 3), "search_floor",
                                 "no earlier boundary evidence; bounded search floor",
                                 ("search_floor",)))
    found.setdefault((round(event, 3), "payoff"),
                     StartAnchor(round(event, 3), "payoff", "the event/payoff itself",
                                 ("event_timestamp",)))

    anchors = sorted(found.values(), key=lambda a: a.timestamp)
    tel["earliest_anchor_s"] = round(min(a.timestamp for a in anchors), 3)
    tel["anchor_count"] = len(anchors)
    return anchors, tel


def select_anchor(anchors: Sequence[StartAnchor], segments: Sequence[dict[str, Any]], *,
                  event_timestamp: float, moment_type: str = "unknown",
                  ) -> tuple[StartAnchor, str, tuple[dict[str, Any], ...]]:
    """Choose the best editorial start from evidence. No minimum or maximum lead."""
    event = float(event_timestamp)
    priority = ANCHOR_PRIORITY.get(moment_type, DEFAULT_ANCHOR_PRIORITY)
    pool = list(anchors) or [StartAnchor(max(0.0, event - 1.0), "fallback",
                                         "no anchor evidence", ())]
    scored: list[tuple[float, StartAnchor, tuple[str, ...], float]] = []
    # For context-sensitive types a truncated setup destroys the payoff, so if a
    # genuine context anchor exists, starting ON the payoff is penalised. This is
    # NOT a duration rule: it compares anchor KINDS, and it does nothing when no
    # earlier context anchor was found.
    context_kinds = ("setup_start", "thought_start", "story_beat", "sentence_start",
                     "reveal_setup", "prank_setup", "interaction_start", "escalation_start",
                     "action_onset", "reaction_trigger")
    has_context_anchor = any(a.kind in context_kinds for a in pool)
    context_sensitive = moment_type in CONTEXT_SENSITIVE_TYPES
    for a in pool:
        strength, evidence = hook_at(segments, a.timestamp)
        dead = dead_ratio(a.timestamp, event, segments)
        bonus = 0.0
        if a.kind in priority:
            bonus = 0.25 + 0.05 * max(0, len(priority) - priority.index(a.kind))
        score = strength * 0.45 + bonus + (1.0 - dead) * 0.15
        if a.kind == "payoff" and dead > 0.5:
            score -= 0.1
        if context_sensitive and a.kind == "payoff" and has_context_anchor:
            score -= 0.25
        scored.append((round(score, 4), a, evidence, dead))
    # TRUE TIE -> the EARLIER evidence-backed anchor. Dead lead-in already has its
    # own dead_ratio penalty; preferring the later anchor silently removed action.
    scored.sort(key=lambda t: (-t[0], t[1].timestamp))
    best_score, best, best_ev, _dead = scored[0]
    lead = round(event - best.timestamp, 3)
    reason = (f"{best.reason}; first-3s opening evidence: "
              f"{'yes' if best_ev else 'none'}; lead_seconds={lead} is DERIVED telemetry")
    alternatives = tuple({"start": round(a.timestamp, 3), "kind": a.kind,
                          "score": score, "hook_strength": strength,
                          "hook_evidence": list(ev), "dead_ratio": dead}
                         for score, a, ev, dead in scored)
    return best, reason, alternatives


def recommend_start(event_timestamp: float, segments: Sequence[dict[str, Any]], *,
                    signals: Iterable[dict[str, Any]] = (),
                    moment_type: str = "unknown",
                    initial_horizon: float = INITIAL_HORIZON_SECONDS,
                    allow_expansion: bool = True) -> AnchorSelection:
    """Full start decision: anchors -> selection -> derived telemetry."""
    event = float(event_timestamp)
    anchors, tel = discover_anchors(event, segments, signals=signals,
                                    moment_type=moment_type,
                                    initial_horizon=initial_horizon,
                                    allow_expansion=allow_expansion)
    best, reason, alternatives = select_anchor(anchors, segments, event_timestamp=event,
                                               moment_type=moment_type)
    strength, evidence = hook_at(segments, best.timestamp)
    tel.update({
        "selected_start_s": round(best.timestamp, 3),
        "selected_start_kind": best.kind,
        "selected_start_reason": best.reason,
        "lead_seconds": round(event - best.timestamp, 3),
        "first_3s_hook_strength": strength,
        "first_3s_hook_evidence": list(evidence),
        "dead_ratio": dead_ratio(best.timestamp, event, segments),
    })
    return AnchorSelection(start=round(best.timestamp, 3), reason=reason, moment_type=moment_type,
                           anchors=tuple(a.as_dict() for a in anchors),
                           alternatives=alternatives, telemetry=tel)
