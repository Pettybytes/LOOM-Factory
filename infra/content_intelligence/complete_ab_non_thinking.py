#!/usr/bin/env python3
"""Bounded COMPLETION of the Relative-Qwen A/B, using the non-thinking profile.

Why this file exists: with thinking ON (the provider default), one pass consumed
5464+ output tokens and frequently exhausted even a 16000 cap, returning an empty
completion (INVALID_RESPONSE). The bounded experiment showed non-thinking mode is
supported by the same route and is ~13x cheaper in tokens and ~11x faster.

BOTH arms here use the SAME generation controls (non-thinking, JSON-constrained,
Qwen's non-thinking sampling) and the SAME route/model/prompts. The ONLY variable
under test is:

    A  one call per candidate   -> existing independent/absolute content scoring
    B  one call per finalist set -> relative comparison

Every number printed is measured. Nothing is fabricated: if a call fails, it is
reported as a failure and the affected comparison is marked NOT PROVEN.

Run:  set -a; . ~/.openclaw/.env; set +a; python3 complete_ab_non_thinking.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from statistics import mean

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from providers import _build_prompt  # noqa: E402  (existing scoring prompt)
from relative_rerank import (  # noqa: E402
    QWEN_KEY_ENV,
    RerankConfig,
    build_rerank_prompt,
    validate_rerank_payload,
)
from experiment_non_thinking import NON_THINKING, call  # noqa: E402
import prove_relative_rerank as H  # noqa: E402

GEN = dict(thinking=False, structured="json_object", max_tokens=1200,
           temperature=NON_THINKING["temperature"], top_p=NON_THINKING["top_p"],
           top_k=NON_THINKING["top_k"])


def extract(text: str) -> dict | None:
    try:
        return json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception:
        return None


def independent_score(excerpt: str) -> tuple[float | None, dict, str]:
    """Arm A: the EXISTING content prompt, asked about this candidate alone."""
    res = call(_build_prompt(excerpt, 12000), **GEN)
    if "http_error" in res:
        return None, res, f"http_{res['http_error']}"
    payload = extract(res["text"])
    if payload is None:
        return None, res, "invalid_json"
    score = payload.get("content_worthiness_score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        dims = payload.get("scores")
        if isinstance(dims, dict) and dims:
            vals = [v for v in dims.values() if isinstance(v, (int, float))]
            if vals:
                score = mean(vals)
                res["score_basis"] = "mean_of_dimension_scores"
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return None, res, "no_score_field"
    return float(score), res, ""


def spearman(a: list[float], b: list[float]) -> float | None:
    """Rank correlation over the SAME candidates (small-n; ties averaged)."""
    n = len(a)
    if n < 2:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: -v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    ra, rb = ranks(a), ranks(b)
    ma, mb = mean(ra), mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return round(num / (da * db), 3) if da and db else None


def main() -> int:
    if not os.environ.get(QWEN_KEY_ENV):
        print(f"{QWEN_KEY_ENV} absent"); return 2
    cfg = RerankConfig()
    clips = H.load_clips()
    sources = [c for c in clips if "kick__looselipssinkships" in c["name"]
               or "mushoku-tensei" in c["name"] or "balpreezy" in c["name"]][:3]

    calls = 0
    valid = 0
    total_cost = 0.0
    latencies: list[float] = []
    results = []

    for clip in sources:
        fs = H.build_finalists(clip, per_source=4, max_excerpt=cfg.max_excerpt_chars)
        print(f"\n=== {clip['name'][:52]}  ({len(fs)} finalists)")

        # ---- Arm A: independent scoring, one call per candidate -------------
        a_ok = True
        for f in fs:
            score, res, err = independent_score(f.transcript_excerpt)
            calls += 1
            m = res.get("metrics") or {}
            if m:
                valid += 1
                total_cost += m.get("cost_usd") or 0.0
                latencies.append(m.get("latency_s") or 0.0)
            if score is None:
                a_ok = False
                print(f"    A {f.candidate_id.split('#')[-1]}: FAILED ({err})")
            else:
                f.independent_score = score
                print(f"    A {f.candidate_id.split('#')[-1]}: score={score:.1f} "
                      f"out_tok={m.get('output_tokens')} reasoning={m.get('reasoning_tokens')} "
                      f"{m.get('latency_s')}s")
        for n, f in enumerate(sorted(fs, key=lambda x: (-x.independent_score, x.candidate_id)), 1):
            f.independent_rank = n

        # ---- Arm B: relative rerank, one call per set -----------------------
        res_b = call(build_rerank_prompt(fs, source_context=clip["name"][:40], config=cfg), **GEN)
        calls += 1
        mb = res_b.get("metrics") or {}
        if mb:
            valid += 1
            total_cost += mb.get("cost_usd") or 0.0
            latencies.append(mb.get("latency_s") or 0.0)
        b_rows = None
        if "http_error" not in res_b:
            payload = extract(res_b.get("text", ""))
            if payload is not None:
                try:
                    b_rows = validate_rerank_payload(payload, fs)
                except Exception as exc:
                    print(f"    B SCHEMA INVALID: {exc}")
        if b_rows is None:
            print("    B FAILED — relative arm unusable for this source")
            results.append({"source": clip["name"], "a_ok": a_ok, "b_ok": False})
            continue

        b_rank = {r["candidate_id"]: r["relative_rank"] for r in b_rows}
        b_score = {r["candidate_id"]: r.get("relative_score") for r in b_rows}
        rationale = {r["candidate_id"]: r.get("rationale", "") for r in b_rows}
        print(f"    B: out_tok={mb.get('output_tokens')} reasoning={mb.get('reasoning_tokens')} "
              f"{mb.get('latency_s')}s cost=${mb.get('cost_usd')}")

        ids = [f.candidate_id for f in fs]
        a_scores = [f.independent_score for f in fs]
        b_scores = [b_score.get(i) if b_score.get(i) is not None else 0.0 for i in ids]
        rho = spearman(a_scores, b_scores) if a_ok else None
        changed = [i for i in ids if b_rank[i] != next(f.independent_rank for f in fs if f.candidate_id == i)]
        print(f"    ordering A (independent): {[f'{f.candidate_id.split(chr(35))[-1]}={f.independent_score:.0f}' for f in sorted(fs, key=lambda x: x.independent_rank)]}")
        print(f"    ordering B (relative)   : {[f'{i.split(chr(35))[-1]}#{b_rank[i]}' for i in sorted(ids, key=lambda i: b_rank[i])]}")
        print(f"    rank correlation (rho)={rho}  candidates whose rank changed: "
              f"{[c.split('#')[-1] for c in changed]}")
        for i in sorted(ids, key=lambda i: b_rank[i]):
            print(f"      B#{b_rank[i]} {i.split('#')[-1]} rel={b_score.get(i)} "
                  f"ind_rank={next(f.independent_rank for f in fs if f.candidate_id == i)} "
                  f":: {rationale[i][:64]}")
        redundancies = [(r["candidate_id"].split('#')[-1], r.get("redundant_with"))
                        for r in b_rows if r.get("redundant_with")]
        print(f"    redundancy flags: {redundancies or 'none'}")
        results.append({"source": clip["name"], "a_ok": a_ok, "b_ok": True,
                        "rho": rho, "changed": [c.split('#')[-1] for c in changed],
                        "redundancies": redundancies,
                        "a_scores": dict(zip([i.split('#')[-1] for i in ids], a_scores)),
                        "b_rank": {i.split('#')[-1]: b_rank[i] for i in ids}})

    print("\n=== MEASURED TOTALS ===")
    print(f"  calls: {calls}  schema-valid: {valid}/{calls}  "
          f"({100.0 * valid / calls:.0f}%)" if calls else "  no calls")
    print(f"  total cost: ${total_cost:.6f}")
    if latencies:
        print(f"  latency: mean {mean(latencies):.1f}s  max {max(latencies):.1f}s")
    print(json.dumps(results, indent=2))

    print("\n=== A-E (evidence-based) ===")
    usable = [r for r in results if r["b_ok"] and r["a_ok"]]
    if not usable:
        print("  A-E: NOT PROVEN (no source produced both arms)")
        return 0
    rhos = [r["rho"] for r in usable if r["rho"] is not None]
    print(f"  A  rank correlation per source: {rhos} -> "
          f"{'substantial agreement' if rhos and min(rhos) >= 0.6 else 'AGREEMENT NOT ESTABLISHED'}")
    moved = [r for r in usable if r["changed"]]
    print(f"  B  sources where relative changed >=1 rank: {len(moved)}/{len(usable)} "
          f"-> {'PROVEN' if moved else 'NOT PROVEN (no change observed)'}")
    red = [r for r in usable if r["redundancies"]]
    print(f"  C  sources with a redundancy flag: {len(red)}/{len(usable)} "
          f"-> {'PROVEN' if red else 'NOT PROVEN (no redundancy observed)'}")
    print("  D/E  see per-source promotions/demotions above")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
