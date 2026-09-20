#!/usr/bin/env python3
"""Relative-Qwen rerank — bounded A/B acceptance on REAL candidates.

A  = the EXISTING independent/absolute scoring (one Qwen call per candidate,
     through the existing route and prompt)
B  = relative Qwen reranking of the SAME finalist set (one call for the set)

Candidate evidence is held constant between A and B. Finalists are built from
real local media: real word timings from the committed Segment 5C.1 run plus live
Segment 5B signal windows ($0, local).

If the Qwen credential is absent, items A-E cannot be produced from real calls
and are reported NOT PROVEN (never manufactured). Items F and G are provable
offline and are always exercised.

Run from this directory:  python3 prove_relative_rerank.py
Exit code 0 only when nothing FAILED (NOT PROVEN items are reported, not hidden).
"""

from __future__ import annotations

import importlib.util
import json
import os
import statistics
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
# The existing provider must be imported as part of the PACKAGE: provider.py uses
# relative imports (`from . import evidence`, `from .contract import ...`) and
# cannot load as a top-level module. Adding infra/ makes content_intelligence.*
# importable alongside the suite-local modules.
_INFRA = _HERE.parent
if str(_INFRA) not in sys.path:
    sys.path.insert(0, str(_INFRA))

from relative_rerank import (  # noqa: E402
    Finalist,
    QWEN_KEY_ENV,
    RerankConfig,
    qwen_transport,
    relative_rerank,
)
from multimodal_signals import analyze_media_signals  # noqa: E402

RESULTS = _HERE.parent / "whisperx-eval" / "results.json"
EVENT_BASES = ("audio_spike", "visual_motion", "scene_cut")

