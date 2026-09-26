"""Manual Intelligence Intake V1.2 — Content Intelligence on SUPPLIED media.

This is the seam V1 lacked: a durable manual submission (media already stored in
GHL) becomes real intelligence and a review candidate, by invoking the EXISTING
Content Intelligence runtime — no parallel analyzer, no new scoring model.

Two behaviours that matter:
  * No timestamps supplied -> the existing pipeline locates the moment and the
    candidate keeps MACHINE boundaries (``boundary_provenance='machine'``).
  * Timestamps supplied -> the operator's window is preserved as the candidate
    boundary (``boundary_provenance='manual_intake'``); the machine result is
    retained as evidence, never silently substituted.

Media is consumed from the SUPPLIED bytes via injected fetchers, so no paid
provider is asked to re-acquire anything.
"""

from __future__ import annotations

import json
import pathlib
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import sys as _sys

# See manual_media_intake: the service runtime does not put infra/ on sys.path,
# but the shared LOOM packages live there.
_INFRA = pathlib.Path(__file__).resolve().parents[1]
if str(_INFRA) not in _sys.path:
    _sys.path.insert(0, str(_INFRA))

MANUAL_PROFILE_ID = "manual-intake"
GEMINI_KEY_NAME = "LOOM_GEMINI_KEY"


def _diag(candidate: Any) -> dict[str, Any]:
    """In-memory candidate facts for observable failures (never silent)."""
    if candidate is None:
        return {}
    return {"status": getattr(candidate, "status", ""),
            "rejection_reason": getattr(candidate, "rejection_reason", ""),
            "error": getattr(candidate, "error", ""),
            "completeness_verdict": (getattr(candidate, "completeness", None) or {}).get(
                "completeness_verdict", ""),
            "has_boundary": bool(getattr(candidate, "boundary_plan", None)),
            "content_score": getattr(candidate, "content_score_recalibrated", None),
            "media_assessment_status": getattr(candidate, "media_assessment_status", "")}


def manual_brief(tenant_id: str, *, profile_id: str = MANUAL_PROFILE_ID,
                 intent: str = "operator-supplied content for human review") -> Any:
    """A tenant-scoped brief for operator-supplied media.

    Theme concepts are intentionally empty: the human already chose the source,
    so there is no discovery shortlist to theme-filter. Safety and evidence gates
    are unchanged (adults_only, nudity/explicit, directly-observed event).
    """
    from discovery.source_brief import SourceBrief
    return SourceBrief.from_dict({
        "schema_version": "1.0.0",
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "version": f"{profile_id}-v1",
        "target_platforms": ["youtube", "reddit", "tiktok", "instagram", "twitch", "kick"],
        "topics": [intent],
        "entities": [],
        "keywords": [],
        "exclusions": ["minors", "ambiguous age", "nudity", "explicit sexual activity"],
        "creator_channel_allowlist": [],
        "creator_channel_blocklist": [],
        "freshness_window_days": 0.0,
        "content_type_requirements": [],
        "language": "en",
        "provenance_rights_requirements": "operator-supplied; source-review only",
        "acquisition_eligibility": False,
        "cost_ceiling_usd": 0.05,
        "manual_review_required": True,
        "connector_restrictions": [],
        "min_evidence_requirements": [],
        "mining_requirements": {
            "intent": intent,
            "required_concepts": [],
            "adults_only": True,
            "review_policy": {"mode": "human_curated", "media_first": True,
                              "minimum_content": 50},
            "market_policy": "observed_demand_v1",
            "require_direct_event": True,
            "require_timed_payoff": True,
        },
    })


def build_record(submission: dict[str, Any], probe: dict[str, Any],
                 *, brief: Any, media_url: str) -> Any:
    """A DiscoveryRecord for supplied media, carrying the durable media pointer."""
    from discovery.discovery_record import DiscoveryRecord
    import hashlib

    url = submission["source_url"]
    platform = submission.get("platform") or ""
    fingerprint = submission["duplicate_fingerprint"]
    metrics = {
        "content_type": "clip",
        "media_type": "video",
        "is_video": True,
        "duration_seconds": probe.get("duration_seconds"),
        "width": probe.get("width"),
        "height": probe.get("height"),
        "supplied_media": True,
        "supplied_media_url": media_url,
        "media_sha256": probe.get("sha256"),
    }
    return DiscoveryRecord(
        tenant_id=submission["tenant_id"], profile_id=brief.profile_id,
        source_brief_version=brief.version, platform=platform,
        canonical_url=url, platform_content_id=submission.get("platform_content_id") or "",
        creator_channel_identity=submission.get("submitter") or "manual",
        creator_channel_url="", title=(submission.get("reviewer_note") or url)[:200],
        publication_timestamp=None, metric_observation_timestamp=None,
        observed_metrics=metrics, metric_history=[],
        discovery_query="", discovery_reason="operator-supplied media via Manual Intake",
        provenance_rights_evidence="operator-supplied media; source-review only; no acquisition",
        evidence_confidence="", duplicate_fingerprint=fingerprint,
        global_fingerprint=hashlib.sha256(url.encode()).hexdigest(),
        processing_eligibility="manual_intake", qualification_status="pending",
        connector_name="manual_media_intake", connector_version="1",
        body_excerpt="",
    )


