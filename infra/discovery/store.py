"""Durable discovery store (deterministic, dependency-free).

V1 uses a local SQLite store so the foundation is fully testable and runnable
with zero external services. The canonical production schema is defined in
``infra/supabase/`` migrations (018/019); those SQL files are the durable
schema contract, and applying them to Supabase is a separate manual step
(Supabase DDL is not reachable from this execution context without the
database password).

The store guarantees:
  * idempotent upsert keyed by ``duplicate_fingerprint``;
  * metric-history preservation (a re-observation appends, never overwrites);
  * tenant-scoped + global fingerprint indexes;
  * explicit duplicate rejection reasons are computed by the caller, not here.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .discovery_record import DiscoveryRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_briefs (
    brief_id        TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    profile_id      TEXT NOT NULL,
    version         TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    payload         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (tenant_id, profile_id, version)
);

CREATE TABLE IF NOT EXISTS discovered_sources (
    fingerprint             TEXT PRIMARY KEY,
    global_fingerprint      TEXT,
    near_duplicate_key      TEXT,
    tenant_id               TEXT NOT NULL,
    source_brief_version    TEXT,
    platform                TEXT NOT NULL,
    canonical_url           TEXT NOT NULL,
    platform_content_id     TEXT NOT NULL,
    creator_channel_identity TEXT,
    creator_channel_url     TEXT,
    title                   TEXT,
    publication_timestamp   TEXT,
    observed_metrics        TEXT,
    metric_observation_timestamp TEXT,
    metric_history          TEXT,
    discovery_query         TEXT,
    discovery_reason        TEXT,
    evidence_confidence     REAL,
    provenance_rights_evidence TEXT,
    processing_eligibility  TEXT,
    qualification_status    TEXT,
    rejection_reason        TEXT,
    connector_name          TEXT,
    connector_version       TEXT,
    velocity_inputs         TEXT,
    opportunity_classification TEXT,
    raw_evidence_reference  TEXT,
    created_at              TEXT,
    updated_at              TEXT
);

CREATE INDEX IF NOT EXISTS idx_ds_global_fp ON discovered_sources (global_fingerprint);
CREATE INDEX IF NOT EXISTS idx_ds_near_key ON discovered_sources (near_duplicate_key);
CREATE INDEX IF NOT EXISTS idx_ds_tenant ON discovered_sources (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ds_platform ON discovered_sources (platform);
"""