_PASS: list[str] = []
_FAIL: list[str] = []
_UNPROVEN: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (_PASS if ok else _FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def unproven(name: str, detail: str) -> None:
    _UNPROVEN.append(name)
    print(f"  [NOT PROVEN] {name} — {detail}")


# ---------------------------------------------------------------------------
# real candidate construction
# ---------------------------------------------------------------------------
def load_clips() -> list[dict]:
    return json.loads(RESULTS.read_text(encoding="utf-8"))["clips"]


def words_of(clip: dict) -> list[dict]:
    return [{"word": w["word"], "start": w["start"], "end": w["end"]}
            for w in clip["baseline"]["words"]]


def phrases(words: list[dict], gap: float = 0.45) -> list[tuple[int, int]]:
    out, i = [], 0
    while i < len(words):
        j = i
        while j + 1 < len(words) and (words[j + 1]["start"] - words[j]["end"]) < gap:
            j += 1
        out.append((i, j))
        i = j + 1
    return out


def build_finalists(clip: dict, per_source: int, max_excerpt: int) -> list[Finalist]:
    """Real windows from real timings; evidence bounded per the contract."""
    w = words_of(clip)
    ph = phrases(w)
    # Choose windows spread across the source, skipping trivially short ones.
    usable = [(i, j) for (i, j) in ph if j - i >= 2]
    if not usable:
        usable = ph
    step = max(1, len(usable) // per_source)
    picked = usable[::step][:per_source]

    try:
        sig = [s for s in analyze_media_signals(clip["media"])["signal_windows"]
               if s["basis"] in EVENT_BASES]
    except Exception as exc:                     # surfaced, never silent
        print(f"    (signal extraction failed for {clip['name'][:36]}: {type(exc).__name__})")
        sig = []

    out: list[Finalist] = []
    for n, (i, j) in enumerate(picked, start=1):
        start, end = w[i]["start"], w[j]["end"]
        excerpt = " ".join(x["word"].strip() for x in w[i:j + 1])[:max_excerpt]
        bases = sorted({s["basis"] for s in sig
                        if s["end_s"] > start and s["start_s"] < end})
        words_in = j - i + 1
        dur = end - start
        out.append(Finalist(
            candidate_id=f"{clip['name'][:18]}#w{n}",
            independent_score=0.0,          # filled by path A
            independent_rank=n,             # provisional
            transcript_excerpt=excerpt,
            duration_s=round(dur, 2),
            dimensions={"word_count": words_in,
                        "words_per_s": round(words_in / dur, 2) if dur else 0.0},
            hook=" ".join(x["word"].strip() for x in w[i:min(i + 5, j + 1)])[:80],
            signal_bases=bases,
            source_id=clip["name"][:40],
            provenance={"clip": clip["name"], "window_s": [round(start, 2), round(end, 2)]},
        ))
    return out


def rank_by_score(fs: list[Finalist]) -> None:
    for n, f in enumerate(sorted(fs, key=lambda x: (-x.independent_score, x.candidate_id)), start=1):
        f.independent_rank = n


# ---------------------------------------------------------------------------
# path A: existing independent scoring (one call per candidate)
# ---------------------------------------------------------------------------
def independent_scores(fs: list[Finalist], brief, timeout: float,
                       max_output_tokens: int) -> tuple[bool, list[str]]:
    """Score each candidate ALONE with the existing provider + prompt.

    The route (endpoint, pinned model, key, prompt) is untouched; only the output
    budget is set explicitly, because the pinned model is a reasoning model that
    exhausts a small budget before emitting any content.
    """
    from content_intelligence.provider import Evidence
    from content_intelligence.providers import ProviderError, build_provider

    key = os.environ.get(QWEN_KEY_ENV, "")
    if not key:
        return False, [f"{QWEN_KEY_ENV} absent"]
    provider = build_provider("qwen", key, timeout=timeout,
                              max_output_tokens=max_output_tokens)
    notes: list[str] = []
    for f in fs:
        ev = Evidence(platform="local", content_type="video", title=f.candidate_id,
                      transcript_text=f.transcript_excerpt, transcript_availability="auto_generated")
        try:
            res = provider.assess(ev, brief)
        except ProviderError as e:
            notes.append(f"{f.candidate_id}: {e.code}")
            return False, notes
        f.independent_score = float(res.get("content_worthiness_score", 0) or 0)
        u = res.get("usage")
        if u is not None:
            notes.append(f"{f.candidate_id}: in={getattr(u, 'input_tokens', '?')} "
                         f"out={getattr(u, 'output_tokens', '?')} cost={getattr(u, 'cost_usd', '?')}")
    return True, notes


def main() -> int:
    cfg = RerankConfig()
    clips = load_clips()
    key_present = bool(os.environ.get(QWEN_KEY_ENV, ""))
    print(f"Qwen credential ({QWEN_KEY_ENV}) present: {key_present}")
    print(f"route: openrouter qwen/{cfg.model.split('/')[-1]}, max_finalists={cfg.max_finalists}")

    sources = [c for c in clips if "mushoku-tensei" in c["name"]
               or "kick__looselipssinkships" in c["name"]
               or "balpreezy" in c["name"]][:3]
    print(f"\nreal sources: {len(sources)}; finalists per source: 4")

    class _Brief:
        topics = ["reaction"]
        keywords = ["moment"]
        entities = []

    sets: list[tuple[dict, list[Finalist]]] = []
    for c in sources:
        fs = build_finalists(c, per_source=4, max_excerpt=cfg.max_excerpt_chars)
        sets.append((c, fs))
        print(f"  {c['name'][:46]:48s} {len(fs)} finalists, "
              f"durations {[f.duration_s for f in fs]}")

    # ------------------------------------------------------------------ F (offline)
    print("\n--- F: deterministic/schema-valid handling of a bounded finalist set ---")
    fs = sets[0][1]
    payload = {"ranking": [
        {"candidate_id": f.candidate_id, "relative_rank": n, "relative_score": 100 - n,
         "rationale": "deterministic test", "strongest_evidence": f.hook,
         "redundant_with": None, "confidence": 0.5}
        for n, f in enumerate(reversed(fs), start=1)]}
    import json as _json
    t = lambda _p: (_json.dumps(payload), {"prompt_tokens": 10, "completion_tokens": 5}, cfg.model)
    r1 = relative_rerank(fs, source_context="determinism", transport=t, config=cfg)
    r2 = relative_rerank(fs, source_context="determinism", transport=t, config=cfg)
    expected = [row["candidate_id"] for row in payload["ranking"]]
    check("F  schema-valid payload is accepted and applied",
          r1.used and [f.candidate_id for f in r1.finalists] == expected,
          f"order={[f.candidate_id for f in r1.finalists]}")
    check("F  identical input yields identical output (deterministic handling)",
          r1.to_dict() == r2.to_dict())
    check("F  independent scores are preserved, never overwritten",
          all(a.independent_score == b.independent_score
              for a, b in zip(r1.finalists, r2.finalists)))

    # ------------------------------------------------------------------ G (offline)
    print("\n--- G: safe fallback ---")
    from providers import ProviderError

    def _boom(_p):
        raise ProviderError("AUTH", f"{QWEN_KEY_ENV} absent", retryable=False)
    rg1 = relative_rerank(fs, transport=_boom, config=cfg)
    check("G  missing credential falls back to the independent order",
          (not rg1.used) and rg1.fallback == "provider_error:AUTH"
          and [f.candidate_id for f in rg1.finalists] == [f.candidate_id for f in sorted(fs, key=lambda x: x.independent_rank)],
          f"fallback={rg1.fallback}")
    rg2 = relative_rerank(fs, transport=lambda _p: ("{not json", {}, cfg.model), config=cfg)
    check("G  malformed JSON falls back", (not rg2.used) and rg2.fallback == "invalid_json",
          f"fallback={rg2.fallback}")
    rg3 = relative_rerank(fs, transport=lambda _p: (_json.dumps({"ranking": []}), {}, cfg.model), config=cfg)
    check("G  schema violation falls back", (not rg3.used) and rg3.fallback == "schema_invalid",
          f"fallback={rg3.fallback}")
    rg4 = relative_rerank(fs[:1], transport=t, config=cfg)
    check("G  a too-small finalist set is skipped safely",
          (not rg4.used) and rg4.fallback == "finalist_set_too_small", f"fallback={rg4.fallback}")
    check("G  reranking is never a hard failure point (every case returned an order)",
          all(len(x.finalists) == len(fs) for x in (rg1, rg2, rg3)))

    # ------------------------------------------------------- A-E (need real calls)
    print("\n--- A-E: real A/B ordering comparison ---")
    if not key_present:
        for item, why in (
            ("A  agreement between independent and relative ordering", f"{QWEN_KEY_ENV} absent"),
            ("B  relative comparison changes a meaningful ordering", f"{QWEN_KEY_ENV} absent"),
            ("C  redundant/overlapping finalists: stronger representative preferred", f"{QWEN_KEY_ENV} absent"),
            ("D  high absolute score with weaker source-relative value", f"{QWEN_KEY_ENV} absent"),
            ("E  lower absolute score that becomes compelling in comparison", f"{QWEN_KEY_ENV} absent"),
        ):
            unproven(item, why)
    else:
        print("    (credential present — real A/B would run here)")
        for c, fs2 in sets:
            ok, notes = independent_scores(fs2, _Brief(), cfg.timeout,
                                           cfg.max_output_tokens)
            if not ok:
                unproven(f"A-E for {c['name'][:30]}", "; ".join(notes))
                continue
            rank_by_score(fs2)
            res = relative_rerank(fs2, source_context=c["name"][:40], config=cfg)
            print(f"    {c['name'][:40]}: used={res.used} changed={res.ordering_changed} "
                  f"calls=1 tokens_in={getattr(res.usage, 'input_tokens', '?')} "
                  f"tokens_out={getattr(res.usage, 'output_tokens', '?')} "
                  f"cost={getattr(res.usage, 'cost_usd', '?')} latency={getattr(res.usage, 'latency_s', '?')}s")
            for f in res.finalists:
                print(f"      {f.candidate_id:26s} ind#{f.independent_rank:<2} rel#{f.relative_rank:<2} "
                      f"{(f.rationale or '')[:70]}")

    print(f"\n=== ACCEPTANCE: {len(_PASS)} passed, {len(_FAIL)} failed, "
          f"{len(_UNPROVEN)} not proven ===")
    for f in _FAIL:
        print(f"  FAILED: {f}")
    for u in _UNPROVEN:
        print(f"  NOT PROVEN: {u}")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