def _provider():
    """The existing Gemini provider, loaded by key NAME (never printed)."""
    from content_intelligence.providers import GeminiProvider, load_key
    root = pathlib.Path(__file__).resolve().parents[2]
    return GeminiProvider(load_key(GEMINI_KEY_NAME, str(root / "infra/tunnel-proxy/.env")),
                          max_output_tokens=3500)


def run_manual_intelligence(*, submission: dict[str, Any], media_path: str,
                            probe: dict[str, Any], media_url: str,
                            brief: Any | None = None, provider: Any = None,
                            store: Any = None, human_window: tuple[float | None, float | None] = (None, None),
                            cost_ceiling_usd: float = 0.05) -> dict[str, Any]:
    """Invoke the EXISTING Content Intelligence runtime on supplied media.

    Returns a summary with the candidate id/status and boundary provenance, or
    raises with a named stage so the caller can report an observable failure.
    """
    from content_intelligence.pipeline import Pass1Config, Pass1Pipeline
    from discovery.topic_intelligence import TopicIntelligence
    import manual_media_intake as mmi

    brief = brief or manual_brief(submission["tenant_id"])
    provider = provider or _provider()
    if store is None:
        from content_intelligence.store_supabase import ContentStoreSupabase
        store = ContentStoreSupabase()

    record = build_record(submission, probe, brief=brief, media_url=media_url)
    # Supplied media has no platform publication timestamp, and an empty string
    # is not a valid timestamptz — NULL is the honest representation. The metric
    # observation timestamp is genuinely "now" (we probed the media now).
    record.publication_timestamp = None
    if not record.metric_observation_timestamp:
        record.metric_observation_timestamp = datetime.now(timezone.utc).isoformat()

    stage = "pipeline_construct"
    try:
        # The existing runtime expects the brief and the source record to be
        # registered first (the same contract the automated lane uses); skipping
        # it makes the candidate upsert fail with a store error.
        stage = "register_source"
        from discovery.store_supabase import SupabaseStore
        discovery_store = SupabaseStore()
        discovery_store.upsert_brief(brief)
        discovery_store.upsert_source(record)

        stage = "pipeline_construct"
        cfg = Pass1Config(max_candidates=1,
                          # Operator-supplied media has no discovery shortlist, so the
                          # market shortlist gate must not drop it before inspection.
                          market_shortlist_threshold=0,
                          cost_ceiling_usd=cost_ceiling_usd,
                          enable_fire_gate=True)
        pipeline = Pass1Pipeline(cfg, brief, TopicIntelligence(), provider=provider)
        bounded, contiguous = mmi.preview_fetchers(media_path)
        pipeline._inspection_preview = bounded
        pipeline._inspection_contiguous_preview = contiguous
        stage = "pipeline_run"
        result = pipeline.run([record], store)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": stage, "error": f"{type(e).__name__}: {e}"}

    candidates = list(getattr(result, "candidates", []) or [])
    if not candidates:
        return {"ok": False, "stage": "no_candidate",
                "summary": (result.summary() if hasattr(result, "summary") else {}),
                "error": "Content Intelligence produced no candidate for the supplied media"}

    cand = candidates[0]
    start_s, end_s = human_window
    boundary_provenance = "machine"
    plan = dict(getattr(cand, "boundary_plan", {}) or {})
    machine_plan = dict(plan)
    if start_s is not None or end_s is not None:
        # Preserve the operator's window; keep the machine result as evidence.
        plan.update({
            "recommended_clip_start": float(start_s if start_s is not None else 0.0),
            "recommended_clip_end": float(end_s if end_s is not None else plan.get("recommended_clip_end") or 0.0),
            "provenance": ["manual_intake_window", "operator_supplied"],
            "machine_recommendation": machine_plan,
        })
        boundary_provenance = "manual_intake"

    return enrich_manual_candidate(
        submission=submission, probe=probe, media_url=media_url, human_window=human_window,
        boundary_plan=plan, boundary_provenance=boundary_provenance, candidate=cand,
        store=store, summary=(result.summary() if hasattr(result, "summary") else {}))


