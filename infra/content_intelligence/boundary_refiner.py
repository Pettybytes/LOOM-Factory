"""Deterministic boundary refinement for an ALREADY-SELECTED candidate moment.

This is NOT candidate discovery and NOT semantic reranking. A candidate has
already been chosen (with approximate start/end); this module decides where its
boundaries should actually sit, deterministically and locally, at **$0** with no
model call.

## Timestamp primitive (locked 2026-09-20 — see CURRENT_STATE.md §16)

1. LOOM's existing transcription/VAD/decoder remains authoritative for transcript
   CONTENT. WhisperX's VAD/decoder path is never used to produce transcript text
   (5C.1 measured content loss/hallucination on 3 of 7 speech clips).
2. WhisperX wav2vec2 alignment is SUPPLEMENTAL word-level timing evidence over
   LOOM's existing transcript.
3. Aligned timestamps are NOT ground truth and never alter transcript content.
4. If alignment fails, is implausible, loses correspondence or is unavailable,
   refinement falls back safely to existing transcript/segment timing.
5. DeepFilterNet is excluded from the automatic processing path.
6. Segment 5B audio/visual signal windows remain event/salience evidence: they
   may preserve a bounded nonverbal tail, but they never replace speech timing.

## What the refiner will not do

* It never invents or rewrites transcript content — it only moves edges.
* It never allows an alignment anomaly to move a boundary arbitrarily far: every
  change is bounded, and implausible aligned spans are rejected outright.
* It never cuts a word in half when word timing is available.
* It never produces negative time or a boundary past the media duration.

Thresholds below are deliberately hand-picked from the 5C.1 / 5B evidence and are
NOT claimed to be scientifically optimal (see `prove_boundary_refiner.py`).
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Sequence


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BoundaryRefineConfig:
    """Bounded, explicit refinement policy. Every limit is a hard guardrail."""

    # A silence gap at least this long reads as a phrase/thought boundary.
    phrase_gap_s: float = 0.45
    # Silence longer than this at an edge is "dead air" and may be trimmed.
    leading_silence_s: float = 0.70
    trailing_silence_s: float = 0.70
    # How far from the candidate we will look for a phrase edge at all.
    edge_search_s: float = 8.0
    # Hard caps on how far a boundary may move from the ORIGINAL candidate.
    max_expand_s: float = 5.0
    max_contract_s: float = 5.0
    # A refined clip may never be shorter than this.
    min_duration_s: float = 1.0
    # Bounded post-speech nonverbal tail preserved when 5B evidence supports it.
    event_tail_max_s: float = 2.5
    event_tail_pad_s: float = 0.35
    # Small pads so edges do not land exactly on a sample.
    start_pad_s: float = 0.05
    end_pad_s: float = 0.05

    # ---- alignment sanity (5C.1: "mean" was aligned across 3.36 s) ----
    # A single word spanning longer than this is implausible speech timing.
    implausible_word_s: float = 1.5
    # Median disagreement with existing transcript timing above this => reject.
    max_timing_disagreement_s: float = 0.75
    # Fraction of aligned words that must correspond to existing words.
    min_correspondence: float = 0.60
    # Minimum matched words before disagreement can be judged at all.
    min_matched_for_judgement: int = 3


# 5B bases that may justify preserving a nonverbal tail.
_EVENT_BASES = ("audio_spike", "visual_motion", "scene_cut")


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class RefinedBoundary:
    start_s: float
    end_s: float
    original_start_s: float
    original_end_s: float
    delta_start_s: float
    delta_end_s: float
    start_reason: str
    end_reason: str
    evidence: list[str] = field(default_factory=list)
    fallback: str = ""
    alignment_used: bool = False
    timing_source: str = "transcript"
    event_tail_s: float = 0.0
    clamped: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_start_s": self.original_start_s,
            "original_end_s": self.original_end_s,
            "start_s": self.start_s,
            "end_s": self.end_s,
            "delta_start_s": self.delta_start_s,
            "delta_end_s": self.delta_end_s,
            "duration_s": self.duration_s,
            "start_reason": self.start_reason,
            "end_reason": self.end_reason,
            "evidence": list(self.evidence),
            "fallback": self.fallback,
            "alignment_used": self.alignment_used,
            "timing_source": self.timing_source,
            "event_tail_s": self.event_tail_s,
            "clamped": self.clamped,
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _norm(token: str) -> str:
    return re.sub(r"[^a-z0-9']", "", str(token).lower())


def _clean_words(rows: Sequence[dict], media_duration: float | None,
                 config: BoundaryRefineConfig) -> tuple[list[dict], list[str]]:
    """Keep only words with coherent spans; report why anything was dropped."""
    kept: list[dict] = []
    notes: list[str] = []
    dropped_span = dropped_bounds = dropped_order = 0
    for w in rows or ():
        if not isinstance(w, dict):
            continue
        s, e = _num(w.get("start")), _num(w.get("end"))
        if s is None or e is None:
            dropped_order += 1
            continue
        if e <= s:
            dropped_order += 1
            continue
        if s < 0 or (media_duration is not None and e > media_duration + 1e-6):
            dropped_bounds += 1
            continue
        if (e - s) > config.implausible_word_s:
            dropped_span += 1
            continue
        kept.append({"word": str(w.get("word", "")), "start": s, "end": e})
    kept.sort(key=lambda w: w["start"])
    if dropped_span:
        notes.append(f"dropped_{dropped_span}_implausible_spans")
    if dropped_bounds:
        notes.append(f"dropped_{dropped_bounds}_out_of_bounds")
    if dropped_order:
        notes.append(f"dropped_{dropped_order}_incoherent_spans")
    return kept, notes


def _correspondence(a: list[dict], b: list[dict]) -> tuple[int, float]:
    """Matched word count and fraction, by text sequence."""
    ta = [_norm(w["word"]) for w in a]
    tb = [_norm(w["word"]) for w in b]
    sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
    matched = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in sm.get_opcodes() if tag == "equal")
    denom = max(1, min(len(ta), len(tb)))
    return matched, matched / denom


def _resolve_timing(words: Sequence[dict], aligned_words: Sequence[dict] | None,
                    media_duration: float | None,
                    config: BoundaryRefineConfig) -> tuple[list[dict], str, str, list[str]]:
    """Decide which word timing to trust. Returns (words, source, fallback, notes)."""
    base, notes = _clean_words(words or (), media_duration, config)
    notes = list(notes)

    if not aligned_words:
        return base, "transcript", "", notes

    aligned, aligned_notes = _clean_words(aligned_words, media_duration, config)
    for n in aligned_notes:
        notes.append(f"alignment:{n}")

    if not aligned:
        return base, "transcript", "alignment_unusable_all_words_rejected", notes

    if not base:
        # Nothing to correspond against: aligned evidence alone is not trusted
        # as a content anchor, but there is no safer alternative either.
        return aligned, "aligned", "transcript_timing_absent", notes

    matched, frac = _correspondence(base, aligned)
    if matched < config.min_matched_for_judgement or frac < config.min_correspondence:
        notes.append(f"alignment_correspondence_{frac:.2f}")
        return base, "transcript", "alignment_correspondence_low", notes

    # Disagreement on matched words, by text.
    ta = [_norm(w["word"]) for w in base]
    tb = [_norm(w["word"]) for w in aligned]
    sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
    deltas: list[float] = []
    for tag, i1, i2, j1, _j2 in sm.get_opcodes():
        if tag != "equal":
            continue
        for k in range(i2 - i1):
            deltas.append(abs(aligned[j1 + k]["start"] - base[i1 + k]["start"]))
    if deltas:
        deltas.sort()
        median = deltas[len(deltas) // 2]
        if median > config.max_timing_disagreement_s:
            notes.append(f"alignment_disagreement_{median:.2f}s")
            return base, "transcript", "alignment_disagreement_high", notes
        notes.append(f"alignment_ok_matched_{matched}_median_{median:.3f}s")

    # Alignment is accepted only as SUPPLEMENTAL precision over a transcript we
    # already own; content always comes from LOOM's own transcription.
    merged = _merge_timing(base, aligned, config)
    return merged, "aligned", "", notes


def _merge_timing(base: list[dict], aligned: list[dict],
                  config: BoundaryRefineConfig) -> list[dict]:
    """Use aligned spans only where they correspond to a transcript word.

    Words with no aligned counterpart keep their original timing, so a partial
    alignment can never delete or invent a word.
    """
    ta = [_norm(w["word"]) for w in base]
    tb = [_norm(w["word"]) for w in aligned]
    sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
    out = [dict(w) for w in base]
    for tag, i1, i2, j1, _j2 in sm.get_opcodes():
        if tag != "equal":
            continue
        for k in range(i2 - i1):
            a = aligned[j1 + k]
            b = out[i1 + k]
            # Only accept a refined span that stays close to the original.
            if abs(a["start"] - b["start"]) <= config.max_timing_disagreement_s \
                    and abs(a["end"] - b["end"]) <= config.max_timing_disagreement_s:
                out[i1 + k]["start"] = a["start"]
                out[i1 + k]["end"] = a["end"]
    return out


def _covering(words: list[dict], t: float) -> dict | None:
    i = _covering_index(words, t)
    return None if i is None else words[i]


def _covering_index(words: list[dict], t: float) -> int | None:
    """Index of the word whose span contains t, else None.

    Positional, never ``list.index``: duplicate word dicts would make
    ``.index()`` return the first equal element instead of the covering one.
    """
    for i, w in enumerate(words):
        if w["start"] <= t < w["end"]:
            return i
    return None


def _phrase_start(words: list[dict], index: int, config: BoundaryRefineConfig) -> int:
    """Walk back to the first word of the phrase containing `index`."""
    j = index
    while j > 0 and (words[j]["start"] - words[j - 1]["end"]) < config.phrase_gap_s:
        j -= 1
    return j


def _phrase_end(words: list[dict], index: int, config: BoundaryRefineConfig) -> int:
    """Walk forward to the last word of the phrase containing `index`."""
    j = index
    while j + 1 < len(words) and (words[j + 1]["start"] - words[j]["end"]) < config.phrase_gap_s:
        j += 1
    return j


def _event_tail_end(new_end: float, signal_windows: Sequence[dict],
                    media_duration: float | None,
                    config: BoundaryRefineConfig) -> tuple[float, str]:
    """Extend to cover a bounded nonverbal event right after the speech ends.

    LOOM clips reactions: laughter, screams, applause, impacts, a visual payoff.
    A signal window near the edge justifies keeping a SHORT tail — never an
    unbounded expansion into whatever happens to be nearby.
    """
    best_end = new_end
    best_basis = ""
    horizon = new_end + config.event_tail_max_s
    for w in signal_windows or ():
        if not isinstance(w, dict):
            continue
        basis = w.get("basis")
        if basis not in _EVENT_BASES:
            continue
        s, e = _num(w.get("start_s")), _num(w.get("end_s"))
        if s is None or e is None or e <= s:
            continue
        # The event must begin at/after the speech end (a payoff, not a
        # pre-existing distraction) and start within the tail horizon.
        if s < new_end - 0.10 or s > horizon:
            continue
        candidate_end = min(e + config.event_tail_pad_s, horizon)
        if media_duration is not None:
            candidate_end = min(candidate_end, media_duration)
        if candidate_end > best_end:
            best_end, best_basis = candidate_end, str(basis)
    if best_basis:
        return best_end, f"preserved_event_tail:{best_basis}"
    return new_end, ""


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def refine_boundaries(
    candidate_start: float,
    candidate_end: float,
    *,
    segments: Sequence[dict] = (),
    words: Sequence[dict] = (),
    aligned_words: Sequence[dict] | None = None,
    signal_windows: Sequence[dict] = (),
    media_duration: float | None = None,
    config: BoundaryRefineConfig | None = None,
    provenance: dict[str, Any] | None = None,
) -> RefinedBoundary:
    """Refine a selected candidate's boundaries. Deterministic; no I/O; no model.

    `segments` is accepted as an optional coarse fallback when no word timing
    exists at all — segment edges are then used instead of word edges, and the
    result says so via `timing_source`.
    """
    cfg = config or BoundaryRefineConfig()
    original_start = _num(candidate_start)
    original_end = _num(candidate_end)
    if original_start is None or original_end is None:
        raise ValueError("candidate_start/candidate_end must be finite numbers")

    evidence: list[str] = []
    fallback = ""
    clamped = False

    # ---- candidate sanity / media bounds -------------------------------
    if media_duration is not None and media_duration < 0:
        raise ValueError("media_duration must be >= 0")
    cs = max(0.0, original_start)
    ce = original_end
    if media_duration is not None:
        ce = min(ce, media_duration)
    if ce < cs:
        cs, ce = min(cs, ce), max(cs, ce)
        clamped = True
        evidence.append("candidate_bounds_were_inverted")
    if ce - cs < cfg.min_duration_s:
        ce = min((media_duration if media_duration is not None else cs + cfg.min_duration_s),
                 cs + cfg.min_duration_s)
        cs = max(0.0, ce - cfg.min_duration_s)
        clamped = True
        evidence.append("candidate_shorter_than_min_duration")

    # ---- timing --------------------------------------------------------
    timing_words, source, fallback, notes = _resolve_timing(
        words, aligned_words, media_duration, cfg)
    evidence.extend(notes)
    alignment_used = source == "aligned"

    if not timing_words:
        # Segment-level fallback: no word timing available at all.
        fallback = fallback or "no_word_timing_used_segments"
        return _refine_from_segments(cs, ce, original_start, original_end, segments,
                                     media_duration, cfg, evidence, fallback,
                                     alignment_used, provenance, clamped)

    # ---- START ---------------------------------------------------------
    new_start = cs
    start_reason = "unchanged"
    idx = _covering_index(timing_words, cs)
    if idx is not None:
        # Never begin mid-word.
        new_start = timing_words[idx]["start"]
        start_reason = "snapped_to_word_start"
        p = _phrase_start(timing_words, idx, cfg)
        if p != idx and (cs - timing_words[p]["start"]) <= cfg.edge_search_s:
            new_start = timing_words[p]["start"]
            start_reason = "expanded_back_to_phrase_start"
    else:
        prev = [w for w in timing_words if w["end"] <= cs]
        nxt_idx = next((i for i, w in enumerate(timing_words) if w["start"] >= cs), None)
        gap_before = (cs - prev[-1]["end"]) if prev else cs
        if prev and gap_before > cfg.leading_silence_s:
            if nxt_idx is not None:
                new_start = timing_words[nxt_idx]["start"]
                start_reason = "trimmed_leading_silence"
            else:
                new_start = cs
                start_reason = "no_speech_after_leading_silence"
        elif nxt_idx is not None and (timing_words[nxt_idx]["start"] - cs) <= cfg.edge_search_s:
            # Candidate starts inside a thought: pull back to its phrase start.
            p = _phrase_start(timing_words, nxt_idx, cfg)
            new_start = timing_words[p]["start"]
            start_reason = ("expanded_back_to_phrase_start" if p != nxt_idx
                            else "snapped_to_next_word_start")
        else:
            new_start = cs
            start_reason = "unchanged"

    if abs(new_start - cs) > 1e-9:
        new_start = max(0.0, new_start - cfg.start_pad_s)
    else:
        start_reason = "unchanged"
    if media_duration is not None:
        new_start = min(new_start, media_duration)

    # ---- END -----------------------------------------------------------
    new_end = ce
    end_reason = "unchanged"
    idx = _covering_index(timing_words, ce)
    if idx is not None:
        new_end = timing_words[idx]["end"]
        end_reason = "snapped_to_word_end"
        e = _phrase_end(timing_words, idx, cfg)
        if e != idx and (timing_words[e]["end"] - ce) <= cfg.edge_search_s:
            new_end = timing_words[e]["end"]
            end_reason = "extended_to_phrase_end"
    else:
        prev = [w for w in timing_words if w["end"] <= ce]
        nxt_idx = next((i for i, w in enumerate(timing_words) if w["start"] >= ce), None)
        if prev and nxt_idx is not None \
                and (timing_words[nxt_idx]["start"] - ce) <= cfg.phrase_gap_s \
                and (timing_words[nxt_idx]["start"] - prev[-1]["end"]) <= cfg.edge_search_s:
            # Candidate ends mid-thought: finish the spoken thought.
            e = _phrase_end(timing_words, nxt_idx, cfg)
            new_end = timing_words[e]["end"]
            end_reason = "extended_to_phrase_end"
        elif prev and (ce - prev[-1]["end"]) > cfg.trailing_silence_s:
            new_end = prev[-1]["end"]
            end_reason = "trimmed_trailing_silence"
        else:
            new_end = ce
            end_reason = "unchanged"

    # Trailing dead air: anchor on the last word that ends at or before the
    # refined end, so an intentional phrase extension is never trimmed back off.
    # Trimming removes silence only — never speech.
    anchor = max((w["end"] for w in timing_words if w["end"] <= new_end + 1e-9),
                 default=0.0)
    if anchor > 0 and new_end - anchor > cfg.trailing_silence_s:
        new_end = anchor
        end_reason = "trimmed_trailing_silence"

    # ---- bounded nonverbal event tail (5B evidence) ---------------------
    extended_end, tail_reason = _event_tail_end(new_end, signal_windows, media_duration, cfg)
    if tail_reason:
        event_tail = round(extended_end - new_end, 3)
        new_end = extended_end
        end_reason = tail_reason
    else:
        event_tail = 0.0

    if abs(new_end - ce) > 1e-9:
        new_end += cfg.end_pad_s
    else:
        end_reason = "unchanged"
    if media_duration is not None:
        new_end = min(new_end, media_duration)

    # ---- guardrails ----------------------------------------------------
    # Expansion is the risky direction: adding content is capped.
    max_start_drop = original_start - cfg.max_expand_s
    if new_start < max_start_drop:
        new_start, clamped = max(0.0, max_start_drop), True
        evidence.append("start_expansion_capped")
    if new_end > original_end + cfg.max_expand_s:
        new_end, clamped = original_end + cfg.max_expand_s, True
        evidence.append("end_expansion_capped")

    # Trimming may only ever remove SILENCE: the refined window must still
    # contain every word the original candidate carried. This — not a blanket
    # contraction cap — is what protects speech from being clipped, and it is
    # why dead air can be trimmed by several seconds when it really is dead air.
    carried_first = next((w for w in timing_words if w["end"] > cs), None)
    if carried_first is not None and new_start > carried_first["start"]:
        new_start, clamped = carried_first["start"], True
        evidence.append("start_restored_to_carried_speech")
    carried_in_window = [w for w in timing_words if w["start"] < ce]
    if carried_in_window:
        anchor_end = max(w["end"] for w in carried_in_window)
        if new_end < anchor_end:
            new_end, clamped = anchor_end, True
            evidence.append("end_restored_to_carried_speech")
    else:
        # No speech anchor at all: bound any contraction explicitly.
        if new_start > original_start + cfg.max_contract_s:
            new_start, clamped = original_start + cfg.max_contract_s, True
            evidence.append("start_contraction_capped")
        if new_end < original_end - cfg.max_contract_s:
            new_end, clamped = original_end - cfg.max_contract_s, True
            evidence.append("end_contraction_capped")

    new_start = max(0.0, new_start)
    if media_duration is not None:
        new_end = min(new_end, media_duration)
        new_start = min(new_start, media_duration)
    if new_end - new_start < cfg.min_duration_s:
        new_end = min((media_duration if media_duration is not None else new_start + cfg.min_duration_s),
                      new_start + cfg.min_duration_s)
        clamped = True
        evidence.append("min_duration_enforced")

    return RefinedBoundary(
        start_s=round(new_start, 6), end_s=round(new_end, 6),
        original_start_s=round(cs, 6), original_end_s=round(ce, 6),
        delta_start_s=round(new_start - cs, 6), delta_end_s=round(new_end - ce, 6),
        start_reason=start_reason, end_reason=end_reason, evidence=evidence,
        fallback=fallback, alignment_used=alignment_used, timing_source=source,
        event_tail_s=event_tail, clamped=clamped, provenance=dict(provenance or {}),
    )


def _refine_from_segments(cs, ce, original_start, original_end, segments,
                          media_duration, cfg, evidence, fallback,
                          alignment_used, provenance, clamped) -> RefinedBoundary:
    """Coarse fallback: move edges onto segment edges only. No word claimed."""
    segs = []
    for s in segments or ():
        if not isinstance(s, dict):
            continue
        a, b = _num(s.get("start")), _num(s.get("end"))
        if a is None or b is None or b <= a:
            continue
        segs.append((a, b))
    segs.sort()
    new_start, new_end = cs, ce
    start_reason = end_reason = "unchanged"
    if segs:
        containing = [s for s in segs if s[0] <= cs < s[1]]
        if containing:
            new_start, start_reason = containing[0][0], "segment_start"
        containing_end = [s for s in segs if s[0] < ce <= s[1]]
        if containing_end:
            new_end, end_reason = containing_end[0][1], "segment_end"
    new_start = max(0.0, new_start)
    if media_duration is not None:
        new_end = min(new_end, media_duration)
    if new_end - new_start < cfg.min_duration_s:
        new_end = min((media_duration if media_duration is not None else new_start + cfg.min_duration_s),
                      new_start + cfg.min_duration_s)
        clamped = True
        evidence.append("min_duration_enforced")
    return RefinedBoundary(
        start_s=round(new_start, 6), end_s=round(new_end, 6),
        original_start_s=round(cs, 6), original_end_s=round(ce, 6),
        delta_start_s=round(new_start - cs, 6), delta_end_s=round(new_end - ce, 6),
        start_reason=start_reason, end_reason=end_reason, evidence=evidence,
        fallback=fallback, alignment_used=alignment_used, timing_source="segments",
        event_tail_s=0.0, clamped=clamped, provenance=dict(provenance or {}),
    )
