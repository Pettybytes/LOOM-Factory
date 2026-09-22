"""Production Supabase store for Content Intelligence assessments + selections.

Mirrors discovery/store_supabase.py: persists derived content assessments and
combined source selections to Supabase/PostgREST. Idempotent per idempotency_key
(replay never duplicates). Never performs DDL, never downloads media, never
triggers Discord/OpenShorts/publishing.

Credentials loaded by name only (LOOM_SUPABASE_URL / LOOM_SUPABASE_SERVICE_ROLE_KEY).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .contract import ContentAssessment
from .selection import SelectionResult


class SupabaseStoreError(RuntimeError):
    pass


def _env(name: str) -> str:
    return os.environ.get(name, "")


def load_supabase_creds() -> tuple[str, str]:
    url = _env("LOOM_SUPABASE_URL").strip().rstrip("/")
    key = _env("LOOM_SUPABASE_SERVICE_ROLE_KEY").strip()
    if not (url and key):
        env_path = os.path.expanduser("~/.openclaw/.env")
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == "LOOM_SUPABASE_URL" and not url:
                    url = v.rstrip("/")
                elif k == "LOOM_SUPABASE_SERVICE_ROLE_KEY" and not key:
                    key = v
    if not (url and key):
        raise SupabaseStoreError("missing LOOM_SUPABASE_URL / LOOM_SUPABASE_SERVICE_ROLE_KEY")
    return url, key


class ContentStoreSupabase:
    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        if url and key:
            self._url = url.rstrip("/")
            self._key = key
        else:
            self._url, self._key = load_supabase_creds()

    def _headers(self, body: bool = False) -> dict[str, str]:
        h = {"apikey": self._key, "Authorization": f"Bearer {self._key}",
             "User-Agent": "LOOM-ContentIntelligence/1.0"}
        if body:
            h["Content-Type"] = "application/json"
            h["Prefer"] = "resolution=merge-duplicates,return=representation"
        return h

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        url = f"{self._url}/rest/v1/{path}"
        headers = self._headers(body is not None)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode() or "[]")
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300] if e.fp else ""
            raise SupabaseStoreError(f"HTTP {e.code} {detail}") from e
        except urllib.error.URLError as e:
            raise SupabaseStoreError(f"network: {e.reason}") from e

    def upsert_assessment(self, a: ContentAssessment) -> str:
        d = a.to_dict()
        payload = {
            "idempotency_key": a.idempotency_key,
            "tenant_id": a.tenant_id, "profile_id": a.profile_id, "platform": a.platform,
            "duplicate_fingerprint": a.duplicate_fingerprint,
            "content_assessment_version": a.content_assessment_version,
            "evidence_level": a.evidence_level, "evidence_types": a.evidence_types,
            "evidence_completeness": a.evidence_completeness, "scores": a.scores,
            "content_worthiness_score": a.content_worthiness_score,
            "content_confidence": a.content_confidence,
            "transcript_availability": a.transcript_availability,
            "transcript_quality": a.transcript_quality,
            "candidate_timestamps": a.candidate_timestamps,
            "recommended_content_format": a.recommended_content_format,
            "recommended_angle": a.recommended_angle, "reasons": a.reasons,
            "flags": a.flags, "risk_flags": a.risk_flags,
            "model_provider": a.model_provider, "model_version": a.model_version,
            "prompt_version": a.prompt_version, "raw_model_reference": a.raw_model_reference,
        }
        self._request("POST", "loom_content_assessments?on_conflict=idempotency_key", payload)
        return "inserted"

    def upsert_selection(self, s: SelectionResult) -> str:
        d = s.to_dict()
        payload = {
            "idempotency_key": s.idempotency_key, "tenant_id": s.tenant_id,
            "profile_id": s.profile_id, "platform": s.platform,
            "duplicate_fingerprint": s.duplicate_fingerprint,
            "scoring_policy_version": s.scoring_policy_version,
            "market_opportunity_score": s.market_opportunity_score,
            "market_confidence": s.market_confidence,
            "content_worthiness_score": s.content_worthiness_score,
            "content_confidence": s.content_confidence,
            "tenant_format_fit_score": s.tenant_format_fit_score,
            "provenance_usability_score": s.provenance_usability_score,
            "source_selection_score": s.source_selection_score,
            "source_selection_confidence": s.source_selection_confidence,
            "recommended_format": s.recommended_format,
            "recommended_angle": s.recommended_angle,
            "suggested_timestamps": s.suggested_timestamps,
            "reason_codes": s.reason_codes, "explanation": s.explanation,
            "missing_evidence": s.missing_evidence, "disqualifying_risks": s.disqualifying_risks,
            "outcome": s.outcome,
        }
        self._request("POST", "loom_source_selections?on_conflict=idempotency_key", payload)
        return "inserted"

    def count_by_key(self, table: str, idempotency_key: str) -> int:
        q = urllib.parse.quote(idempotency_key)
        rows = self._request("GET", f"{table}?idempotency_key=eq.{q}&select=idempotency_key")
        return len(rows) if isinstance(rows, list) else 0

    def upsert_candidate(self, c: Any) -> str:
        """Persist the AUTHORITATIVE joined candidate (post-FIRE + ranking).

        Idempotent per duplicate_fingerprint: replay never duplicates, and the
        latest pipeline state wins. The Source Review queue consumes this table.
        """
        me = dict(getattr(c, "moment_evidence", None) or {})
        bp = dict(getattr(c, "boundary_plan", None) or {})
        comp = dict(getattr(c, "completeness", None) or {})
        mt = dict(getattr(c, "moment_type", None) or {})
        payload = {
            "duplicate_fingerprint": c.duplicate_fingerprint,
            "tenant_id": getattr(c, "tenant_id", "") or "",
            "profile_id": getattr(c, "profile_id", "") or "",
            "platform": getattr(c, "platform", "") or "",
            "platform_content_id": c.platform_content_id or "",
            "title": c.title or "",
            "canonical_url": getattr(c, "canonical_url", "") or "",
            "creator_channel_identity": getattr(c, "creator_channel_identity", "") or "",
            "publication_timestamp": getattr(c, "publication_timestamp", None),
            "market_score": int(getattr(c, "market_score_final", None)
                                or c.market_score or 0),
            "content_score": int(getattr(c, "content_score_recalibrated", None)
                                or c.content_worthiness or 0),
            "selection_score": int(getattr(c, "selection_score", None) or 0),
            "confidence_score": int(getattr(c, "evidence_confidence", None) or 0),
            "selection_outcome": c.selection_outcome or "",
            "fire_passed": bool(c.fire_passed),
            "fire_reasons": list(getattr(c, "fire_reasons", ()) or ()),
            "completeness": comp or None,
            "completeness_verdict": (comp or {}).get("completeness_verdict", ""),
            "boundary_plan": bp or None,
            "moment_type": mt or None,
            "moment_evidence": me or None,
            "evidence_depth": c.evidence_depth or "",
            "source_mode": c.source_mode or "",
            "source_relative_rank": getattr(c, "source_relative_rank", None),
            "source_candidate_count": int(getattr(c, "source_candidate_count", 0) or 0),
            "duplicate_state": c.duplicate_state or "",
            "rank_evidence": dict(getattr(c, "rank_evidence", None) or {}),
            "inspection_attempts": list(getattr(c, "inspection_attempts", ()) or ()),
            "media_assessment_status": c.media_assessment_status or "",
            "media_assessment": dict(getattr(c, "media_assessment", None) or {}),
            "signal_status": c.signal_status or "",
            "status": c.status or "",
            "rejection_reason": c.rejection_reason or "",
        }
        self._request("POST",
                      "loom_source_candidates?on_conflict=duplicate_fingerprint",
                      payload)
        return "inserted"

    def upsert_selection_fire(self, c: Any) -> str:
        """Write the FIRE/moment fields onto the selection row the queue reads.

        The queue's legacy join (selections + discovered_sources) keeps working
        for pre-migration rows; this makes those rows FIRE-filterable too.
        """
        payload = {
            "idempotency_key": getattr(c, "idempotency_key", "") or "",
            "tenant_id": getattr(c, "tenant_id", "") or "",
            "profile_id": getattr(c, "profile_id", "") or "",
            "platform": getattr(c, "platform", "") or "",
            "duplicate_fingerprint": c.duplicate_fingerprint,
            "scoring_policy_version": getattr(c, "scoring_policy_version", "") or "",
            "market_opportunity_score": int(getattr(c, "market_score_final", None)
                                             or c.market_score or 0),
            "market_confidence": 0,
            "content_worthiness_score": int(c.content_worthiness or 0),
            "content_confidence": 0,
            "tenant_format_fit_score": 0,
            "provenance_usability_score": 0,
            "source_selection_score": int(getattr(c, "selection_score", None) or 0),
            "source_selection_confidence": int(getattr(c, "evidence_confidence", None) or 0),
            "recommended_format": "",
            "recommended_angle": "",
            "suggested_timestamps": [],
            "reason_codes": [],
            "explanation": "",
            "missing_evidence": [],
            "disqualifying_risks": [],
            "outcome": c.selection_outcome or "",
            "fire_passed": bool(c.fire_passed),
            "fire_reasons": list(getattr(c, "fire_reasons", ()) or ()),
            "evidence_confidence": getattr(c, "evidence_confidence", None),
            "content_score_recalibrated": getattr(c, "content_score_recalibrated", None),
            "moment_evidence": dict(getattr(c, "moment_evidence", None) or {}),
            "boundary_plan": dict(getattr(c, "boundary_plan", None) or {}),
            "completeness": dict(getattr(c, "completeness", None) or {}),
            "moment_type": dict(getattr(c, "moment_type", None) or {}),
            "source_mode": c.source_mode or "",
            "source_relative_rank": getattr(c, "source_relative_rank", None),
            "source_candidate_count": int(getattr(c, "source_candidate_count", 0) or 0),
            "duplicate_state": c.duplicate_state or "",
            "rank_evidence": dict(getattr(c, "rank_evidence", None) or {}),
            "inspection_attempts": list(getattr(c, "inspection_attempts", ()) or ()),
            "media_assessment_status": c.media_assessment_status or "",
        }
        self._request("POST", "loom_source_selections?on_conflict=idempotency_key",
                      payload)
        return "inserted"

    def selections(self) -> list[dict[str, Any]]:
        rows = self._request("GET", "loom_source_selections?select=*&order=created_at.asc")
        return rows if isinstance(rows, list) else []

    def production_selections(self) -> list[dict[str, Any]]:
        """Completed selections for normal production profiles (proof excluded)."""
        from .store import is_non_production
        return [
            s for s in self.selections()
            if not is_non_production(s.get("profile_id") or "", s.get("tenant_id") or "")
        ]
