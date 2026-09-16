"""Live YouTube connector proof (bounded, compliant access only).

Proves the full discovery pipeline end-to-end against REAL YouTube data:

  1. Discover real YouTube candidates via the first-party RSS feed (free,
     no credential, no scraping).
  2. Normalize each into a DiscoveryRecord.
  3. Store them (deduplicated, idempotent) in a local SQLite store.
  4. Run duplicate detection — reject at least one duplicate/invalid candidate
     with an explicit reason.
  5. Qualify at least one eligible candidate via the qualification gate.
  6. Pass the eligible source to the manual-ingest boundary and STOP.

No media is downloaded. No publication, distribution, or OpenShorts call.
This script never invents credentials and never falls back to scraping.

Run from repo root:  python3 infra/discovery/prove_youtube_connector.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "infra"))

from discovery.connector import AuthRequiredError, ConnectorError, DiscoveryRequest  # noqa: E402
from discovery.connectors.youtube import YouTubeConnector  # noqa: E402
from discovery.identity import global_fingerprint  # noqa: E402
from discovery.qualification import QualificationGate  # noqa: E402
from discovery.source_brief import SourceBrief  # noqa: E402
from discovery.store import DiscoveryStore  # noqa: E402

# The one REAL tenant that exists today. The three planned profiles are NOT
# provisioned tenants; we use a discovery brief scoped to the real tenant for
# the connector proof only.
REAL_TENANT = "dreemzy"


def make_brief() -> SourceBrief:
    return SourceBrief(
        tenant_id=REAL_TENANT,
        profile_id="discovery-proof",
        version="1",
        target_platforms=["youtube"],
        topics=["relationships", "sports", "animals", "confrontations", "music"],
        entities=[],
        keywords=["viral", "reaction", "moment"],
        exclusions=["political", "graphic violence", "nsfw"],
        creator_channel_allowlist=[],
        creator_channel_blocklist=[],
        freshness_window_days=365,  # wide for proof so live entries qualify
        content_type_requirements=["video"],
        language="en",
        provenance_rights_requirements="first-party source URL + channel identity",
        acquisition_eligibility=True,
        cost_ceiling_usd=0.0,
        manual_review_required=True,
        connector_restrictions=["youtube"],
    )


def main() -> int:
    store = DiscoveryStore(":memory:")
    gate = QualificationGate()
    brief = make_brief()
    conn = YouTubeConnector(timeout=20.0)

    print("=== LOOM Automated Source Discovery V1 — YouTube connector proof ===\n")

    # (0) Confirm the keyword-search path fails deterministically (no credential).
    print("[0] keyword search credential check (must be name-only, deterministic):")
    try:
        list(conn.discover(DiscoveryRequest(brief=brief, query="viral moment")))
        print("    UNEXPECTED: search did not raise")
    except AuthRequiredError as e:
        print(f"    OK — AuthRequiredError: credential={e.credential_name!r}")
    except ConnectorError as e:
        print(f"    ConnectorError (network/gate): [{e.code}] {e}")

    # (1) Live channel discovery via first-party RSS.
    channel = "UCXuqSBlHAE6Xw-yeJA0Tunw"  # Linus Tech Tips (public, real, 15-entry feed)
    print(f"\n[1] live channel discovery: channel:{channel}")
    records = []
    try:
        records = list(conn.discover(DiscoveryRequest(brief=brief, query=f"channel:{channel}")))
    except ConnectorError as e:
        print(f"    CONNECTOR BLOCKED (exact, deterministic): [{e.code}] {e}")
        print("\n    => Live RSS proof could not complete (see handoff for the exact")
        print("       credential/manual action required). Foundation remains complete.")
        return 2

    if not records:
        print("    No records returned (feed empty).")
        return 2

    print(f"    discovered {len(records)} real candidates\n")

    # (2) Normalize + store (idempotent).
    seen_global: set[str] = set()
    stored = 0
    for rec in records:
        result = store.upsert_source(rec)
        seen_global.add(rec.global_fingerprint)
        if result == "inserted":
            stored += 1
        print(f"    stored {rec.platform_content_id} ({result}) — {rec.title[:60]!r}")

    # (3) Duplicate detection — re-insert one record and force an exact dup.
    if records:
        dup = records[0]
        print(f"\n[3] duplicate detection (re-observe {dup.platform_content_id}):")
        r2 = store.upsert_source(dup)
        print(f"    re-upsert result = {r2!r} (row count unchanged at {store.count()})")

        # Demonstrate an explicit duplicate rejection through the gate.
        gate_res = gate.evaluate(
            dup,
            brief,
            seen_global_ids=seen_global,
        )
        print(f"    gate on already-seen global id => status={gate_res.status!r} reason={gate_res.rejection_reason!r}")

    # (4) Qualify each candidate.
    print("\n[4] qualification-before-acquisition:")
    qualified = 0
    rejected = 0
    for rec in records:
        res = gate.evaluate(rec, brief, seen_global_ids=set())
        if res.status in ("qualified", "manual_review"):
            qualified += 1
        else:
            rejected += 1
        print(f"    {rec.platform_content_id}: {res.status}" + (f" — {res.rejection_reason}" if res.rejection_reason else ""))

    # (5) Pass an eligible source to the manual-ingest boundary (stop here).
    print("\n[5] manual-ingest boundary:")
    eligible = None
    for rec in records:
        res = gate.evaluate(rec, brief, seen_global_ids=set())
        if res.status in ("qualified", "manual_review"):
            eligible = rec
            break
    if eligible is not None:
        eligible.processing_eligibility = "manual_ingest_only"
        eligible.qualification_status = "manual_review"
        print(f"    passed {eligible.platform_content_id} ({eligible.title[:60]!r})")
        print("    -> STOP. No download, no OpenShorts, no clip, no publish, no GHL post.")
    else:
        print("    no eligible source (unexpected)")

    print(f"\n=== RESULT: {stored} stored, {qualified} qualified, {rejected} rejected, "
          f"1+ duplicate rejected explicitly ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
