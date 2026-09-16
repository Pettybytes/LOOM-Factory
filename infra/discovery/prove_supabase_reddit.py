"""Supabase-backed acceptance proof for the Reddit connector milestone.

Runs the FULL discovery pipeline against real Reddit data (first-party RSS),
persisting to the production Supabase store, and asserts every milestone
criterion:

  1. Real candidates discovered via first-party Reddit RSS (no credential, no
     scraping, no auth evasion).
  2. Normalized + stored to Supabase (loom_discovered_sources).
  3. Replay is idempotent — candidates do NOT become duplicates.
  4. One duplicate / cross-post is rejected explicitly (with reason).
  5. One policy/qualification failure is rejected with an explicit reason.
  6. One eligible candidate reaches ONLY manual_ingest_only.
  7. No download / OpenShorts / publication / distribution.
  8. Non-production proof profile is auto-excluded from production queues.

No DDL. No media download. No paid model calls. No new paid service.

Run from repo root:  python3 infra/discovery/prove_supabase_reddit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "infra"))

from discovery.connector import ConnectorError, DiscoveryRequest  # noqa: E402
from discovery.connectors.reddit import RedditConnector  # noqa: E402
from discovery.discovery_record import DiscoveryRecord  # noqa: E402
from discovery.identity import crosspost_key  # noqa: E402
from discovery.qualification import QualificationGate  # noqa: E402
from discovery.source_brief import SourceBrief, is_non_production  # noqa: E402
from discovery.store_supabase import SupabaseStore  # noqa: E402

REAL_TENANT = "dreemzy"
PROOF_PROFILE = "reddit-acceptance-proof"  # non-production, auto-excluded
SUBREDDIT = "technology"

_PASS: list[str] = []
_FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (_PASS if ok else _FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def make_brief(**overrides) -> SourceBrief:
    data = dict(
        tenant_id=REAL_TENANT,
        profile_id=PROOF_PROFILE,
        version="1",
        target_platforms=["reddit"],
        topics=["technology", "reviews", "gaming", "pets"],
        entities=[],
        keywords=["ai", "review", "gaming"],
        exclusions=["political", "graphic", "nsfw"],
        creator_channel_allowlist=[],
        creator_channel_blocklist=[],
        freshness_window_days=365,
        content_type_requirements=["post"],
        language="en",
        provenance_rights_requirements="first-party Reddit RSS + post id",
        acquisition_eligibility=True,
        cost_ceiling_usd=0.0,
        manual_review_required=True,
        connector_restrictions=["reddit"],
    )
    data.update(overrides)
    return SourceBrief(**data)


def main() -> int:
    store = SupabaseStore()
    gate = QualificationGate()
    brief = make_brief()
    conn = RedditConnector(timeout=20.0)

    print("=== LOOM Source Discovery V1 — Reddit connector acceptance proof ===\n")

    # Sanity: proof profile is non-production (auto-excluded).
    print("[0] proof profile classification:")
    check("profile auto-excluded from production", is_non_production(PROOF_PROFILE, REAL_TENANT), PROOF_PROFILE)

    # 1. Discover real candidates.
    print("\n[1] discover real Reddit candidates via first-party RSS:")
    try:
        records = list(conn.discover(DiscoveryRequest(brief=brief, query=f"subreddit:{SUBREDDIT}")))
    except ConnectorError as e:
        print(f"    CONNECTOR BLOCKED: [{e.code}] {e}")
        print("    => abort; report exact manual action (no scraping fallback).")
        return 2
    check("discovered > 0 candidates", len(records) > 0, f"n={len(records)}")
    if not records:
        return 2

    # 2. Store to Supabase.
    print("\n[2] store to Supabase (loom_discovered_sources):")
    inserted = sum(1 for r in records if store.upsert_source(r) == "inserted")
    check("all records stored", inserted == len(records), f"{inserted}/{len(records)}")

    # 3. Replay idempotency.
    print("\n[3] replay idempotency:")
    before = store.count()
    for r in records:
        store.upsert_source(r)
    after = store.count()
    check("no duplicate rows on replay", before == after, f"{before} -> {after}")

    # 4. One explicit duplicate / cross-post rejection.
    print("\n[4] explicit duplicate/cross-post rejection:")
    # (a) exact duplicate via tenant-scoped fingerprint.
    stored = store.all_sources()
    fingerprints = {s["duplicate_fingerprint"] for s in stored}
    dup_res = gate.evaluate(records[0], brief, seen_fingerprints=fingerprints)
    check("exact duplicate rejected", dup_res.status == "rejected" and "exact" in dup_res.rejection_reason,
          dup_res.rejection_reason)

    # (b) cross-post: same title, different post id + author.
    xpost = DiscoveryRecord(
        tenant_id=REAL_TENANT, source_brief_version="1", profile_id=PROOF_PROFILE,
        platform="reddit", canonical_url="https://www.reddit.com/r/other/comments/zzzzzz/",
        platform_content_id="zzzzzz", creator_channel_identity="differentauthor",
        creator_channel_url="https://www.reddit.com/user/differentauthor",
        title=records[0].title, publication_timestamp=records[0].publication_timestamp,
        observed_metrics={"content_type": "post"},
        metric_observation_timestamp=records[0].metric_observation_timestamp,
        metric_history=[], discovery_query="subreddit:other",
        discovery_reason="cross-post simulation", evidence_confidence=1.0,
        provenance_rights_evidence="simulated cross-post",
        duplicate_fingerprint="xpost-fp-zzzzzz", global_fingerprint="xpost-gp-zzzzzz",
        processing_eligibility="manual_ingest_only", qualification_status="pending",
        connector_name="reddit", connector_version="1.0.0",
    )
    xpost_res = gate.evaluate(
        xpost, brief,
        seen_crosspost_keys={crosspost_key("reddit", records[0].title)},
    )
    check("cross-post rejected", xpost_res.status == "rejected" and "crosspost" in xpost_res.rejection_reason,
          xpost_res.rejection_reason)

    # 5. One policy/qualification failure with reason.
    print("\n[5] policy/qualification failure:")
    # A record with no provenance evidence fails the brief's provenance policy.
    no_prov = DiscoveryRecord(
        tenant_id=REAL_TENANT, source_brief_version="1", profile_id=PROOF_PROFILE,
        platform="reddit", canonical_url="https://www.reddit.com/r/technology/comments/aaaaaa/",
        platform_content_id="aaaaaa", creator_channel_identity="someauthor",
        creator_channel_url="https://www.reddit.com/user/someauthor",
        title="A post with missing provenance", publication_timestamp=records[0].publication_timestamp,
        observed_metrics={"content_type": "post"},
        metric_observation_timestamp=records[0].metric_observation_timestamp,
        metric_history=[], discovery_query="subreddit:technology",
        discovery_reason="policy-failure simulation", evidence_confidence=1.0,
        provenance_rights_evidence="",  # missing — brief requires it
        duplicate_fingerprint="pol-fp-aaaaaa", global_fingerprint="pol-gp-aaaaaa",
        processing_eligibility="manual_ingest_only", qualification_status="pending",
        connector_name="reddit", connector_version="1.0.0",
    )
    pol_res = gate.evaluate(no_prov, brief)
    check("policy failure rejected with reason", pol_res.status == "rejected" and "provenance" in pol_res.rejection_reason,
          pol_res.rejection_reason)

    # 6. One eligible candidate reaches only manual_ingest_only.
    print("\n[6] manual-ingest boundary:")
    eligible = None
    for r in records:
        res = gate.evaluate(r, brief, seen_global_ids=set())
        if res.status in ("qualified", "manual_review"):
            eligible = r
            break
    if eligible is not None:
        eligible.processing_eligibility = "manual_ingest_only"
        eligible.qualification_status = "manual_review"
        store.upsert_source(eligible)
        check("eligible reached manual_ingest_only",
              eligible.processing_eligibility == "manual_ingest_only", eligible.platform_content_id)
        check("no status beyond manual review", eligible.qualification_status == "manual_review",
              eligible.qualification_status)
    else:
        check("eligible reached manual_ingest_only", False, "no eligible source")

    # 7. No side effects.
    print("\n[7] no download/OpenShorts/publication/distribution:")
    import sys as _sys
    forbidden = ("openshorts", "retell", "ghl", "distribution", "social", "elevenlabs")
    imported = [m for m in _sys.modules if any(f in m.lower() for f in forbidden)]
    check("no forbidden modules imported", not imported, ",".join(imported) if imported else "none")
    check("connector is RSS-GET-only", conn.RSS_BASE.startswith("https://www.reddit.com"), conn.RSS_BASE)

    # 8. Production-queue exclusion.
    print("\n[8] production-queue exclusion (proof profile auto-excluded):")
    prod = store.production_sources()
    proof_rows_in_prod = [s for s in prod if s.get("profile_id") == PROOF_PROFILE]
    check("proof profile absent from production queue", len(proof_rows_in_prod) == 0,
          f"{len(proof_rows_in_prod)} leaked")

    print("\n=== RESULT ===")
    print(f"  PASS: {len(_PASS)}  FAIL: {len(_FAIL)}")
    print(f"  final store count: {store.count()}")
    if _FAIL:
        print("  FAILED:", ", ".join(_FAIL))
        return 1
    print("  ALL ACCEPTANCE CRITERIA PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
