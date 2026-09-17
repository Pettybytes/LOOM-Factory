"""Topic Intelligence V1 — Supabase-backed acceptance proof.

Runs the FULL discovery + scoring pipeline against real YouTube data
(first-party RSS), persisting to the PRODUCTION Supabase store, and asserts
every Topic Intelligence acceptance criterion. Mirrors prove_topic_intelligence.py
(local SQLite) but validates the durable production path now that migrations
022 (loom_topic_assessments) and 023 (content_format) are applied.

Uses a non-production proof profile that is auto-excluded from production
queues. No DDL, no media download, no paid model calls, no publication, no
distribution.

Run from repo root:  python3 infra/discovery/prove_topic_intelligence_supabase.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "infra"))

from discovery.connector import ConnectorError, DiscoveryRequest  # noqa: E402
from discovery.connectors.youtube import YouTubeConnector  # noqa: E402
from discovery.connectors.reddit import RedditConnector  # noqa: E402
from discovery.qualification import QualificationGate  # noqa: E402
from discovery.source_brief import SourceBrief, is_non_production  # noqa: E402
from discovery.store_supabase import SupabaseStore  # noqa: E402
from discovery.topic_intelligence import TopicIntelligence  # noqa: E402

REAL_TENANT = "dreemzy"
PROOF_PROFILE = "topic-intelligence-acceptance-proof"  # non-production, auto-excluded
YOUTUBE_CHANNEL = "UCXuqSBlHAE6Xw-yeJA0Tunw"  # Linus Tech Tips (real, public)

_PASS: list[str] = []
_FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (_PASS if ok else _FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def make_brief(platforms) -> SourceBrief:
    return SourceBrief(
        tenant_id=REAL_TENANT,
        profile_id=PROOF_PROFILE,
        version="1",
        target_platforms=platforms,
        topics=["technology", "reviews", "gaming", "pets", "animals"],
        entities=[],
        keywords=["ai", "review", "gaming", "puppy", "rescue"],
        exclusions=["political", "graphic", "nsfw"],
        creator_channel_allowlist=[],
        creator_channel_blocklist=[],
        freshness_window_days=365,
        content_type_requirements=["video", "post"],
        language="en",
        provenance_rights_requirements="first-party RSS",
        acquisition_eligibility=True,
        cost_ceiling_usd=0.0,
        manual_review_required=True,
        connector_restrictions=[],
    )


def main() -> int:
    store = SupabaseStore()
    gate = QualificationGate()
    ti = TopicIntelligence()

    print("=== LOOM Topic Intelligence V1 — Supabase-backed acceptance proof ===\n")

    print("[0] proof profile classification:")
    check("profile auto-excluded from production", is_non_production(PROOF_PROFILE, REAL_TENANT), PROOF_PROFILE)

    print("[1] schema (migrations 022/023):")
    try:
        ta = store._request("GET", "loom_topic_assessments?select=duplicate_fingerprint&limit=1")
        check("loom_topic_assessments exists", isinstance(ta, list), "table present")
    except Exception as e:
        check("loom_topic_assessments exists", False, str(e))
    try:
        cf = store._request("GET", "loom_discovered_sources?select=content_format&limit=1")
        check("content_format column exists", isinstance(cf, list), "column present")
    except Exception as e:
        check("content_format column exists", False, str(e))

    # 2. Discover real candidates via first-party RSS.
    print("\n[2] discover real candidates via first-party RSS:")
    brief = make_brief(["youtube", "reddit"])
    yt = YouTubeConnector(timeout=20.0)
    records = []
    try:
        records = list(yt.discover(DiscoveryRequest(brief=brief, query=f"channel:{YOUTUBE_CHANNEL}")))
    except ConnectorError as e:
        print(f"    youtube blocked: [{e.code}] {e}")
    check("discovered > 0 candidates", len(records) > 0, f"n={len(records)}")
    if not records:
        print("    => no real data; abort (report exact manual action, no scraping).")
        return 2

    # 3. Normalize + store to Supabase (idempotent).
    print("\n[3] store to Supabase (loom_discovered_sources + content_format):")
    inserted = sum(1 for r in records if store.upsert_source(r) == "inserted")
    # Idempotency: re-discovering previously-seen content is an UPDATE, not an
    # insert. The real invariant is that every record is now present.
    all_present = all(store.contains_fingerprint(r.duplicate_fingerprint) for r in records)
    check("all records persisted", all_present,
          f"inserted={inserted}, updated={len(records) - inserted}")

    # 4. Replay idempotency (sources).
    print("\n[4] source replay idempotency:")
    before = store.count()
    for r in records:
        store.upsert_source(r)
    after = store.count()
    check("no duplicate source rows on replay", before == after, f"{before} -> {after}")

    # 5. Assess + store deterministically.
    print("\n[5] deterministic scoring + assessment store:")
    seen_ok = True
    for r in records:
        comp = store.saturation_count(r.platform, r.title, exclude_fingerprint=r.duplicate_fingerprint)
        a1 = ti.assess(r, brief, competition_count=comp)
        a2 = ti.assess(r, brief, competition_count=comp)
        if a1.opportunity_score != a2.opportunity_score or a1.to_dict()["reasons"] != a2.to_dict()["reasons"]:
            seen_ok = False
        store.upsert_assessment(a1)
    check("deterministic scoring (all candidates)", seen_ok, f"n={len(records)}")
    check("assessments stored", store.assessment_count() >= len(records),
          f"{store.assessment_count()} rows")

    # 6. Assessment replay idempotency.
    print("\n[6] assessment replay idempotency:")
    before = store.assessment_count()
    for r in records:
        comp = store.saturation_count(r.platform, r.title, exclude_fingerprint=r.duplicate_fingerprint)
        store.upsert_assessment(ti.assess(r, brief, competition_count=comp))
    after = store.assessment_count()
    check("no duplicate assessment rows on replay", before == after, f"{before} -> {after}")

    # 7. Duplicate evidence (explicit rejection at the gate).
    print("\n[7] duplicate evidence:")
    stored = store.all_sources()
    fingerprints = {s["duplicate_fingerprint"] for s in stored}
    global_ids = {s["global_fingerprint"] for s in stored if s.get("global_fingerprint")}
    dup_res = gate.evaluate(records[0], brief, seen_fingerprints=fingerprints, seen_global_ids=global_ids)
    check("replayed candidate rejected as duplicate", dup_res.status == "rejected", dup_res.rejection_reason)

    # 8. Manual-ingest boundary preserved.
    print("\n[8] manual-ingest boundary:")
    proof_sources = [s for s in stored if s.get("profile_id") == PROOF_PROFILE]
    check("all proof records manual_ingest_only",
          all(s["processing_eligibility"] == "manual_ingest_only" for s in proof_sources),
          f"{sum(1 for s in proof_sources if s['processing_eligibility']=='manual_ingest_only')}/{len(proof_sources)}")

    # 9. Production-queue exclusion.
    print("\n[9] production-queue exclusion:")
    prod_assess = store.production_assessments()
    leak = [a for a in prod_assess if a.get("profile_id") == PROOF_PROFILE]
    check("proof profile absent from production assessments", len(leak) == 0, f"{len(leak)} leaked")

    # 10. No side effects.
    print("\n[10] no download/OpenShorts/publication/distribution:")
    forbidden = ("openshorts", "retell", "ghl", "distribution", "social", "elevenlabs")
    imported = [m for m in sys.modules if any(f in m.lower() for f in forbidden)]
    check("no forbidden modules imported", not imported, ",".join(imported) if imported else "none")

    print("\n=== RESULT ===")
    print(f"  PASS: {len(_PASS)}  FAIL: {len(_FAIL)}")
    if _FAIL:
        print("  FAILED:", ", ".join(_FAIL))
        return 1
    print("  ALL SUPABASE ACCEPTANCE CRITERIA PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
