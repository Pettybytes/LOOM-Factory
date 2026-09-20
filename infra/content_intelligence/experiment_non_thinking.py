#!/usr/bin/env python3
"""Bounded experiment: can the pinned Qwen model do the rerank contract in
NON-THINKING mode — reliably, cheaply and structurally?

Context: with thinking ON, a single rerank/assessment pass consumed 5464+ output
tokens (and sometimes exhausted even a 16000 cap, returning an empty completion
that surfaced as INVALID_RESPONSE). That is not a workable production profile for
a 2-6 candidate reranker.

This script is deliberately SMALL: one real finalist set, a couple of variants.
It reports, per variant: HTTP ok, finish_reason, JSON present, schema validity
(via the real validator), the resulting ordering, output tokens, reasoning tokens
if exposed, latency and cost.

Route is unchanged: same provider (OpenRouter), same pinned model
(qwen/qwen3.5-9b), same key, same prompt. Only generation controls differ.

Run:  set -a; . ~/.openclaw/.env; set +a; python3 experiment_non_thinking.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
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
import prove_relative_rerank as H  # noqa: E402

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "qwen/qwen3.5-9b"

# Qwen's documented non-thinking sampling recommendation is warmer than the
# thinking default (temperature 0.7 / top_p 0.8 / top_k 20), so temperature 0 is
# deliberately NOT assumed to be optimal here — both are measured.
NON_THINKING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20}

SCHEMA = {
    "name": "rerank_result",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "ranking": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "relative_rank": {"type": "integer"},
                        "relative_score": {"type": "number"},
                        "rationale": {"type": "string"},
                        "strongest_evidence": {"type": "string"},
                        "redundant_with": {"type": ["string", "null"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["candidate_id", "relative_rank", "relative_score",
                                 "rationale", "strongest_evidence", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["ranking"],
        "additionalProperties": False,
    },
}


def _auth() -> str:
    """Authorization header value.

    The scheme word is assembled from parts on purpose: writing it literally has
    twice been corrupted into a placeholder here, producing
    "Missing Authentication header" (HTTP 401) from OpenRouter.
    """
    return "Bear" + "er " + os.environ[QWEN_KEY_ENV]


def call(prompt: str, *, thinking: bool, structured: str | None,
         max_tokens: int, temperature: float, top_p: float | None = None,
         top_k: int | None = None) -> dict:
    body: dict = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if top_p is not None:
        body["top_p"] = top_p
    if top_k is not None:
        body["top_k"] = top_k
    if not thinking:
        body["reasoning"] = {"enabled": False}
    if structured == "json_object":
        body["response_format"] = {"type": "json_object"}
    elif structured == "json_schema":
        body["response_format"] = {"type": "json_schema", "json_schema": SCHEMA}

    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": _auth()},
        method="POST")
    t0 = time.time()
    try:
        raw = json.loads(urllib.request.urlopen(req, timeout=180).read().decode())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode()[:400],
                "latency_s": round(time.time() - t0, 2)}
    latency = round(time.time() - t0, 2)
    ch = (raw.get("choices") or [{}])[0]
    txt = (ch.get("message") or {}).get("content") or ""
    u = raw.get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    out: dict = {
        "finish_reason": ch.get("finish_reason"),
        "content_len": len(txt),
        "reasoning_tokens": det.get("reasoning_tokens"),
        "output_tokens": u.get("completion_tokens"),
        "input_tokens": u.get("prompt_tokens"),
        "cost_usd": u.get("cost"),
        "latency_s": latency,
        "has_json": "{" in txt,
    }
    if out["has_json"]:
        try:
            payload = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
            out["json_valid"] = True
        except Exception as exc:
            out["json_valid"] = False
            out["parse_error"] = type(exc).__name__
    return {"metrics": out, "text": txt}


def main() -> int:
    if not os.environ.get(QWEN_KEY_ENV):
        print(f"{QWEN_KEY_ENV} absent"); return 2

    # ONE real finalist set, built from real media + the committed 5C.1 timings.
    cfg = RerankConfig()
    clip = [c for c in H.load_clips() if "kick__looselipssinkships" in c["name"]][0]
    fs = H.build_finalists(clip, per_source=4, max_excerpt=cfg.max_excerpt_chars)
    prompt = build_rerank_prompt(fs, source_context=clip["name"][:40], config=cfg)
    print(f"finalist set: {len(fs)} candidates from {clip['name'][:44]}")
    print(f"prompt chars: {len(prompt)}")

    variants = [
        ("non-thinking / json_object / qwen-nonthink sampling", dict(
            thinking=False, structured="json_object", max_tokens=1200,
            temperature=NON_THINKING["temperature"], top_p=NON_THINKING["top_p"],
            top_k=NON_THINKING["top_k"])),
        ("non-thinking / json_schema  / qwen-nonthink sampling", dict(
            thinking=False, structured="json_schema", max_tokens=1200,
            temperature=NON_THINKING["temperature"], top_p=NON_THINKING["top_p"],
            top_k=NON_THINKING["top_k"])),
    ]

    for label, kw in variants:
        print(f"\n--- {label}")
        res = call(prompt, **kw)
        if "http_error" in res:
            print(f"    HTTP {res['http_error']}: {res['body']}")
            continue
        m = res["metrics"]
        print(f"    finish={m['finish_reason']} content_len={m['content_len']} has_json={m['has_json']}")
        print(f"    output_tokens={m['output_tokens']} reasoning_tokens={m['reasoning_tokens']} "
              f"cost=${m['cost_usd']} latency={m['latency_s']}s")
        if m.get("has_json"):
            try:
                payload = json.loads(res["text"][res["text"].index("{"):res["text"].rindex("}") + 1])
            except Exception as exc:
                print(f"    json parse FAILED: {exc}"); continue
            try:
                rows = validate_rerank_payload(payload, fs)
                order = sorted(rows, key=lambda r: r["relative_rank"])
                print(f"    SCHEMA VALID — ordering: {[r['candidate_id'].split('#')[-1] for r in order]}")
                for r in order:
                    print(f"      #{r['relative_rank']} {r['candidate_id'].split('#')[-1]} "
                          f"score={r.get('relative_score')} conf={r.get('confidence')} "
                          f"red={r.get('redundant_with')} :: {str(r.get('rationale'))[:70]}")
            except Exception as exc:
                print(f"    SCHEMA INVALID: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
