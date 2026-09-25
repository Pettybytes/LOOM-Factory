"""Bounded moment inspection — REAL content evidence, not metadata guessing.

Only candidates that already passed MARKET and TENANT FIT may enter.

Evidence order, cheapest sufficient first:
  1. provider-supplied scene/hook hints (if genuinely available)
  2. existing preview/thumbnail/cover
  3. platform captions/subtitles
  4. bounded source media segment (0-3s, extended to 8s then 12s only if needed)
  5. existing local deterministic 5B signals
  6. semantic interpretation only on survivors

A candidate may never be credited with visual activity from title, hashtags,
creator name, "viral", "crazy" or "funny" alone. If the evidence cannot support a
field, that field stays unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

WINDOWS: tuple[float, ...] = (3.0, 8.0, 12.0)

PASS = "PASS"
FAIL = "FAIL"
AMBIGUOUS = "AMBIGUOUS"

# Grounded spoken-hook cues (language the moment actually says).
POSITIVE_HOOK_TERMS: tuple[str, ...] = (
    "oh my god", "what the", "no way", "look at this", "watch this", "how",
    "are you serious", "stop", "wait", "help", "let's go", "why would you",
    "he's gonna", "she's gonna", "it's happening",
)
# Opening evidence that is a clear reason NOT to engage.
NEGATIVE_OPENING_TERMS: tuple[str, ...] = (
    "dead air", "loading", "lobby", "main menu", "menu", "starting soon",
    "subscribe", "welcome back", "thanks for watching", "intro", "sponsor",
    "brought to you by", "in this video",
)
# Deterministic 5B signal kinds that count as real activity.
ACTIVITY_KINDS: tuple[str, ...] = ("motion", "scene_change", "cut_density",
                                   "audio_energy", "audio_spike", "speech_onset")

# Signal-layer basis names -> canonical activity kinds. The extraction layer
# emits visual_motion/scene_cut/visual_change where the evidence layer expects
# motion/scene_change; normalizing AT COLLECTION time (before any assessment
# runs) is the only point that fixes activity_strength(), assess_activity and
# the confidence dimensions together.
BASIS_TO_KIND: dict[str, str] = {
    "motion": "motion", "visual_motion": "motion", "movement": "motion",
    "scene_change": "scene_change", "scene_cut": "scene_change",
    "visual_change": "scene_change", "cut_density": "cut_density",
    "audio_energy": "audio_energy", "audio_spike": "audio_spike",
    "speech_onset": "speech_onset",
}


def normalize_signal_kinds(windows: Iterable[dict[str, Any]]) -> dict[str, bool]:
    """Canonical activity kinds for a signal-window manifest (aliases resolved)."""
    kinds: dict[str, bool] = {}
    for w in (windows or []):
        if isinstance(w, dict):
            kind = BASIS_TO_KIND.get(str(w.get("basis") or "").strip().lower())
            if kind:
                kinds[kind] = True
    return kinds

PAYOFF_CUES: tuple[str, ...] = (
    "fall", "fell", "impact", "crash", "hit", "reveal", "revealed", "confrontation",
    "confronted", "punchline", "prank landed", "response", "reacted", "shocked",
    "surprised", "laughed", "lost it", " screamed", "gasp", "jumped", "ran",
)


@dataclass(frozen=True)
class FieldAssessment:
    """One grounded field: verdict, human-readable reason, and provenance."""

    verdict: str
    reason: str
    provenance: tuple[str, ...] = ()
    value: Any = None

    @property
    def passed(self) -> bool:
        return self.verdict == PASS


@dataclass
class InspectionEvidence:
    """Evidence gathered for the first seconds of one source."""

    window_seconds: float = 0.0
    preview_reference: str = ""
    visual_opening: bool = False          # frames actually examined
    caption_text: str = ""
    caption_segments: tuple[dict[str, Any], ...] = ()
    spoken_hook: str = ""
    signals: dict[str, Any] = field(default_factory=dict)   # 5B deterministic
    signal_window: tuple[float, float] | None = None
    provider_hints: dict[str, Any] = field(default_factory=dict)
    media_segment_seconds: float = 0.0
    timestamps: tuple[dict[str, Any], ...] = ()
    provenance: tuple[str, ...] = ()
    last_artifact_path: str = ""          # retained for the media-aware model pass

    def has_visual(self) -> bool:
        return bool(self.visual_opening)

    def activity_strength(self) -> float:
        """0..1 deterministic activity, from real signals ONLY."""
        if not self.signals:
            return 0.0
        hits = [k for k in ACTIVITY_KINDS if self.signals.get(k)]
        if not hits:
            return 0.0
        return round(min(1.0, len(hits) / len(ACTIVITY_KINDS)), 4)

    def as_confidence_evidence(self, *, immediate_hook: bool, payoff: bool,
                               reaction_angle: str, has_context: bool = True) -> dict[str, Any]:
        """Feed the FIRE confidence dimension set (no invented fields)."""
        return {
            "visual_opening": self.has_visual(),
            "preview": bool(self.preview_reference),
            "action_activity": self.activity_strength() > 0.0,
            "immediate_hook": bool(immediate_hook),
            "payoff_understanding": bool(payoff),
            "reaction_opportunity": bool(reaction_angle),
            "spoken_context": bool(self.caption_text or self.spoken_hook) or has_context,
            "timestamps": bool(self.timestamps),
            "provenance": bool(self.provenance),
        }


def first_look_windows() -> tuple[float, ...]:
    """The bounded inspection plan: 0-3s, extended only when needed."""
    return WINDOWS


# ---------------------------------------------------------------- assessments

def assess_stop(ev: InspectionEvidence) -> FieldAssessment:
    """Is there an immediate reason NOT to swipe? (0-3s)"""
    # A media-aware model pass that actually watched the opening is real hook
    # evidence; it is checked BEFORE the no-evidence failure so a watched clip
    # is never discarded as uninspected.
    if ev.provider_hints.get("hook"):
        return FieldAssessment(PASS, "media_model_observed_hook_at_open",
                               ("media_model",), {"hook": True})
    # No visual opening AND no audio/activity signals means significance exists
    # only in caption/title — explicit negative evidence, never a PASS.
    if not ev.has_visual() and not ev.signals:
        return FieldAssessment(FAIL, "no_visual_or_audio_evidence_at_open", ("none",))
    text = f"{ev.caption_text} {ev.spoken_hook}".lower()
    if any(t in text for t in NEGATIVE_OPENING_TERMS) and not any(t in text for t in POSITIVE_HOOK_TERMS):
        return FieldAssessment(FAIL, "static_or_setup_open_no_event", ("captions",))
    strength = ev.activity_strength()
    hook_hit = next((t for t in POSITIVE_HOOK_TERMS if t in text), "")
    if strength >= 0.4 and (hook_hit or ev.signals.get("audio_spike") or ev.signals.get("motion")):
        return FieldAssessment(PASS, "immediate_activity_and_hook_at_open",
                               ("5b_signals", "captions"), {"strength": strength, "hook": hook_hit})
    if hook_hit and ev.has_visual():
        return FieldAssessment(PASS, "spoken_hook_present_with_visual", ("captions", "frames"))
    if strength >= 0.4:
        return FieldAssessment(AMBIGUOUS, "activity_without_clear_hook", ("5b_signals",), {"strength": strength})
    return FieldAssessment(AMBIGUOUS, "insufficient_opening_evidence", ("none",))


def assess_activity(ev: InspectionEvidence) -> FieldAssessment:
    """Real activity, from deterministic signals — never from fame or titles."""
    strength = ev.activity_strength()
    if not ev.signals:
        return FieldAssessment(FAIL, "no_deterministic_signals_collected", ("none",), {"strength": 0.0})
    if strength >= 0.5:
        return FieldAssessment(PASS, "multiple_activity_signals", ("5b_signals",),
                               {"strength": strength, "window": ev.signal_window})
    if strength > 0.0:
        return FieldAssessment(AMBIGUOUS, "single_weak_activity_signal", ("5b_signals",),
                               {"strength": strength, "window": ev.signal_window})
    return FieldAssessment(FAIL, "no_activity_signals", ("5b_signals",), {"strength": 0.0})


def assess_setup(ev: InspectionEvidence) -> FieldAssessment:
    text = f"{ev.caption_text} {ev.spoken_hook}".strip()
    if not text:
        return FieldAssessment(AMBIGUOUS, "no_context_text_to_understand_setup", ("none",))
    if len(text.split()) >= 4:
        return FieldAssessment(PASS, "context_explains_the_situation", ("captions",))
    return FieldAssessment(AMBIGUOUS, "too_little_context", ("captions",))


def assess_escalation(ev: InspectionEvidence) -> FieldAssessment:
    strength = ev.activity_strength()
    if strength >= 0.6:
        return FieldAssessment(PASS, "activity_intensity_increases_in_window", ("5b_signals",))
    if ev.provider_hints.get("escalation"):
        return FieldAssessment(PASS, "provider_hint_escalation", ("provider_hints",))
    if strength > 0.0:
        return FieldAssessment(AMBIGUOUS, "activity_present_escalation_unclear", ("5b_signals",))
    return FieldAssessment(FAIL, "no_escalation_evidence", ("none",))


def assess_payoff(ev: InspectionEvidence) -> FieldAssessment:
    text = f"{ev.caption_text} {ev.spoken_hook}".lower()
    cue = next((c for c in PAYOFF_CUES if c in text), "")
    if cue:
        return FieldAssessment(PASS, f"payoff_cue:{cue}", ("captions",))
    if ev.provider_hints.get("payoff"):
        return FieldAssessment(PASS, "provider_hint_payoff", ("provider_hints",))
    if ev.signals.get("audio_spike") and ev.signal_window:
        return FieldAssessment(AMBIGUOUS, "impact_like_signal_without_semantic_payoff", ("5b_signals",))
    return FieldAssessment(FAIL, "no_payoff_evidence", ("none",))


def assess_reaction_angle(ev: InspectionEvidence) -> FieldAssessment:
    """Can Bryan naturally comment on this?"""
    text = f"{ev.caption_text} {ev.spoken_hook}".lower()
    payoff = assess_payoff(ev)
    if payoff.passed and ev.activity_strength() > 0.0:
        cue = next((c for c in PAYOFF_CUES if c in text), "event")
        return FieldAssessment(PASS, f"commentable_event:{cue}", ("captions", "5b_signals"),
                               {"angle": f"react to the {cue}"})
    if ev.provider_hints.get("reaction_angle"):
        return FieldAssessment(PASS, "provider_hint_reaction_angle", ("provider_hints",),
                               {"angle": ev.provider_hints["reaction_angle"]})
    if "tutorial" in text or "explain" in text or "how to" in text:
        return FieldAssessment(FAIL, "informational_content_no_reaction_entry", ("captions",))
    return FieldAssessment(FAIL, "no_clear_reaction_entry_point", ("none",))


# ---------------------------------------------------------------- orchestration

def inspect_bounded(record: Any, *, fetch_captions: Callable[[str], Any] | None = None,
                    fetch_signals: Callable[[str], Any] | None = None,
                    provider_hints: dict[str, Any] | None = None,
                    preview_reference: str = "") -> InspectionEvidence:
    """Collect the cheapest sufficient evidence for the first seconds.

    Never downloads whole videos: a bounded media segment is requested only when
    deterministic signals are actually needed. Failures are recorded, not fatal.
    """
    url = str(getattr(record, "canonical_url", "") or "")
    ev = InspectionEvidence(provider_hints=dict(provider_hints or {}),
                            preview_reference=preview_reference or
                            str(getattr(record, "thumbnail_reference", "") or (getattr(record, "observed_metrics", {}) or {}).get("thumbnail_url") or ""))
    prov: list[str] = []

    # 2/3. captions first (cheapest text evidence)
    if fetch_captions is not None:
        try:
            segments, _ttype = fetch_captions(url)
            if segments:
                ev.window_seconds = WINDOWS[0]
                ev.caption_text = " ".join(s.get("text", "") for s in segments[:40])[:2000]
                ev.spoken_hook = ev.caption_text[:200]
                prov.append("captions")
        except Exception as exc:  # noqa: BLE001
            prov.append(f"captions_failed:{type(exc).__name__}")

    # 5. deterministic signals (bounded media segment, existing helper)
    if fetch_signals is not None:
        try:
            windows = fetch_signals(url) or []
            if windows:
                ev.signals = normalize_signal_kinds(windows)
                starts = [w.get("start_s") for w in windows if isinstance(w, dict) and w.get("start_s") is not None]
                ends = [w.get("end_s") for w in windows if isinstance(w, dict) and w.get("end_s") is not None]
                if starts and ends:
                    ev.signal_window = (float(min(starts)), float(max(ends)))
                ev.visual_opening = True          # frames/audio in the window were examined
                ev.media_segment_seconds = WINDOWS[0]
                ev.timestamps = tuple(
                    {"start_s": w.get("start_s"), "end_s": w.get("end_s"),
                     "basis": w.get("basis")}
                    for w in windows if isinstance(w, dict) and w.get("basis"))
                prov.append("bounded_media_0_3s")
        except Exception as exc:  # noqa: BLE001
            prov.append(f"signals_failed:{type(exc).__name__}")

    if ev.preview_reference:
        prov.append("preview")
    if ev.provider_hints:
        prov.append("provider_hints")
    ev.provenance = tuple(prov)
    return ev


def populate_from_inspection(candidate: Any, ev: InspectionEvidence,
                             assessments: dict[str, FieldAssessment]) -> Any:
    """Fill the MomentCandidate moment fields that the evidence actually supports.

    Fields the evidence cannot support remain unavailable (None) — never inferred.
    """
    hook = assessments.get("stop")
    activity = assessments.get("activity")
    payoff = assessments.get("payoff")
    reaction = assessments.get("reaction_angle")
    emotion = assessments.get("emotion_event_type")
    me = dict(getattr(candidate, "moment_evidence", {}) or {})
    me.update({
        "preview_reference": ev.preview_reference or None,
        "hook_0_3s": ((hook.value or {}).get("hook") or hook.reason) if hook and hook.passed else None,
        "action_activity": (activity.value or {}).get("strength") if activity and activity.passed else None,
        "setup": assessments["setup"].reason if assessments.get("setup") and assessments["setup"].passed else None,
        "anticipation_escalation": assessments["escalation"].reason if assessments.get("escalation") and assessments["escalation"].passed else None,
        "payoff": payoff.reason if payoff and payoff.passed else None,
        "reaction_resolution": payoff.reason if payoff and payoff.passed else None,
        "emotion_event_type": emotion.value if emotion is not None else None,
        "reaction_angle": (reaction.value or {}).get("angle") if reaction and reaction.passed else None,
        "exact_timestamps": list(ev.timestamps) or None,
    })
    candidate.moment_evidence = me
    candidate.evidence_depth = "inspection_0_3s" if ev.has_visual() else ("captions_only" if ev.caption_text else "metadata_only")
    if ev.signal_window:
        candidate.moment_start, candidate.moment_end = ev.signal_window
        candidate.moment_duration = round(ev.signal_window[1] - ev.signal_window[0], 3)
    if getattr(candidate, "source_duration", None) is None and ev.window_seconds:
        candidate.source_duration = ev.window_seconds
    return candidate


def inspect_with_escalation(record: Any, *, fetch_preview: Callable[..., Any],
                            analyze_signals: Callable[..., Any] | None = None,
                            decide: Callable[[InspectionEvidence], str] | None = None,
                            windows: tuple[float, ...] = WINDOWS,
                            fetch_captions: Callable[[str], Any] | None = None,
                            max_window: float = 12.0,
                            start: float = 0.0,
                            retain_last: bool = False,
                            ) -> tuple[InspectionEvidence, tuple[dict[str, Any], ...]]:
    """0-3s first; extend to 8s then 12s ONLY while the decision is ambiguous.

    Never continues past ``max_window`` and never processes the full source.
    Returns the evidence plus a per-window attempt log for honest reporting.
    ``retain_last`` keeps the final artifact on ``ev.last_artifact_path`` for a
    media-aware model pass; the caller must then clean it up.
    """
    import time as _time
    url = str(getattr(record, "canonical_url", "") or "")
    ev = InspectionEvidence(preview_reference=str(getattr(record, "thumbnail_reference", "") or (getattr(record, "observed_metrics", {}) or {}).get("thumbnail_url") or ""))
    prov: list[str] = []
    log: list[dict[str, Any]] = []
    all_segments: list[dict[str, Any]] = []

    if fetch_captions is not None:
        try:
            segments, _t = fetch_captions(url)
            if segments:
                all_segments = [s for s in segments if isinstance(s, dict)]
                prov.append("captions")
        except Exception as exc:  # noqa: BLE001
            prov.append(f"captions_failed:{type(exc).__name__}")

    def _scope_captions(win_end: float) -> None:
        """Captions must describe the INSPECTED interval, not the whole source."""
        if not all_segments:
            return
        scoped = [s for s in all_segments
                  if float(s.get("start_s", 0.0) or 0.0) >= start - 0.001
                  and float(s.get("start_s", 0.0) or 0.0) <= start + win_end + 0.5]
        text = " ".join(x.get("text", "") for x in scoped[:40])[:2000]
        ev.caption_text = text
        ev.caption_segments = tuple(scoped)
        ev.spoken_hook = text[:200]

    for end in windows:
        if end > max_window:
            break
        started = _time.time()
        try:
            # MOMENT-RELATIVE: the window begins at the located moment's chosen
            # clip start, never at source 00:00.
            preview = fetch_preview(url, start=start, end=start + end)
        except Exception as exc:  # noqa: BLE001
            log.append({"start": start, "end": end, "error": type(exc).__name__})
            break
        entry = {"start": start, "end": end, "method": getattr(preview, "method", ""),
                 "bytes": int(getattr(preview, "artifact_bytes", 0) or 0),
                 "available": bool(getattr(preview, "available", False)),
                 "fallback": bool(getattr(preview, "fallback_used", False)),
                 "reason": getattr(preview, "reason", "")}
        # Provider metadata may round the source duration. The inspection
        # contract carries actual visual coverage separately from the request;
        # never tell the model that a missing terminal fraction was observed.
        actual_start = getattr(preview, "inspected_start", start)
        actual_end = getattr(preview, "inspected_end", start + end)
        actual_length = end
        if (isinstance(actual_start, (int, float)) and isinstance(actual_end, (int, float))
                and actual_end > actual_start and abs(actual_start - start) <= .001):
            actual_length = min(end, actual_end - start)
            entry.update(inspected_start=actual_start, inspected_end=start + actual_length)
        try:
            if analyze_signals is not None and getattr(preview, "available", False):
                manifest = analyze_signals(getattr(preview, "artifact_path"), max_seconds=int(end)) or {}
                # Canonical kinds AT COLLECTION: assessments, decide() and the
                # confidence dimensions all see the same resolved activity names.
                ev.signals = normalize_signal_kinds(manifest.get("signal_windows") or [])
                ev.timestamps = tuple({"start_s": w.get("start_s"), "end_s": w.get("end_s"),
                                       "basis": w.get("basis")}
                                      for w in (manifest.get("signal_windows") or [])
                                      if isinstance(w, dict) and w.get("basis"))
                entry["signals"] = sorted(ev.signals)
            if getattr(preview, "available", False):
                ev.visual_opening = True
                ev.window_seconds = actual_length
                ev.media_segment_seconds = actual_length
                _scope_captions(actual_length)
                prov.append(f"bounded_preview_{start:g}s+{actual_length:g}s")
            else:
                prov.append("bounded_preview_unavailable")
        except Exception as exc:  # noqa: BLE001
            entry["signal_error"] = type(exc).__name__
        finally:
            # The retained artifact lives until analysis finishes; free it now
            # unless the caller asked to keep it for a media-aware model pass.
            if retain_last:
                _old = ev.last_artifact_path
                _new = getattr(preview, "artifact_path", "") or ""
                if _old and _old != _new:
                    from .bounded_preview import cleanup
                    cleanup(_old)
                ev.last_artifact_path = _new
            else:
                from .bounded_preview import cleanup
                cleanup(preview)
        entry["duration_ms"] = int((_time.time() - started) * 1000)
        log.append(entry)

        if not getattr(preview, "available", False):
            break                      # nothing to inspect further from
        verdict = decide(ev) if decide is not None else "decisive"
        if verdict == "decisive":
            break
    return ev, tuple(log)
