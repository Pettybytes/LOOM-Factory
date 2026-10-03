"""Manual Intelligence Intake V1.2 — Content Intelligence on SUPPLIED media.

This is the seam V1 lacked: a durable manual submission (media already stored in
GHL) becomes real intelligence and a review candidate, by invoking the EXISTING
Content Intelligence runtime — no parallel analyzer, no new scoring model.

Two behaviours that matter:
  * No timestamps supplied -> the entire supplied asset is the operator's window.
    The pipeline's selected moment remains advisory evidence only.
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
    so there is no discovery shortlist to theme-filter. Content-quality and safety
    checks that ARE authorized remain unchanged (nudity/explicit media, directly
    observed event). Age is not a gate anywhere: LOOM analyzes and informs, the
    human decides.
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
            "review_policy": {"mode": "human_curated", "media_first": True,
                              "minimum_content": 50},
            "market_policy": "observed_demand_v1",
            "require_direct_event": True,
            "require_timed_payoff": True,
        },
    })


def _human_title(submission: dict[str, Any], url: str, platform: str) -> str:
    """The HUMAN-READABLE title for a supplied submission — never the source URL.

    The reviewer note is the operator's own words and wins when it says
    something. When it is absent (or is itself just the pasted pointer) the title
    is resolved by the SAME shared contract every other platform uses, which
    falls back to ``creator · content label``.

    This used to be ``(reviewer_note or url)``: a missing note published the
    SOURCE POINTER into the human title slot. ``canonical_url`` already carries
    that pointer, so the fallback added no information and turned provenance
    into a title — which downstream became visible on-screen text in the CapCut
    handoff. Provenance and human copy are different kinds of thing; only the
    human value may fill a human slot.
    """
    import human_title
    note = submission.get("reviewer_note") or ""
    if human_title.is_provenance_reference(note):
        note = ""
    return human_title.resolve_display_title(
        platform=platform,
        content_id=submission.get("platform_content_id") or "",
        source_title=note,
        creator_handle=submission.get("submitter") or "",
        kind="clip",
    )[:200]


def build_record(submission: dict[str, Any], probe: dict[str, Any],
                 *, brief: Any, media_url: str,
                 source_metadata: dict[str, Any] | None = None) -> Any:
    """A DiscoveryRecord for supplied media, carrying the durable media pointer.

    ``source_metadata`` is the engagement evidence captured at ingress through the
    existing free extractor path. Only genuinely measured values are merged; a
    platform that does not expose a field leaves it ABSENT, which the market layer
    reads as UNKNOWN. Nothing is fabricated and no default zero is invented.
    """
    from discovery.discovery_record import DiscoveryRecord
    import hashlib

    url = submission["source_url"]
    platform = submission.get("platform") or ""
    fingerprint = submission["duplicate_fingerprint"]
    captured = source_metadata or {}
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
    for key, value in (captured.get("metrics") or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            continue
        metrics[key] = int(value)
    return DiscoveryRecord(
        tenant_id=submission["tenant_id"], profile_id=brief.profile_id,
        source_brief_version=brief.version, platform=platform,
        canonical_url=url, platform_content_id=submission.get("platform_content_id") or "",
        creator_channel_identity=submission.get("submitter") or "manual",
        creator_channel_url="", title=_human_title(submission, url, platform),
        publication_timestamp=(captured.get("publication_timestamp") or None),
        metric_observation_timestamp=None,
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


def _submission_source_metadata(submission: dict[str, Any]) -> dict[str, Any]:
    """Engagement evidence captured at ingress on the durable submission row."""
    reference = submission.get("media_reference") or {}
    captured = reference.get("source_metadata") if isinstance(reference, dict) else None
    return captured if isinstance(captured, dict) else {}


def _working_origin_s(submission: dict[str, Any]) -> float:
    """The ORIGINAL-source time that corresponds to working t=0 (0.0 for a full source)."""
    ref = (submission or {}).get("media_reference") or {}
    acq = ref.get("acquisition") if isinstance(ref, dict) else None
    if isinstance(acq, dict):
        value = acq.get("working_timebase_origin_s")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def _operator_window_in_working_coords(*, start_s, end_s, submission
                                       ) -> tuple[float, float, float]:
    """Convert an operator's ORIGINAL window into WORKING-asset coordinates.

    The durable working asset has its OWN 0-based timeline: for a bounded
    acquisition, ORIGINAL 120->180 with origin 120 IS WORKING 0->60. Writing the
    ORIGINAL pair into `boundary_plan.recommended_clip_*` (which every downstream
    consumer reads as relative to the working asset) was the P0 coordinate
    collapse — a 120->180 range on a 60-second asset.

    Returns (working_start_s, working_end_s, origin_s). The ORIGINAL values remain
    the caller's to preserve as provenance; only this pair is safe to write into a
    working-relative field. A full-source acquisition has origin 0, so its
    behaviour is unchanged.
    """
    origin = _working_origin_s(submission)
    w_start = max(0.0, float(start_s) - origin) if start_s is not None else 0.0
    w_end = float(end_s) - origin if end_s is not None else 0.0
    return round(w_start, 3), round(w_end, 3), origin


def _assert_working_range(*, plan: dict[str, Any], probe: dict[str, Any]) -> None:
    """C: defence in depth. Reject a plan that does not fit the working asset.

    Runs whether the range came from the operator or the machine, so bad upstream
    coordinates fail HERE rather than being hidden later by editor-side clamping.
    Skipped only when the working duration is genuinely unknown (nothing to assert
    against) — never silently when it is known.
    """
    duration = (probe or {}).get("duration_seconds")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
        return
    start = plan.get("recommended_clip_start")
    end = plan.get("recommended_clip_end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return
    # Lazy import: this module is loaded under several sys.path layouts (the
    # adapter runtime and the test runners), and the CI seam must not depend on
    # load-order to reach the shared window contract.
    import manual_media_intake as _mmi
    swc = _mmi._acq("source_window")
    swc.validate_working_range(start_s=float(start), end_s=float(end),
                               working_duration_s=float(duration))


def run_manual_intelligence(*, submission: dict[str, Any], media_path: str,
                            probe: dict[str, Any], media_url: str,
                            brief: Any | None = None, provider: Any = None,
                            store: Any = None, human_window: tuple[float | None, float | None] = (None, None),
                            cost_ceiling_usd: float = 0.05,
                            source_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
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

    source_metadata = source_metadata or _submission_source_metadata(submission)
    record = build_record(submission, probe, brief=brief, media_url=media_url,
                          source_metadata=source_metadata)
    # The captured source metadata carries the REAL platform publication timestamp
    # whenever the extractor proved one. Only when it is genuinely absent does the
    # timestamp stay NULL — and an empty string is not a valid timestamptz, so NULL
    # remains the honest representation. The metric observation timestamp is
    # genuinely "now" (we probed the media now).
    if not record.publication_timestamp:
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
    plan = dict(getattr(cand, "boundary_plan", {}) or {})
    return enrich_manual_candidate(
        submission=submission, probe=probe, media_url=media_url, human_window=human_window,
        boundary_plan=plan, candidate=cand,
        store=store, source_metadata=source_metadata,
        summary=(result.summary() if hasattr(result, "summary") else {}))


def enrich_manual_candidate(*, submission: dict[str, Any], probe: dict[str, Any],
                            media_url: str,
                            human_window: tuple[float | None, float | None] = (None, None),
                            boundary_plan: dict[str, Any] | None = None,
                            boundary_provenance: str | None = None,
                            candidate: Any = None, store: Any = None,
                            source_metadata: dict[str, Any] | None = None,
                            summary: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply boundary provenance + lineage to the DURABLE candidate row.

    Split out of the analysis run so it is idempotent and retryable WITHOUT
    paying for Content Intelligence again. The operator owns the boundary even
    without explicit timestamps: supplying a whole clip selects that whole clip.
    """
    if store is None:
        from content_intelligence.store_supabase import ContentStoreSupabase
        store = ContentStoreSupabase()
    plan = dict(boundary_plan or {})
    start_s, end_s = human_window
    if start_s is None and end_s is None:
        start_s, end_s = submission.get("time_start_s"), submission.get("time_end_s")
    origin = _working_origin_s(submission)
    whole = start_s is None and end_s is None
    if whole:
        duration = (probe or {}).get("duration_seconds")
        import math
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration <= 0):
            raise ValueError("manual whole-clip boundary requires measured media duration")
        start_s, end_s = origin, origin + float(duration)
    elif start_s is None or end_s is None:
        raise ValueError("manual operator window requires both start and end")
    w_start, w_end, origin = _operator_window_in_working_coords(
        start_s=start_s, end_s=end_s, submission=submission)
    # Retrying enrichment must not nest the human plan inside machine evidence.
    advisory = plan.get("machine_recommendation")
    machine_plan = dict(advisory if isinstance(advisory, dict) else plan)
    plan.update({
        "recommended_clip_start": w_start,
        "recommended_clip_end": w_end,
        "duration": round(w_end - w_start, 3),
        "provenance": ["manual_intake_window", "operator_supplied"],
        "boundary_selection": "entire_supplied_clip" if whole else "operator_timestamps",
        "machine_recommendation": machine_plan,
        "original_window": {"start_s": float(start_s), "end_s": float(end_s)},
        "working_timebase_origin_s": origin,
    })
    boundary_provenance = "manual_intake"
    _assert_working_range(plan=plan, probe=probe)

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
    captured = source_metadata or _submission_source_metadata(submission)
    if captured:
        # Durable lineage: the raw source evidence travels WITH the candidate and
        # Bryan's later decision is keyed to the same row, so the future sourcing
        # engine can relate source characteristics to what he actually approved —
        # including the honest list of what the platform did not expose.
        lineage["source_metadata"] = captured
    stage = "persist_enrichment"
    try:
        # The pipeline assigns its own candidate fingerprint (per materialized
        # moment), so resolve the durable row by the authoritative source id the
        # submission carries instead of assuming a shared key scheme.
        content_id = submission.get("platform_content_id") or ""
        tenant_id = submission.get("tenant_id", "")
        # NEWEST first: with more than one row for one submission the previous
        # unordered lookup picked an arbitrary "first" row, so a re-analysis could
        # bind to a stale duplicate. Deterministic instead.
        rows = store._request(
            "GET", "loom_source_candidates?platform_content_id=eq."
            + urllib.parse.quote(content_id) + "&tenant_id=eq."
            + urllib.parse.quote(tenant_id) + "&order=created_at.desc&select=*") or []
        row = rows[0] if rows else None
        if not row:
            return {"ok": False, "stage": stage,
                    "error": ("Content Intelligence produced a candidate but the durable "
                               "row is absent — the candidate was filtered before persistence"),
                    "candidate_diagnostics": _diag(candidate),
                    "summary": summary or {}}
        candidate_fp = row.get("duplicate_fingerprint") or ""
        # ONE manual submission -> ONE ACTIVE review object (and therefore one
        # Discord card). A re-analysis may legitimately re-evaluate the submitted
        # asset, but it must not leave several ACTIVE review objects behind just
        # because the model returned slightly different boundaries. Older rows are
        # SUPERSEDED — evidence retained, never deleted. Manual path only: the
        # automated-discovery lane is untouched.
        superseded: list[str] = []
        for older in rows[1:]:
            fp_old = older.get("duplicate_fingerprint") or ""
            if not fp_old or fp_old == candidate_fp:
                continue
            try:
                store._request(
                    "PATCH",
                    "loom_source_candidates?duplicate_fingerprint=eq."
                    + urllib.parse.quote(fp_old),
                    {"duplicate_state": "SUPERSEDED_MANUAL_REINGEST"})
                superseded.append(fp_old)
            except Exception:  # noqa: BLE001 — supersession must not fail the intake
                pass
        # The durable row is authoritative for the candidate key; an in-memory
        # candidate is optional (enrichment may be re-run without re-analysis).
        lineage["candidate_fingerprint"] = candidate_fp or lineage.get("candidate_fingerprint", "")
        if superseded:
            lineage["superseded_fingerprints"] = superseded
        evidence = dict(row.get("moment_evidence") or {})
        prev_lineage = evidence.get("lineage") or {}
        # Reuse the EXISTING Discord card for this submission when one exists, so a
        # re-analysis updates that card instead of posting a second one.
        card_message_id = str(prev_lineage.get("card_message_id") or "")
        evidence["lineage"] = {**prev_lineage, **lineage}
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
        "card_message_id": card_message_id,
        "superseded_fingerprints": superseded,
        "boundary_plan": plan,
        "boundary_provenance": boundary_provenance,
        "media_already_acquired": True,
        "summary": summary or {},
        "lineage": lineage,
    }
