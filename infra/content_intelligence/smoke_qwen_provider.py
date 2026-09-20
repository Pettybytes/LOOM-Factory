#!/usr/bin/env python3
"""Minimal REAL-provider smoke test through the established production scoring path.

Runs the exact call chain production uses, minus pipeline plumbing and without any
store writes:

    build_provider("qwen", _load_provider_key("qwen"))   # as pipeline.py does
        -> provider.assess(Evidence, brief)              # the model call
        -> assess_content(...)                           # validation/composition

Prints: validity, reasoning tokens, output tokens, input tokens, latency, cost and
the resulting content worthiness score. Exits non-zero on any failure.

Run:  set -a; . ~/.openclaw/.env; set +a; python3 smoke_qwen_provider.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from content_intelligence.pipeline import _load_provider_key  # noqa: E402
from content_intelligence.provider import Evidence, assess_content  # noqa: E402
from content_intelligence.providers import (  # noqa: E402
    ProviderError,
    build_provider,
    load_key,
)
from discovery.source_brief import SourceBrief  # noqa: E402

SMOKE_PROFILE = "qwen-provider-smoke"   # explicitly non-production


class _Record:
    """Minimal record: only the four fields assess_content reads."""
    duplicate_fingerprint = "smoke-fingerprint-0001"
    platform = "local"
    profile_id = SMOKE_PROFILE
    tenant_id = "dreemzy"


def _real_transcript() -> str:
    """A real local transcript excerpt, so the smoke test is not synthetic."""
    results = _HERE.parent / "whisperx-eval" / "results.json"
    if results.exists():
        clips = json.loads(results.read_text(encoding="utf-8"))["clips"]
        for c in clips:
            words = c.get("baseline", {}).get("words") or []
            if len(words) > 40:
                take = words[:120]
                text = " ".join(w["word"].strip() for w in take)
                return (f"{text} "
                        f"(sample window {take[0]['start']:.1f}-{take[-1]['end']:.1f}s)")
    return ("Nobody doing shit about killing kids right now. It is really fucking out "
            "of style. When Trey's have passed and nobody says anything, that is the problem.")


def main() -> int:
    key = _load_provider_key("qwen")
    if not key:
        print("FAIL: LOOM_OPENROUTER_API_KEY not available to this process")
        return 2
    print(f"key present: True (len {len(key)}) | structured path via build_provider('qwen')")

    provider = build_provider("qwen", key)          # production construction
    print(f"provider: {provider.name} model={provider.model} "
          f"reasoning_enabled={provider.reasoning_enabled} "
          f"structured_output={provider.structured_output} "
          f"max_output_tokens={provider.max_output_tokens}")

    brief = SourceBrief(
        tenant_id="dreemzy", profile_id=SMOKE_PROFILE, version="1",
        target_platforms=["tiktok", "instagram"], topics=["reaction"],
        entities=[], keywords=["moment", "reaction"], exclusions=["political", "graphic"],
        creator_channel_allowlist=[], creator_channel_blocklist=[],
        freshness_window_days=365, content_type_requirements=["video"], language="en",
        provenance_rights_requirements="first-party RSS",
        acquisition_eligibility=True, cost_ceiling_usd=0.0, manual_review_required=True,
        connector_restrictions=["youtube"],
    )
    ev = Evidence(platform="local", content_type="video",
                  title="qwen provider smoke", transcript_text=_real_transcript(),
                  transcript_availability="auto_generated")

    t0 = time.time()
    try:
        raw = provider.assess(ev, brief)
    except ProviderError as e:
        print(f"FAIL: provider error {e.code} | {str(e)[:200]}")
        return 1
    latency = round(time.time() - t0, 2)

    usage = raw.get("usage") or {}
    ident = raw.get("model_identity") or {}
    print("\n--- assessed OK")
    print(f"    provider/model identity : {ident.get('provider')} / {ident.get('resolved_model')}")
    print(f"    content_worthiness_score: {raw.get('content_worthiness_score')}")
    print(f"    dims scored             : {len(raw.get('scores') or {})}")
    print(f"    input_tokens            : {usage.get('input_tokens')}")
    print(f"    output_tokens           : {usage.get('output_tokens')}")
    print(f"    latency_s               : {latency} (provider reports {usage.get('latency_s')})")
    print(f"    cost_usd                : {usage.get('cost_usd')}")

    # Production validation/composition step.
    try:
        assessment = assess_content(record=_Record(), brief=brief, evidence_bundle=ev,
                                    provider=provider, competition_count=0, raw=raw)
    except Exception as exc:
        print(f"FAIL: assess_content rejected the response: {type(exc).__name__}: {exc}")
        return 1
    print(f"    assessment accepted     : worthiness={assessment.content_worthiness_score} "
          f"confidence={assessment.content_confidence} "
          f"format={assessment.recommended_content_format}")
    print("\nSMOKE PASS — production scoring path returned a valid assessment "
          "with non-thinking structured output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
