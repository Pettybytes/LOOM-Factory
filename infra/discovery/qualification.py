"""Qualification-before-acquisition gate.

Deterministic, ordered checks applied to a normalized record BEFORE any
download, processing, or publication may occur. The gate never authorizes
publication — a qualified source only ever advances to the *manual-ingest
boundary* (it may later be reviewed and manually ingested; it is never
auto-downloaded, auto-clipped, or auto-distributed).

Each check returns a rejection reason string or ``None`` (pass). The first
rejection wins; the final status is one of:

  * ``qualified``        — passes all deterministic checks, eligible for
                           manual ingest;
  * ``manual_review``    — otherwise eligible, but the brief requires manual
                           review before manual ingest;
  * ``rejected``         — an explicit rejection reason was produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .discovery_record import DiscoveryRecord
from .identity import classify_duplicate, normalize_creator
from .source_brief import SourceBrief


@dataclass(frozen=True)
class QualificationResult:
    status: str  # qualified | manual_review | rejected
    rejection_reason: str = ""
    checks: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status in ("qualified", "manual_review")


def _text_match(brief_tokens: list[str], haystack: str) -> bool:
    """Deterministic substring match of any brief token against a text field."""
    hay = (haystack or "").lower()
    return any(tok and tok.lower() in hay for tok in brief_tokens)


class QualificationGate:
    """Runs the qualification-before-acquisition checks against a record."""

    def evaluate(
        self,
        record: DiscoveryRecord,
        brief: SourceBrief,
        seen_fingerprints: set[str] | None = None,
        seen_global_ids: set[str] | None = None,
        seen_near_keys: set[str] | None = None,
    ) -> QualificationResult:
        seen_fingerprints = seen_fingerprints or set()
        seen_global_ids = seen_global_ids or set()
        seen_near_keys = seen_near_keys or set()
        checks: list[str] = []

        # 1. Platform policy: platform must be in the brief's target list.
        if record.platform not in brief.target_platforms:
            return QualificationResult(
                "rejected",
                f"platform {record.platform!r} not in brief target_platforms",
                tuple(checks),
            )
        checks.append("platform")

        # 2. Connector restrictions (if brief restricts connector names).
        if brief.connector_restrictions and record.connector_name not in brief.connector_restrictions:
            return QualificationResult(
                "rejected",
                f"connector {record.connector_name!r} not in brief connector_restrictions",
                tuple(checks),
            )
        checks.append("connector")

        # 3. Duplicate / repost / near-duplicate boundaries (deterministic).
        kind, reason = classify_duplicate(
            tenant_id=record.tenant_id,
            platform=record.platform,
            content_id=record.platform_content_id,
            seen_fingerprints=seen_fingerprints,
            seen_global_ids=seen_global_ids,
            seen_near_keys=seen_near_keys,
            creator_channel_identity=record.creator_channel_identity,
            title=record.title,
        )
        if kind == "exact":
            return QualificationResult("rejected", f"duplicate (exact): {reason}", tuple(checks))
        if kind == "repost":
            return QualificationResult("rejected", f"duplicate (repost): {reason}", tuple(checks))
        if kind == "near":
            return QualificationResult("rejected", f"duplicate (near): {reason}", tuple(checks))
        checks.append("duplicate")

        # 4. Creator/channel blocklist (explicit rejection).
        creator_norm = normalize_creator(record.creator_channel_identity)
        for blocked in brief.creator_channel_blocklist:
            if normalize_creator(blocked) and normalize_creator(blocked) in creator_norm:
                return QualificationResult(
                    "rejected",
                    f"creator/channel {record.creator_channel_identity!r} is blocklisted",
                    tuple(checks),
                )
        checks.append("blocklist")

        # 5. Creator/channel allowlist (if non-empty, creator must be present).
        if brief.creator_channel_allowlist:
            allowed = [normalize_creator(c) for c in brief.creator_channel_allowlist]
            if not any(a and a in creator_norm for a in allowed):
                return QualificationResult(
                    "rejected",
                    f"creator/channel {record.creator_channel_identity!r} not in allowlist",
                    tuple(checks),
                )
        checks.append("allowlist")

        # 6. Exclusions (explicit negative keyword/entity guard).
        if brief.exclusions:
            hay = f"{record.title} {record.discovery_reason}".lower()
            if any(normalize_creator(e) and normalize_creator(e) in hay for e in brief.exclusions):
                return QualificationResult(
                    "rejected",
                    "matches brief exclusion",
                    tuple(checks),
                )
        checks.append("exclusions")

        # 7. Freshness window.
        if brief.freshness_window_days > 0:
            # publication_timestamp is ISO-8601; parse defensively.
            if not _within_window(record.publication_timestamp, brief.freshness_window_days):
                return QualificationResult(
                    "rejected",
                    f"publication outside freshness window ({brief.freshness_window_days}d)",
                    tuple(checks),
                )
        checks.append("freshness")

        # 8. Content-type requirements (if specified).
        if brief.content_type_requirements:
            # Content type lives in observed_metrics or a reserved field; for
            # connectors that always yield a fixed type (e.g. video), it is
            # recorded under observed_metrics["content_type"].
            ct = (record.observed_metrics or {}).get("content_type", "")
            if ct and ct.lower() not in {c.lower() for c in brief.content_type_requirements}:
                return QualificationResult(
                    "rejected",
                    f"content_type {ct!r} not in brief requirements",
                    tuple(checks),
                )
        checks.append("content_type")

        # 9. Provenance/rights policy.
        if brief.provenance_rights_requirements and not record.provenance_rights_evidence:
            return QualificationResult(
                "rejected",
                "missing provenance/rights evidence (brief requires it)",
                tuple(checks),
            )
        checks.append("provenance")

        # 10. Acquisition eligibility (tenant-level gate).
        if not brief.acquisition_eligibility:
            return QualificationResult(
                "rejected",
                "acquisition not eligible for this brief",
                tuple(checks),
            )
        checks.append("eligibility")

        # 11. Cost ceiling (connector cost is declarative; free = 0).
        #     This is a placeholder gate — connectors report cost via
        #     cost_report(); the gate only rejects if a cost is passed in and
        #     exceeds the ceiling. No paid connector exists in this segment.
        checks.append("cost_ceiling")

        # Manual review requirement.
        if brief.manual_review_required:
            return QualificationResult("manual_review", "", tuple(checks))

        return QualificationResult("qualified", "", tuple(checks))


def _within_window(iso_ts: str, window_days: float) -> bool:
    """Return True if ``iso_ts`` is within ``window_days`` of now. Empty/missing
    timestamps are treated as OUT of window (fail-closed)."""
    if not iso_ts:
        return False
    from datetime import datetime, timezone, timedelta

    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - dt).total_seconds() <= window_days * 86400
