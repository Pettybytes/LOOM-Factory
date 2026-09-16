"""Normalized discovery record — the durable, platform-agnostic shape for every
discovered source.

A record is produced by a connector, deduplicated/identified, then evaluated by
the qualification gate. The record itself carries NO scoring and NO
opportunity ranking — it only reserves the fields a later Topic Intelligence
segment may fill in. Discovery never authorizes publication.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

SCHEMA_VERSION = "1.0.0"

# Fields every normalized record must carry.
REQUIRED_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "source_brief_version",
    "platform",
    "canonical_url",
    "platform_content_id",
    "creator_channel_identity",
    "creator_channel_url",
    "title",
    "publication_timestamp",
    "observed_metrics",
    "metric_observation_timestamp",
    "metric_history",
    "discovery_query",
    "discovery_reason",
    "evidence_confidence",
    "provenance_rights_evidence",
    "duplicate_fingerprint",
    "global_fingerprint",
    "processing_eligibility",
    "qualification_status",
    "connector_name",
    "connector_version",
)

# Reserved for Topic Intelligence (not computed in this segment).
RESERVED_FIELDS: tuple[str, ...] = (
    "velocity_inputs",
    "opportunity_classification",
)


class DiscoveryRecordError(ValueError):
    """Raised when a normalized record fails structural validation."""


def _iso(value: Any) -> str:
    """Normalize a timestamp to ISO-8601 UTC string ('' if None)."""
    if value is None or value == "":
        return ""
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.astimezone(_dt.timezone.utc).isoformat()
    return str(value)


class DiscoveryRecord:
    """A validated normalized discovery record."""

    def __init__(self, **data: Any) -> None:
        self.schema_version: str = data.get("schema_version") or SCHEMA_VERSION

        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise DiscoveryRecordError(f"missing required fields: {', '.join(missing)}")

        self.tenant_id: str = str(data["tenant_id"]).strip()
        self.source_brief_version: str = str(data["source_brief_version"]).strip()
        # profile_id identifies which Source Brief profile produced this record;
        # it is REQUIRED for production queries to exclude acceptance/test
        # profiles by default. Default '' = non-production/unknown.
        self.profile_id: str = str(data.get("profile_id") or "").strip()
        self.platform: str = str(data["platform"]).strip().lower()
        self.canonical_url: str = str(data["canonical_url"]).strip()
        self.platform_content_id: str = str(data["platform_content_id"]).strip()
        self.creator_channel_identity: str = str(data["creator_channel_identity"]).strip()
        self.creator_channel_url: str = str(data["creator_channel_url"]).strip()
        self.title: str = str(data["title"]).strip()

        self.publication_timestamp: str = _iso(data["publication_timestamp"])
        self.metric_observation_timestamp: str = _iso(data["metric_observation_timestamp"])

        # observed_metrics: single observation; metric_history: list of observations.
        if not isinstance(data["observed_metrics"], dict):
            raise DiscoveryRecordError("observed_metrics must be a dict")
        self.observed_metrics: dict[str, Any] = dict(data["observed_metrics"])

        mh = data["metric_history"]
        if mh is None:
            mh = []
        if not isinstance(mh, list):
            raise DiscoveryRecordError("metric_history must be a list")
        self.metric_history: list[dict[str, Any]] = [dict(x) for x in mh]

        self.discovery_query: str = str(data["discovery_query"] or "").strip()
        self.discovery_reason: str = str(data["discovery_reason"] or "").strip()

        self.provenance_rights_evidence: str = str(
            data["provenance_rights_evidence"] or ""
        ).strip()

        # evidence_confidence: deterministic 0..1 (reserved; no model involved).
        ec = data["evidence_confidence"]
        if isinstance(ec, str) and ec == "":
            ec = None
        self.evidence_confidence: float | None = None if ec is None else float(ec)

        self.duplicate_fingerprint: str = str(data["duplicate_fingerprint"]).strip()
        self.global_fingerprint: str = str(data["global_fingerprint"]).strip()

        self.processing_eligibility: str = str(data["processing_eligibility"] or "").strip()
        self.qualification_status: str = str(data["qualification_status"] or "pending").strip()
        self.rejection_reason: str = str(data.get("rejection_reason") or "").strip()

        self.connector_name: str = str(data["connector_name"]).strip()
        self.connector_version: str = str(data["connector_version"]).strip()

        # Reserved fields — carried through verbatim if present, never computed.
        self.velocity_inputs: dict[str, Any] = dict(data.get("velocity_inputs") or {})
        self.opportunity_classification: str = str(
            data.get("opportunity_classification") or ""
        ).strip()

        self.raw_evidence_reference: str = str(data.get("raw_evidence_reference") or "").strip()
        # body_excerpt: bounded plain-text excerpt of the source body (optional;
        # populated by connectors that expose a text body, e.g. Reddit RSS).
        self.body_excerpt: str = str(data.get("body_excerpt") or "").strip()
        self.created_at: str = _iso(data.get("created_at") or _dt.datetime.now(_dt.timezone.utc))
        self.updated_at: str = _iso(data.get("updated_at") or _dt.datetime.now(_dt.timezone.utc))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveryRecord":
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "source_brief_version": self.source_brief_version,
            "profile_id": self.profile_id,
            "platform": self.platform,
            "canonical_url": self.canonical_url,
            "platform_content_id": self.platform_content_id,
            "creator_channel_identity": self.creator_channel_identity,
            "creator_channel_url": self.creator_channel_url,
            "title": self.title,
            "publication_timestamp": self.publication_timestamp,
            "observed_metrics": dict(self.observed_metrics),
            "metric_observation_timestamp": self.metric_observation_timestamp,
            "metric_history": [dict(x) for x in self.metric_history],
            "discovery_query": self.discovery_query,
            "discovery_reason": self.discovery_reason,
            "evidence_confidence": self.evidence_confidence,
            "provenance_rights_evidence": self.provenance_rights_evidence,
            "duplicate_fingerprint": self.duplicate_fingerprint,
            "global_fingerprint": self.global_fingerprint,
            "processing_eligibility": self.processing_eligibility,
            "qualification_status": self.qualification_status,
            "rejection_reason": self.rejection_reason,
            "connector_name": self.connector_name,
            "connector_version": self.connector_version,
            "velocity_inputs": dict(self.velocity_inputs),
            "opportunity_classification": self.opportunity_classification,
            "raw_evidence_reference": self.raw_evidence_reference,
            "body_excerpt": self.body_excerpt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
