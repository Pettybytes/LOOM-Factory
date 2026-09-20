#!/usr/bin/env python3
"""Segment 5C.2 runtime acceptance — boundary refiner on REAL media and REAL timings.

Every input is real, not synthetic:

* media     — the actual files in the local Reaction Library
* timings   — the committed Segment 5C.1 run (`infra/whisperx-eval/results.json`):
              LOOM's existing word timestamps plus the real WhisperX aligned words
* events    — 5B signal windows computed live by `multimodal_signals` ($0, local)

The cases are found by SEARCHING the evidence set for real instances of each
precondition (a genuine multi-second silence, a real payoff-after-speech window,
a real multi-word phrase). Where the evidence set contains no real instance, the
item is reported NOT PROVEN rather than quietly passed.

  A  excessive leading dead air is reduced
  B  excessive trailing dead air is reduced
  C  a tight candidate is left unchanged
  D  a mid-sentence start expands backward
  E  an early end expands forward
  F  a nonverbal payoff is preserved where 5B evidence supports it
  G  bad alignment is rejected / falls back safely
  H  a media-edge candidate is clamped to valid media time
  I  replay is deterministic

Run from this directory:  python3 prove_boundary_refiner.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from boundary_refiner import BoundaryRefineConfig, refine_boundaries  # noqa: E402
from multimodal_signals import analyze_media_signals  # noqa: E402

RESULTS = _HERE.parent / "whisperx-eval" / "results.json"
CFG = BoundaryRefineConfig()
_EVENT_BASES = ("audio_spike", "visual_motion", "scene_cut")

_PASS: list[str] = []
_FAIL: list[str] = []
_UNPROVEN: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (_PASS if ok else _FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def unproven(name: str, detail: str) -> None:
    _UNPROVEN.append(name)
    print(f"  [NOT PROVEN] {name} — {detail}")


def load_clips() -> list[dict]:
    return json.loads(RESULTS.read_text(encoding="utf-8"))["clips"]


def words_of(clip: dict) -> list[dict]:
    return [{"word": w["word"], "start": w["start"], "end": w["end"]}
            for w in clip["baseline"]["words"]]


def aligned_of(clip: dict) -> list[dict]:
    return [{"word": w["word"], "start": w["start"], "end": w["end"]}
            for w in clip["whisperx"]["words"]]


def phrases(words: list[dict]) -> list[tuple[int, int]]:
    """(first_index, last_index) of each spoken phrase, split on real gaps."""
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(words):
        j = i
        while j + 1 < len(words) and (words[j + 1]["start"] - words[j]["end"]) < CFG.phrase_gap_s:
            j += 1
        out.append((i, j))
        i = j + 1
    return out


def main() -> int:
    clips = load_clips()
    print(f"5C.1 evidence set: {len(clips)} clips "
          f"({json.loads(RESULTS.read_text())['environment']['asr_model']}/"
          f"{json.loads(RESULTS.read_text())['environment']['compute_type']})")

    # ---------------------------------------------------------------- A / B
    print("\n--- A/B: real dead air, searched across the evidence set ---")
    best_lead = None   # (gap, clip, phrase_start, phrase_end)
    best_trail = None
    for c in clips:
        w = words_of(c)
        if len(w) < 3:
            continue
        dur = c["duration_s"]
        for (i, j) in phrases(w):
            if i > 0:
                gap = w[i]["start"] - w[i - 1]["end"]
                if best_lead is None or gap > best_lead[0]:
                    best_lead = (gap, c, w[i]["start"], w[j]["end"], w)
            after = (w[j + 1]["start"] - w[j]["end"]) if j + 1 < len(w) else (dur - w[j]["end"])
            if after > 0 and (best_trail is None or after > best_trail[0]):
                best_trail = (after, c, w[i]["start"], w[j]["end"], w)

    if best_lead and best_lead[0] >= 1.0:
        gap, c, pstart, pend, w = best_lead
        cand_start = pstart - min(gap / 2.0, 3.0)
        r = refine_boundaries(cand_start, pend, words=w, media_duration=c["duration_s"])
        print(f"    clip {c['name'][:44]} real gap {gap:.2f}s; candidate {cand_start:.2f}-{pend:.2f}")
        check("A  excessive leading dead air is reduced",
              r.delta_start_s > 0.2 and r.start_reason in
              ("trimmed_leading_silence", "snapped_to_next_word_start"),
              f"start {r.original_start_s:.2f} -> {r.start_s:.2f} ({r.start_reason})")
    else:
        unproven("A  excessive leading dead air is reduced",
                 f"largest real inter-phrase gap is {(best_lead[0] if best_lead else 0):.2f}s (< 1.0s)")

    if best_trail and best_trail[0] >= 1.0:
        gap, c, pstart, pend, w = best_trail
        # Stay inside the real silence: overshooting into the next phrase would
        # (correctly) be treated as mid-thought, not as dead air.
        cand_end = pend + min(gap / 2.0, 4.0)
        r = refine_boundaries(pstart, cand_end, words=w, media_duration=c["duration_s"])
        print(f"    clip {c['name'][:44]} real silence after phrase {gap:.2f}s; candidate ends {cand_end:.2f}")
        check("B  excessive trailing dead air is reduced",
              r.delta_end_s < -0.5 and r.end_reason == "trimmed_trailing_silence",
              f"end {r.original_end_s:.2f} -> {r.end_s:.2f} ({r.end_reason})")
    else:
        unproven("B  excessive trailing dead air is reduced",
                 f"largest real trailing gap is {(best_trail[0] if best_trail else 0):.2f}s (< 1.0s)")

    # ------------------------------------------------------------------ C/D/E
    print("\n--- C/D/E: long real phrase (clean dialogue clip) ---")
    anime = next(c for c in clips if "mushoku-tensei" in c["name"])
    w = words_of(anime)
    dur = anime["duration_s"]
    multi = [(i, j) for (i, j) in phrases(w) if j - i >= 3]
    i, j = max(multi, key=lambda p: p[1] - p[0])
    pstart, pend = w[i]["start"], w[j]["end"]
    print(f"    phrase: words {i}-{j} = {pstart:.2f}-{pend:.2f} "
          f"({'" + " ".join(x["word"].strip() for x in w[i:j + 1])[:60] + "'})")

    r = refine_boundaries(pstart, pend, words=w, media_duration=dur)
    check("C  a tight candidate is left unchanged",
          abs(r.delta_start_s) <= 0.06 and abs(r.delta_end_s) <= 0.06,
          f"Δ {r.delta_start_s:+.3f}/{r.delta_end_s:+.3f}")

    # Interior word, but within edge_search_s of the phrase start: the refiner
    # deliberately refuses to reach back further than that.
    k = min(i + 1, j)
    mid = w[k]["start"] + (w[k]["end"] - w[k]["start"]) / 2
    r = refine_boundaries(mid, pend, words=w, media_duration=dur)
    check("D  a mid-sentence start expands backward",
          r.delta_start_s < -0.05 and r.start_reason == "expanded_back_to_phrase_start",
          f"start {mid:.2f} -> {r.start_s:.2f} ({r.start_reason}, phrase start {pstart:.2f})")

    early = w[j - 1]["start"] + 0.05
    r = refine_boundaries(pstart, early, words=w, media_duration=dur)
    check("E  an early end expands forward",
          r.delta_end_s > 0.05 and r.end_reason == "extended_to_phrase_end",
          f"end {early:.2f} -> {r.end_s:.2f} ({r.end_reason})")

    # -------------------------------------------------------------------- F
    print("\n--- F: real nonverbal payoff, searched with live 5B evidence ---")
    payoff = None
    for c in clips[:5]:                      # bounded: first five evidence clips
        w = words_of(c)
        if not w:
            continue
        try:
            sig = [s for s in analyze_media_signals(c["media"])["signal_windows"]
                   if s["basis"] in _EVENT_BASES]
        except Exception as exc:             # surfaced, never silent
            print(f"    {c['name'][:40]}: signal extraction failed: {type(exc).__name__}")
            continue
        for s in sig:
            prior = [x for x in w if x["end"] <= s["start_s"]]
            if not prior:
                continue
            speech_end = prior[-1]["end"]
            # The payoff must follow the REFINED speech boundary: the refiner may
            # first complete the thought, which moves the end past the raw word.
            refined_end = refine_boundaries(w[0]["start"], speech_end, words=w,
                                            media_duration=c["duration_s"]).end_s
            gap = s["start_s"] - refined_end
            if 0.0 <= gap <= CFG.event_tail_max_s:
                if payoff is None or gap < payoff[0]:
                    payoff = (gap, c, w, refined_end, s)
    if payoff:
        gap, c, w, last_end, s = payoff
        r = refine_boundaries(w[0]["start"], last_end, words=w,
                              signal_windows=[s], media_duration=c["duration_s"])
        print(f"    clip {c['name'][:44]} real {s['basis']} at "
              f"{s['start_s']:.2f}-{s['end_s']:.2f}, {gap:.2f}s after speech end")
        check("F  a nonverbal payoff is preserved where 5B evidence supports it",
              r.end_reason.startswith("preserved_event_tail") and r.event_tail_s > 0,
              f"tail {r.event_tail_s:+.2f}s ({r.end_reason})")
        far = [{"start_s": c["duration_s"] - 1.0, "end_s": c["duration_s"], "basis": "audio_spike"}]
        r2 = refine_boundaries(w[0]["start"], last_end, words=w, signal_windows=far,
                               media_duration=c["duration_s"])
        check("F  a distant signal does not expand the candidate", r2.event_tail_s == 0.0,
              f"event_tail_s={r2.event_tail_s}")
    else:
        unproven("F  a nonverbal payoff is preserved where 5B evidence supports it",
                 "no real signal window begins within the tail horizon after speech in the searched clips")

    # -------------------------------------------------------------------- G
    print("\n--- G: bad alignment rejected (real 5C.1 anomaly) ---")
    kick = next(c for c in clips if "kick__looselipssinkships" in c["name"])
    kw, ka = words_of(kick), aligned_of(kick)
    stretched = [(x["word"].strip(), round(x["end"] - x["start"], 2)) for x in ka
                 if x["end"] - x["start"] > CFG.implausible_word_s]
    print(f"    real implausible aligned spans present: {stretched}")
    r = refine_boundaries(kw[0]["start"], kw[len(kw) // 2]["end"], words=kw,
                          aligned_words=ka, media_duration=kick["duration_s"])
    print(f"    evidence: {r.evidence}")
    check("G  implausible aligned spans never reach the boundary decision",
          (not stretched) or any("implausible" in e for e in r.evidence),
          f"{len(stretched)} real stretched span(s) dropped")

    shifted = [{"word": x["word"], "start": x["start"] + 3.0, "end": x["end"] + 3.0} for x in ka]
    r2 = refine_boundaries(kw[0]["start"], kw[len(kw) // 2]["end"], words=kw,
                           aligned_words=shifted, media_duration=kick["duration_s"])
    check("G  severe alignment disagreement falls back to transcript timing",
          r2.fallback == "alignment_disagreement_high" and not r2.alignment_used,
          f"fallback={r2.fallback}")

    # ------------------------------------------------------------------ H/I
    print("\n--- H/I: media edges and determinism ---")
    r = refine_boundaries(-5.0, dur + 10.0, words=w, media_duration=dur)
    check("H  a media-edge candidate is clamped to valid media time",
          0.0 <= r.start_s and r.end_s <= dur and r.end_s > r.start_s,
          f"{r.start_s:.2f}-{r.end_s:.2f} within 0-{dur:.2f}")
    a1 = refine_boundaries(pstart - 2.0, pend + 2.0, words=w, media_duration=dur).to_dict()
    a2 = refine_boundaries(pstart - 2.0, pend + 2.0, words=w, media_duration=dur).to_dict()
    check("I  replay is deterministic", a1 == a2)

    print(f"\n=== ACCEPTANCE: {len(_PASS)} passed, {len(_FAIL)} failed, "
          f"{len(_UNPROVEN)} not proven ===")
    for f in _FAIL:
        print(f"  FAILED: {f}")
    for u in _UNPROVEN:
        print(f"  NOT PROVEN: {u}")
    return 1 if _FAIL or _UNPROVEN else 0


if __name__ == "__main__":
    raise SystemExit(main())