class DiscoveryStore:
    """SQLite-backed store for Source Briefs + discovered sources."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- Source Briefs -----------------------------------------------------
    def upsert_brief(self, brief: Any) -> None:
        """Idempotently store a Source Brief (payload as JSON)."""
        from .source_brief import SourceBrief

        assert isinstance(brief, SourceBrief)
        brief_id = f"{brief.tenant_id}:{brief.profile_id}:{brief.version}"
        self._conn.execute(
            """
            INSERT INTO source_briefs
                (brief_id, tenant_id, profile_id, version, schema_version, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(brief_id) DO UPDATE SET payload = excluded.payload
            """,
            (
                brief_id, brief.tenant_id, brief.profile_id, brief.version,
                brief.schema_version, json.dumps(brief.to_dict(), sort_keys=True),
            ),
        )
        self._conn.commit()

    def list_briefs(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM source_briefs"
        args: tuple[Any, ...] = ()
        if tenant_id:
            q += " WHERE tenant_id = ?"
            args = (tenant_id,)
        return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    # -- Discovered sources ------------------------------------------------
    def upsert_source(self, record: DiscoveryRecord) -> str:
        """Idempotently insert a normalized record. Re-observation appends
        metric history and refreshes ``updated_at`` without duplicating the row.

        Returns ``"inserted"`` or ``"updated"``.
        """
        from .identity import near_duplicate_key

        near_key = near_duplicate_key(
            record.platform, record.creator_channel_identity, record.title
        )
        now = record.updated_at or record.created_at

        existing = self._conn.execute(
            "SELECT metric_history, observed_metrics, metric_observation_timestamp "
            "FROM discovered_sources WHERE fingerprint = ?",
            (record.duplicate_fingerprint,),
        ).fetchone()

        if existing:
            # Preserve metric history: append the new observation if it is not
            # already present (idempotent by observation timestamp + metrics).
            history = json.loads(existing["metric_history"] or "[]")
            obs = {
                "observed_at": record.metric_observation_timestamp,
                "metrics": record.observed_metrics,
            }
            if obs not in history:
                history.append(obs)
            self._conn.execute(
                """
                UPDATE discovered_sources
                SET observed_metrics = ?, metric_history = ?,
                    metric_observation_timestamp = ?, updated_at = ?,
                    processing_eligibility = ?, qualification_status = ?,
                    rejection_reason = ?
                WHERE fingerprint = ?
                """,
                (
                    json.dumps(record.observed_metrics, sort_keys=True),
                    json.dumps(history, sort_keys=True),
                    record.metric_observation_timestamp,
                    now,
                    record.processing_eligibility,
                    record.qualification_status,
                    record.rejection_reason,
                    record.duplicate_fingerprint,
                ),
            )
            self._conn.commit()
            return "updated"

        self._conn.execute(
            """
            INSERT INTO discovered_sources (
                fingerprint, global_fingerprint, near_duplicate_key, tenant_id,
                source_brief_version, platform, canonical_url, platform_content_id,
                creator_channel_identity, creator_channel_url, title,
                publication_timestamp, observed_metrics,
                metric_observation_timestamp, metric_history, discovery_query,
                discovery_reason, evidence_confidence, provenance_rights_evidence,
                processing_eligibility, qualification_status, rejection_reason,
                connector_name, connector_version, velocity_inputs,
                opportunity_classification, raw_evidence_reference, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                record.duplicate_fingerprint,
                record.global_fingerprint,
                near_key,
                record.tenant_id,
                record.source_brief_version,
                record.platform,
                record.canonical_url,
                record.platform_content_id,
                record.creator_channel_identity,
                record.creator_channel_url,
                record.title,
                record.publication_timestamp,
                json.dumps(record.observed_metrics, sort_keys=True),
                record.metric_observation_timestamp,
                json.dumps(
                    [{"observed_at": record.metric_observation_timestamp,
                      "metrics": record.observed_metrics}]
                    if record.metric_observation_timestamp
                    else record.metric_history,
                    sort_keys=True,
                ),
                record.discovery_query,
                record.discovery_reason,
                record.evidence_confidence,
                record.provenance_rights_evidence,
                record.processing_eligibility,
                record.qualification_status,
                record.rejection_reason,
                record.connector_name,
                record.connector_version,
                json.dumps(record.velocity_inputs, sort_keys=True),
                record.opportunity_classification,
                record.raw_evidence_reference,
                record.created_at,
                now,
            ),
        )
        self._conn.commit()
        return "inserted"

    def contains_fingerprint(self, fingerprint: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM discovered_sources WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            is not None
        )

    def contains_global(self, global_fp: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM discovered_sources WHERE global_fingerprint = ?", (global_fp,)
            ).fetchone()
            is not None
        )

    def all_global_fingerprints(self) -> set[str]:
        return {r[0] for r in self._conn.execute(
            "SELECT global_fingerprint FROM discovered_sources WHERE global_fingerprint != ''"
        ).fetchall()}

    def all_near_keys(self) -> set[str]:
        return {r[0] for r in self._conn.execute(
            "SELECT near_duplicate_key FROM discovered_sources WHERE near_duplicate_key != ''"
        ).fetchall()}

    def all_sources(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM discovered_sources ORDER BY created_at"
        ).fetchall()]

    def count(self) -> int:
        return self._conn.execute("SELECT count(*) FROM discovered_sources").fetchone()[0]
