"""Production Supabase discovery store.

Wraps the same durable store contract as ``store.DiscoveryStore`` (idempotent
upsert keyed by ``duplicate_fingerprint``; append-only metric history) but
persists to Supabase/PostgREST — LOOM's canonical structured memory — instead
of local SQLite.

Credentials are loaded from the environment by NAME ONLY (``LOOM_SUPABASE_URL``,
``LOOM_SUPABASE_SERVICE_ROLE_KEY``); no secret value is ever hard-coded or
printed. The service-role key is required because the discovery rows are
server-written (no end-user can insert into ``loom_discovered_sources``).

This store NEVER performs DDL and NEVER downloads media, calls OpenShorts,
publishes, or distributes. It only writes/reads discovery rows.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .discovery_record import DiscoveryRecord
from .identity import near_duplicate_key
from .source_brief import SourceBrief


class SupabaseStoreError(RuntimeError):
    """Deterministic store error (no secret values in messages)."""


def _env(name: str) -> str:
    return os.environ.get(name, "")


def _load_env() -> tuple[str, str]:
    """Load Supabase URL + service-role key from the environment by name only."""
    url = _env("LOOM_SUPABASE_URL").strip().rstrip("/")
    key = _env("LOOM_SUPABASE_SERVICE_ROLE_KEY").strip()
    # If not already in os.environ, source the local vault (names only).
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
        raise SupabaseStoreError(
            "missing LOOM_SUPABASE_URL / LOOM_SUPABASE_SERVICE_ROLE_KEY (names only)"
        )
    return url, key


class SupabaseStore:
    """Durable discovery store backed by Supabase/PostgREST."""

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        if url and key:
            self._url = url.rstrip("/")
            self._key = key
        else:
            self._url, self._key = _load_env()

    # -- HTTP helpers ---------------------------------------------------
    def _headers(self, content_type: bool = False) -> dict[str, str]:
        h = {"apikey": self._key, "Authorization": f"Bearer {self._key}"}
        if content_type:
            h["Content-Type"] = "application/json"
        return h

    def _request(self, method: str, path: str, body: dict | None = None,
                 prefer: str | None = None, timeout: int = 30) -> Any:
        url = f"{self._url}/rest/v1/{path}"
        headers = self._headers(content_type=body is not None)
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode() or "[]"
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300] if e.fp else ""
            raise SupabaseStoreError(f"HTTP {e.code} {detail}") from e
        except urllib.error.URLError as e:
            raise SupabaseStoreError(f"network: {e.reason}") from e

    # -- Source Briefs ---------------------------------------------------
    def upsert_brief(self, brief: SourceBrief) -> None:
        payload = brief.to_dict()
        payload["payload"] = json.dumps(brief.to_dict(), sort_keys=True)
        # Extension contracts live in the existing JSON payload, not invented
        # PostgREST columns. Older schema deployments remain compatible.
        payload.pop("mining_requirements", None)
        self._request(
            "POST",
            "loom_source_briefs?on_conflict=tenant_id,profile_id,version",
            body=payload,
            prefer="resolution=merge-duplicates,return=representation",
        )

    # -- Discovered sources ---------------------------------------------
    def upsert_source(self, record: DiscoveryRecord) -> str:
        """Idempotent upsert keyed by duplicate_fingerprint. Re-observation
        appends metric history and refreshes updated_at without a new row.

        Returns ``"inserted"`` or ``"updated"``.
        """
        near_key = near_duplicate_key(
            record.platform, record.creator_channel_identity, record.title
        )
        existing = self._get_row(record.duplicate_fingerprint)

        if existing is not None:
            history = existing.get("metric_history") or []
            obs = {
                "observed_at": record.metric_observation_timestamp,
                "metrics": record.observed_metrics,
            }
            if obs not in history:
                history = list(history) + [obs]
            patch = {
                "observed_metrics": record.observed_metrics,
                "metric_history": history,
                "metric_observation_timestamp": record.metric_observation_timestamp,
                "processing_eligibility": record.processing_eligibility,
                "qualification_status": record.qualification_status,
                "rejection_reason": record.rejection_reason,
                "profile_id": record.profile_id,
                "body_excerpt": record.body_excerpt,
                "content_format": getattr(record, "content_format", "") or "hold_unassigned",
                "updated_at": record.updated_at or record.created_at,
            }
            self._request(
                "PATCH",
                f"loom_discovered_sources?duplicate_fingerprint=eq.{urllib.parse.quote(record.duplicate_fingerprint)}",
                body=patch,
                prefer="return=representation",
            )
            return "updated"

        payload = self._record_payload(record, near_key)
        self._request(
            "POST",
            "loom_discovered_sources?on_conflict=duplicate_fingerprint",
            body=payload,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return "inserted"

    def _record_payload(self, r: DiscoveryRecord, near_key: str) -> dict[str, Any]:
        history = r.metric_history
        if r.metric_observation_timestamp and not any(
            h.get("observed_at") == r.metric_observation_timestamp for h in history
        ):
            history = list(history) + [{
                "observed_at": r.metric_observation_timestamp,
                "metrics": r.observed_metrics,
            }]
        return {
            "tenant_id": r.tenant_id,
            "source_brief_version": r.source_brief_version,
            "profile_id": r.profile_id,
            "platform": r.platform,
            "canonical_url": r.canonical_url,
            "platform_content_id": r.platform_content_id,
            "creator_channel_identity": r.creator_channel_identity,
            "creator_channel_url": r.creator_channel_url,
            "title": r.title,
            "publication_timestamp": r.publication_timestamp or None,
            "observed_metrics": r.observed_metrics,
            "metric_observation_timestamp": r.metric_observation_timestamp or None,
            "metric_history": history,
            "velocity_inputs": r.velocity_inputs,
            "discovery_query": r.discovery_query,
            "discovery_reason": r.discovery_reason,
            "opportunity_classification": r.opportunity_classification,
            "evidence_confidence": r.evidence_confidence,
            "provenance_rights_evidence": r.provenance_rights_evidence,
            "duplicate_fingerprint": r.duplicate_fingerprint,
            "global_fingerprint": r.global_fingerprint,
            "near_duplicate_key": near_key,
            "processing_eligibility": r.processing_eligibility,
            "qualification_status": r.qualification_status,
            "rejection_reason": r.rejection_reason,
            "connector_name": r.connector_name,
            "connector_version": r.connector_version,
            "raw_evidence_reference": r.raw_evidence_reference,
            "body_excerpt": r.body_excerpt,
            "content_format": getattr(r, "content_format", "") or "hold_unassigned",
            "created_at": r.created_at,
            "updated_at": r.updated_at or r.created_at,
        }

    def _get_row(self, fingerprint: str) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            f"loom_discovered_sources?duplicate_fingerprint=eq.{urllib.parse.quote(fingerprint)}&select=*",
        )
        if isinstance(rows, list) and rows:
            return rows[0]
        return None

    def contains_fingerprint(self, fingerprint: str) -> bool:
        return self._get_row(fingerprint) is not None

    def contains_global(self, global_fp: str) -> bool:
        rows = self._request(
            "GET",
            f"loom_discovered_sources?global_fingerprint=eq.{urllib.parse.quote(global_fp)}&select=duplicate_fingerprint&limit=1",
        )
        return bool(isinstance(rows, list) and rows)

    def all_global_fingerprints(self) -> set[str]:
        rows = self._request(
            "GET",
            "loom_discovered_sources?select=global_fingerprint&global_fingerprint=neq.",
        )
        return {r["global_fingerprint"] for r in rows if r.get("global_fingerprint")} if isinstance(rows, list) else set()

    def all_near_keys(self) -> set[str]:
        rows = self._request(
            "GET",
            "loom_discovered_sources?select=near_duplicate_key&near_duplicate_key=neq.",
        )
        return {r["near_duplicate_key"] for r in rows if r.get("near_duplicate_key")} if isinstance(rows, list) else set()

    def all_crosspost_keys(self) -> set[str]:
        """Cross-post keys derived from stored (platform, title) pairs."""
        from .identity import crosspost_key

        rows = self._request(
            "GET", "loom_discovered_sources?select=platform,title"
        )
        if not isinstance(rows, list):
            return set()
        return {crosspost_key(r["platform"], r["title"]) for r in rows if r.get("title")}

    def all_sources(self) -> list[dict[str, Any]]:
        rows = self._request("GET", "loom_discovered_sources?select=*&order=created_at.asc")
        return rows if isinstance(rows, list) else []

    def production_sources(self) -> list[dict[str, Any]]:
        """Rows eligible for a normal production queue — excludes acceptance /
        test / example profiles by default (see source_brief.is_non_production)."""
        from .source_brief import is_non_production

        return [
            s for s in self.all_sources()
            if not is_non_production(s.get("profile_id") or "", s.get("tenant_id") or "")
        ]

    def production_fingerprints(self) -> set[str]:
        """Seen fingerprints scoped to production profiles only."""
        return {s["duplicate_fingerprint"] for s in self.production_sources()}

    def production_global_fingerprints(self) -> set[str]:
        return {
            s["global_fingerprint"] for s in self.production_sources()
            if s.get("global_fingerprint")
        }

    def count(self) -> int:
        rows = self._request(
            "GET",
            "loom_discovered_sources?select=duplicate_fingerprint",
            prefer="count=exact",
        )
        # When Prefer: count=exact is used, PostgREST returns the count in a
        # Content-Range header; urllib loses headers here, so fall back to a
        # full select and count locally (bounded by the small proof dataset).
        all_rows = self._request("GET", "loom_discovered_sources?select=duplicate_fingerprint")
        return len(all_rows) if isinstance(all_rows, list) else 0

    # -- Saturation context ------------------------------------------------
    def saturation_count(self, platform: str, title: str, *,
                         exclude_fingerprint: str = "") -> int:
        """Number of other stored candidates sharing the same near/cross-post
        key (competition/saturation), excluding the given fingerprint."""
        from .identity import crosspost_key

        if not title:
            return 0
        xpost = crosspost_key(platform, title)
        rows = self._request("GET", "loom_discovered_sources?select=duplicate_fingerprint,platform,title")
        if not isinstance(rows, list):
            return 0
        count = 0
        for r in rows:
            if exclude_fingerprint and r.get("duplicate_fingerprint") == exclude_fingerprint:
                continue
            if r.get("title") and crosspost_key(r["platform"], r["title"]) == xpost:
                count += 1
        return count

    # -- Topic Intelligence assessments ------------------------------------
    def upsert_assessment(self, assessment: Any) -> str:
        """Idempotently store an assessment keyed by
        (duplicate_fingerprint, score_version)."""
        data = assessment.to_dict()
        sig = assessment.normalized_signals
        payload = {
            "duplicate_fingerprint": assessment.duplicate_fingerprint,
            "score_version": assessment.score_version,
            "tenant_id": assessment.tenant_id,
            "profile_id": assessment.profile_id,
            "platform": assessment.platform,
            "platform_content_id": assessment.platform_content_id,
            "opportunity_score": assessment.opportunity_score,
            "opportunity_class": assessment.opportunity_class,
            "confidence": assessment.confidence,
            "data_completeness": assessment.data_completeness,
            "content_format": assessment.content_format,
            "reasons": data["reasons"],
            "flags": data["flags"],
            "signal_scores": data["signal_scores"],
            "normalized_signals": {
                "platform": sig.platform,
                "content_type": sig.content_type,
                "volume": sig.volume,
                "volume_metric": sig.volume_metric,
                "likes": sig.likes,
                "comments": sig.comments,
                "engagement_rate": sig.engagement_rate,
                "source_url_references": list(sig.source_url_references),
                "missing_metrics": list(sig.missing_metrics),
            },
            "velocity": assessment.velocity.to_dict(),
            "assessed_at": assessment.assessed_at,
        }
        existing = self._get_assessment(
            assessment.duplicate_fingerprint, assessment.score_version
        )
        self._request(
            "POST",
            "loom_topic_assessments?on_conflict=duplicate_fingerprint,score_version",
            body=payload,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return "updated" if existing is not None else "inserted"

    def _get_assessment(self, fingerprint: str, score_version: str) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "loom_topic_assessments?duplicate_fingerprint=eq.{0}&score_version=eq.{1}&select=duplicate_fingerprint".format(
                urllib.parse.quote(fingerprint), urllib.parse.quote(score_version)
            ),
        )
        if isinstance(rows, list) and rows:
            return rows[0]
        return None

    def assessment_count(self) -> int:
        rows = self._request("GET", "loom_topic_assessments?select=duplicate_fingerprint")
        return len(rows) if isinstance(rows, list) else 0

    def assessments(self) -> list[dict[str, Any]]:
        rows = self._request("GET", "loom_topic_assessments?select=*&order=created_at.asc")
        return rows if isinstance(rows, list) else []

    def production_assessments(self) -> list[dict[str, Any]]:
        """Assessments for normal production candidates (proof profiles excluded)."""
        from .source_brief import is_non_production

        return [
            a for a in self.assessments()
            if not is_non_production(a.get("profile_id") or "", a.get("tenant_id") or "")
        ]

    def delete_all(self) -> int:
        """Delete all discovered sources (used only by the proof harness to
        start from a clean slate). NOT part of the durable contract."""
        rows = self._request("GET", "loom_discovered_sources?select=duplicate_fingerprint")
        n = 0
        if isinstance(rows, list):
            for r in rows:
                self._request(
                    "DELETE",
                    f"loom_discovered_sources?duplicate_fingerprint=eq.{urllib.parse.quote(r['duplicate_fingerprint'])}",
                )
                n += 1
        return n
