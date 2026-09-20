#!/usr/bin/env python3
"""Bounded diagnostic: is the rho=1.0 result REAL agreement, or is the model just
echoing the independent score it was shown?

In the completed A/B, every source produced rank correlation 1.0 and relative
scores numerically IDENTICAL to the independent scores. That is exactly what
anchoring looks like: the rerank prompt included a line
`independent_score: <n>` per candidate.

This script asks the SAME relative question with that line REMOVED, using the same
candidates, the same non-thinking generation profile, the same route/model/prompt
otherwise. Nothing else changes.

3 calls total. Arm A scores are the ones MEASURED by complete_ab_non_thinking.py
(recorded here for comparison, not re-invented).

Run:  set -a; . ~/.openclaw/.env; set +a; python3 experiment_unanchored_rerank.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from relative_rerank import (  # noqa: E402
    QWEN_KEY_ENV,
    RerankConfig,
    build_rerank_prompt,
    validate_rerank_payload,
)
from experiment_non_thinking import NON_THINKING, call  # noqa: E402
from complete_ab_non_thinking import extract, spearman  # noqa: E402
import prove_relative_rerank as H  # noqa: E402

GEN = dict(thinking=False, structured="json_object", max_tokens=1200,
           temperature=NON_THINKING["temperature"], top_p=NON_THINKING["top_p"],
           top_k=NON_THINKING["top_k"])

# Measured by complete_ab_non_thinking.py (arm A, one call per candidate).
ARM_A = {
    "kick__looselipssinkships": {"w1": 85.0, "w2": 68.0, "w3": 72.0, "w4": 82.0},
    "lore-addict": {"w1": 82.0, "w2": 92.0},
    "balpreezy": {"w1": 35.0, "w2": 42.0, "w3": 55.0, "w4": 35.0},
}


def main() -> int:
    if not os.environ.get(QWEN_KEY_ENV):
        print(f"{QWEN_KEY_ENV} absent"); return 2
    cfg = RerankConfig()
    clips = [c for c in H.load_clips()
             if "kick__looselipssinkships" in c["name"] or "mushoku-tensei" in c["name"]
             or "balpreezy" in c["name"]][:3]

    total_cost = 0.0
    for clip in clips:
        key = next(k for k in ARM_A if k in clip["name"])
        fs = H.build_finalists(clip, per_source=4, max_excerpt=cfg.max_excerpt_chars)
        for f in fs:
            f.independent_score = ARM_A[key][f.candidate_id.split("#")[-1]]
        print(f"\n=== {clip['name'][:50]}")

        prompt = build_rerank_prompt(fs, source_context=clip["name"][:40], config=cfg)
        # Remove ONLY the anchor line; everything else is byte-identical.
        # (Line filter, not a regex: escaping in the pattern is easy to corrupt.)
        unanchored = "\n".join(
            line for line in prompt.splitlines()
            if not line.startswith("independent_score:")
        )
        assert "independent_score" not in unanchored, "anchor line survived"

        res = call(unanchored, **GEN)
        if "http_error" in res:
            print(f"    HTTP {res['http_error']}: {res['body']}"); continue
        m = res["metrics"]
        total_cost += m.get("cost_usd") or 0.0
        payload = extract(res.get("text", ""))
        if payload is None:
            print(f"    invalid JSON (finish={m['finish_reason']})"); continue
        try:
            rows = validate_rerank_payload(payload, fs)
        except Exception as exc:
            print(f"    SCHEMA INVALID: {exc}"); continue

        b_rank = {r["candidate_id"].split("#")[-1]: r["relative_rank"] for r in rows}
        b_score = {r["candidate_id"].split("#")[-1]: r.get("relative_score") for r in rows}
        labels = [f.candidate_id.split("#")[-1] for f in fs]
        a_scores = [ARM_A[key][l] for l in labels]
        b_scores = [b_score[l] if b_score[l] is not None else 0.0 for l in labels]
        rho = spearman(a_scores, b_scores)
        a_rank = {l: r for r, l in enumerate(
            sorted(labels, key=lambda l: -ARM_A[key][l]), start=1)}
        changed = [l for l in labels if b_rank[l] != a_rank[l]]
        print(f"    out_tok={m['output_tokens']} reasoning={m['reasoning_tokens']} "
              f"{m['latency_s']}s cost=${m['cost_usd']}")
        print(f"    arm A order (anchored source of truth): "
              f"{[f'{l}={ARM_A[key][l]:.0f}' for l in sorted(labels, key=lambda l: a_rank[l])]}")
        print(f"    UNANCHORED relative order             : "
              f"{[f'{l}#{b_rank[l]}({b_score[l]})' for l in sorted(labels, key=lambda l: b_rank[l])]}")
        print(f"    rho={rho}   rank changes vs arm A: {changed or 'NONE'}")
        for r in sorted(rows, key=lambda r: r["relative_rank"]):
            cid = r["candidate_id"].split("#")[-1]
            print(f"      #{r['relative_rank']} {cid} rel={r.get('relative_score')} "
                  f"ind={ARM_A[key][cid]:.0f} red={r.get('redundant_with')} :: "
                  f"{str(r.get('rationale'))[:66]}")
    print(f"\ntotal cost: ${total_cost:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
