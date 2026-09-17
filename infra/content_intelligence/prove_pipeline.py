"""Recurring Pass-1 pipeline — minimal end-to-end runtime proof (non-production).

One real discovered YouTube candidate travels the COMPLETE recurring path:
  discovery (first-party RSS) -> Topic Intelligence -> transcript-only retrieval
  (yt-dlp --skip-download, 0 media bytes) -> real provider (Gemini Flash-Lite)
  -> Content Intelligence -> Combined Source Selection -> idempotent Supabase
  persistence -> production-helper exclusion.

No media download, no Discord, no Acquisition/OpenShorts/publishing. Total new
proof cost < $0.05. Uses a non-production proof profile (auto-excluded).

Run from repo root:  python3 infra/content_intelligence/prove_pipeline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "infra"))

from discovery.connectors.youtube import YouTubeConnector  # noqa: E402
from discovery.connector import DiscoveryRequest  # noqa: E402
from discovery.source_brief import SourceBrief  # noqa: E402
from discovery.topic_intelligence import TopicIntelligence  # noqa: E402
from content_intelligence.pipeline import Pass1Config, Pass1Pipeline, selection_queue  # noqa: E402
from content_intelligence.store_supabase import ContentStoreSupabase  # noqa: E402

REAL_TENANT = "dreemzy"
PROOF_PROFILE = "pass1-pipeline-proof"  # non-production (contains 'proof')
CHANNEL = "UCXuqSBlHAE6Xw-yeJA0Tunw"

_PASS: list[str] = []
_FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (_PASS if ok else _FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=== LOOM Content Intelligence — recurring Pass-1 pipeline proof ===\n")

    store = ContentStoreSupabase()

    brief = SourceBrief(
        tenant_id=REAL_TENANT, profile_id=PROOF_PROFILE, version="1",
        target_platforms=["youtube"], topics=["technology", "reviews"], entities=[],
        keywords=["review", "smart home", "tech"], exclusions=["political", "graphic", "nsfw"],
        creator_channel_allowlist=[], creator_channel_blocklist=[],
        freshness_window_days=365, content_type_requirements=["video"], language="en",
        provenance_rights_requirements="first-party RSS",
        acquisition_eligibility=True, cost_ceiling_usd=0.0, manual_review_required=True,
        connector_restrictions=["youtube"],
    )

    print("[1] discovery (first-party RSS):")
    conn = YouTubeConnector(timeout=20.0)
    records = list(conn.discover(DiscoveryRequest(brief=brief, query=f"channel:{CHANNEL}")))
    check("discovered > 0 candidates", len(records) > 0, f"n={len(records)}")
    if not records:
        return 2

    # Count existing proof rows (for idempotency baseline).
    base_assess = _count_profile("loom_content_assessments", PROOF_PROFILE)
    base_sel = _count_profile("loom_source_selections", PROOF_PROFILE)

    print("[2] recurring pipeline (topic -> transcript -> content -> selection -> persist):")
    config = Pass1Config(max_candidates=1, market_shortlist_threshold=0, cost_ceiling_usd=0.05)
    ti = TopicIntelligence()
    pipe = Pass1Pipeline(config, brief, ti, provider_name="gemini")
    result = pipe.run(records, store)
    check("one candidate scored via recurring path", result.scored >= 1, result.summary())

    c = result.candidates[0] if result.candidates else None
    check("candidate completed full path",
          c is not None and c.status == "scored",
          f"{c.platform_content_id if c else 'none'} market={c.market_score if c else '?'} "
          f"worthiness={c.content_worthiness if c else '?'} selection={c.selection_score if c else '?'} "
          f"outcome={c.selection_outcome if c else '?'}")

    print("[3] idempotent replay:")
    pipe.run(records, store)
    after_assess = _count_profile("loom_content_assessments", PROOF_PROFILE)
    after_sel = _count_profile("loom_source_selections", PROOF_PROFILE)
    check("assessment replay idempotent (no dup)",
          after_assess == base_assess + (1 if result.scored else 0), f"{base_assess}->{after_assess}")
    check("selection replay idempotent (no dup)",
          after_sel == base_sel + (1 if result.scored else 0), f"{base_sel}->{after_sel}")

    print("[4] production-helper exclusion:")
    prod = store.production_selections()
    leak = [s for s in prod if s.get("profile_id") == PROOF_PROFILE]
    check("proof profile absent from production queue", len(leak) == 0, f"{len(leak)} leaked")
    check("selection_queue returns completed only",
          all(s.get("outcome") for s in selection_queue(prod)))

    print("[5] cost + boundary guards:")
    check("total cost < $0.05 ceiling", result.total_cost_usd < 0.05,
          f"${result.total_cost_usd:.5f}")
    forbidden = ("openshorts", "discord", "ghl", "distribution", "social", "elevenlabs", "retell")
    imported = [m for m in sys.modules if any(f in m.lower() for f in forbidden)]
    check("no forbidden modules imported", not imported, ",".join(imported) if imported else "none")

    print("\n=== RESULT ===")
    print(f"  {result.summary()}")
    print(f"  PASS: {len(_PASS)}  FAIL: {len(_FAIL)}")
    if _FAIL:
        print("  FAILED:", ", ".join(_FAIL))
        return 1
    print("  ALL RECURRING-PIPELINE ACCEPTANCE CRITERIA PASSED")
    return 0


def _count_profile(table: str, profile_id: str) -> int:
    import json
    import os
    import urllib.request
    from content_intelligence.store_supabase import load_supabase_creds
    url, key = load_supabase_creds()
    req = urllib.request.Request(
        f"{url}/rest/v1/{table}?profile_id=eq.{profile_id}&select=idempotency_key",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.loads(r.read().decode() or "[]")
    return len(rows) if isinstance(rows, list) else 0


if __name__ == "__main__":
    sys.exit(main())