def enrich_manual_candidate(*, submission: dict[str, Any], probe: dict[str, Any],
                            media_url: str,
                            human_window: tuple[float | None, float | None] = (None, None),
                            boundary_plan: dict[str, Any] | None = None,
                            boundary_provenance: str | None = None,
                            candidate: Any = None, store: Any = None,
                            summary: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply boundary provenance + lineage to the DURABLE candidate row.

    Split out of the analysis run so it is idempotent and retryable WITHOUT
    paying for Content Intelligence again. Preserves the operator's window when
    one was supplied; the machine recommendation stays as evidence.
    """
    if store is None:
        from content_intelligence.store_supabase import ContentStoreSupabase
        store = ContentStoreSupabase()
    plan = dict(boundary_plan or {})
    start_s, end_s = human_window
    if boundary_provenance is None:
        if start_s is not None or end_s is not None:
            plan.update({
                "recommended_clip_start": float(start_s if start_s is not None else 0.0),
                "recommended_clip_end": float(end_s if end_s is not None
                                             else plan.get("recommended_clip_end") or 0.0),
                "provenance": ["manual_intake_window", "operator_supplied"],
                "machine_recommendation": dict(plan),
            })
            boundary_provenance = "manual_intake"
        else:
            boundary_provenance = "machine"

    lineage = {
        "origin": "manual_media_intake",
        "submission_id": submission.get("id"),
        "submission_fingerprint": submission.get("duplicate_fingerprint"),
        "candidate_fingerprint": getattr(candidate, "duplicate_fingerprint", ""),
        "media_reference": (submission.get("media_reference") or {}).get("url") or media_url,
        "media_sha256": probe.get("sha256"),
        "media_already_acquired": True,
        "boundary_provenance": boundary_provenance,
    }
    stage = "persist_enrichment"
    try:
        # The pipeline assigns its own candidate fingerprint (per materialized
        # moment), so resolve the durable row by the authoritative source id the
        # submission carries instead of assuming a shared key scheme.
        content_id = submission.get("platform_content_id") or ""
        rows = store._request(
            "GET", "loom_source_candidates?platform_content_id=eq."
            + urllib.parse.quote(content_id) + "&tenant_id=eq."
            + urllib.parse.quote(submission.get("tenant_id", "")) + "&select=*")
        row = (rows or [None])[0]
        if not row:
            return {"ok": False, "stage": stage,
                    "error": ("Content Intelligence produced a candidate but the durable "
                               "row is absent — the candidate was filtered before persistence"),
                    "candidate_diagnostics": _diag(candidate),
                    "summary": summary or {}}
        candidate_fp = row.get("duplicate_fingerprint") or ""
        # The durable row is authoritative for the candidate key; an in-memory
        # candidate is optional (enrichment may be re-run without re-analysis).
        lineage["candidate_fingerprint"] = candidate_fp or lineage.get("candidate_fingerprint", "")
        evidence = dict(row.get("moment_evidence") or {})
        evidence["lineage"] = {**(evidence.get("lineage") or {}), **lineage}
        patch: dict[str, Any] = {"moment_evidence": evidence}
        if boundary_provenance == "manual_intake":
            patch["boundary_plan"] = plan
        store._request("PATCH", "loom_source_candidates?duplicate_fingerprint=eq."
                       + urllib.parse.quote(candidate_fp), patch)
        # The submission row records who chose the window (durable, queryable).
        try:
            store._request("PATCH", "loom_manual_submissions?duplicate_fingerprint=eq."
                           + urllib.parse.quote(submission.get("duplicate_fingerprint", "")),
                           {"boundary_provenance": boundary_provenance})
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": stage, "error": f"{type(e).__name__}: {e}",
                "candidate_diagnostics": _diag(candidate)}

    return {
        "ok": True,
        "candidate_id": (candidate.platform_content_id if candidate is not None
                         else submission.get("platform_content_id", "")),
        "fingerprint": candidate_fp,
        "boundary_plan": plan,
        "boundary_provenance": boundary_provenance,
        "media_already_acquired": True,
        "summary": summary or {},
        "lineage": lineage,
    }
