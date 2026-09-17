"""Durable store for Content Intelligence assessments + combined selections.

Deterministic, dependency-free SQLite store (mirrors discovery/store.py). The
canonical production schema lives in ``infra/supabase/`` migrations 025/026.

Guarantees:
  * idempotent upsert keyed by ``idempotency_key`` (replay never duplicates);
  * a new evidence version (different idempotency key) creates a NEW row —
    history is never corrupted;
  * non-production profiles excluded from production queues.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .contract import ContentAssessment
from .selection import SelectionResult

# Mirrors discovery/source_brief.py NON_PRODUCTION_MARKERS (kept in sync).
_NON_PRODUCTION_MARKERS: tuple[str, ...] = (
    "acceptance", "test", "proof", "example", "fixture",
    "demo", "sample", "sandbox", "staging", "dev",
)


def is_non_production(profile_id: str = "", tenant_id: str = "") -> bool:
    hay = f"{(profile_id or '').lower()} {(tenant_id or '').lower()}"
    return any(m in hay for m in _NON_PRODUCTION_MARKERS)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS content_assessments (
    idempotency_key         TEXT PRIMARY KEY,
    duplicate_fingerprint   TEXT NOT NULL,
    tenant_id               TEXT NOT NULL,
    profile_id              TEXT NOT NULL DEFAULT '',
    platform                TEXT NOT NULL,
    content_assessment_version TEXT NOT NULL,
    evidence_level          INTEGER NOT NULL,
    evidence_types          TEXT NOT NULL,
    evidence_completeness   REAL NOT NULL,
    scores                  TEXT NOT NULL,
    content_worthiness_score INTEGER NOT NULL,
    content_confidence      REAL NOT NULL,
    transcript_availability TEXT NOT NULL,
    transcript_quality      TEXT NOT NULL,
    candidate_timestamps    TEXT NOT NULL,
    recommended_content_format TEXT NOT NULL,
    recommended_angle       TEXT NOT NULL,
    reasons                 TEXT NOT NULL,
    flags                   TEXT NOT NULL,
    risk_flags              TEXT NOT NULL,
    model_provider          TEXT NOT NULL,
    model_version           TEXT NOT NULL,
    prompt_version          TEXT NOT NULL,
    raw_model_reference     TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ca_fp ON content_assessments (duplicate_fingerprint);
CREATE INDEX IF NOT EXISTS idx_ca_tenant ON content_assessments (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ca_profile ON content_assessments (profile_id);

CREATE TABLE IF NOT EXISTS source_selections (
    idempotency_key          TEXT PRIMARY KEY,
    duplicate_fingerprint    TEXT NOT NULL,
    tenant_id                TEXT NOT NULL,
    profile_id               TEXT NOT NULL DEFAULT '',
    platform                 TEXT NOT NULL,
    scoring_policy_version   TEXT NOT NULL,
    market_opportunity_score INTEGER NOT NULL,
    market_confidence        REAL NOT NULL,
    content_worthiness_score INTEGER NOT NULL,
    content_confidence       REAL NOT NULL,
    tenant_format_fit_score  INTEGER NOT NULL,
    provenance_usability_score INTEGER NOT NULL,
    source_selection_score   INTEGER NOT NULL,
    source_selection_confidence REAL NOT NULL,
    recommended_format       TEXT NOT NULL,
    recommended_angle        TEXT NOT NULL,
    suggested_timestamps     TEXT NOT NULL,
    reason_codes             TEXT NOT NULL,
    explanation              TEXT NOT NULL,
    missing_evidence         TEXT NOT NULL,
    disqualifying_risks      TEXT NOT NULL,
    outcome                  TEXT NOT NULL,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ss_fp ON source_selections (duplicate_fingerprint);
CREATE INDEX IF NOT EXISTS idx_ss_tenant ON source_selections (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ss_profile ON source_selections (profile_id);
CREATE INDEX IF NOT EXISTS idx_ss_outcome ON source_selections (outcome);
"""


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class ContentStore:
    """SQLite store for content assessments + source selections."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- Content assessments ---------------------------------------------
    def upsert_assessment(self, a: ContentAssessment) -> str:
        exists = self._conn.execute(
            "SELECT 1 FROM content_assessments WHERE idempotency_key = ?",
            (a.idempotency_key,),
        ).fetchone() is not None

        d = a.to_dict()
        self._conn.execute(
            """
            INSERT INTO content_assessments (
                idempotency_key, duplicate_fingerprint, tenant_id, profile_id,
                platform, content_assessment_version, evidence_level,
                evidence_types, evidence_completeness, scores,
                content_worthiness_score, content_confidence,
                transcript_availability, transcript_quality, candidate_timestamps,
                recommended_content_format, recommended_angle, reasons, flags,
                risk_flags, model_provider, model_version, prompt_version,
                raw_model_reference, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
                scores = excluded.scores,
                content_worthiness_score = excluded.content_worthiness_score,
                content_confidence = excluded.content_confidence,
                updated_at = excluded.updated_at
            """,
            (
                a.idempotency_key, a.duplicate_fingerprint, a.tenant_id, a.profile_id,
                a.platform, a.content_assessment_version, a.evidence_level,
                json.dumps(a.evidence_types, sort_keys=True),
                a.evidence_completeness,
                json.dumps({k: v for k, v in a.scores.items()}, sort_keys=True),
                a.content_worthiness_score, a.content_confidence,
                a.transcript_availability, a.transcript_quality,
                json.dumps(a.candidate_timestamps, sort_keys=True),
                a.recommended_content_format, a.recommended_angle,
                json.dumps(a.reasons, sort_keys=True),
                json.dumps(a.flags, sort_keys=True),
                json.dumps(a.risk_flags, sort_keys=True),
                a.model_provider, a.model_version, a.prompt_version,
                a.raw_model_reference,
                _now(), _now(),
            ),
        )
        self._conn.commit()
        return "updated" if exists else "inserted"

    def assessment_count(self) -> int:
        return self._conn.execute("SELECT count(*) FROM content_assessments").fetchone()[0]

    def assessments(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM content_assessments ORDER BY created_at"
        ).fetchall()]

    def production_assessments(self) -> list[dict[str, Any]]:
        return [
            a for a in self.assessments()
            if not is_non_production(a.get("profile_id") or "", a.get("tenant_id") or "")
        ]

    # -- Source selections ------------------------------------------------
    def upsert_selection(self, s: SelectionResult) -> str:
        exists = self._conn.execute(
            "SELECT 1 FROM source_selections WHERE idempotency_key = ?",
            (s.idempotency_key,),
        ).fetchone() is not None

        d = s.to_dict()
        self._conn.execute(
            """
            INSERT INTO source_selections (
                idempotency_key, duplicate_fingerprint, tenant_id, profile_id,
                platform, scoring_policy_version, market_opportunity_score,
                market_confidence, content_worthiness_score, content_confidence,
                tenant_format_fit_score, provenance_usability_score,
                source_selection_score, source_selection_confidence,
                recommended_format, recommended_angle, suggested_timestamps,
                reason_codes, explanation, missing_evidence,
                disqualifying_risks, outcome, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
                source_selection_score = excluded.source_selection_score,
                source_selection_confidence = excluded.source_selection_confidence,
                outcome = excluded.outcome,
                updated_at = excluded.updated_at
            """,
            (
                s.idempotency_key, s.duplicate_fingerprint, s.tenant_id, s.profile_id,
                s.platform, s.scoring_policy_version, s.market_opportunity_score,
                s.market_confidence, s.content_worthiness_score, s.content_confidence,
                s.tenant_format_fit_score, s.provenance_usability_score,
                s.source_selection_score, s.source_selection_confidence,
                s.recommended_format, s.recommended_angle,
                json.dumps(s.suggested_timestamps, sort_keys=True),
                json.dumps(s.reason_codes, sort_keys=True),
                s.explanation,
                json.dumps(s.missing_evidence, sort_keys=True),
                json.dumps(s.disqualifying_risks, sort_keys=True),
                s.outcome,
                _now(), _now(),
            ),
        )
        self._conn.commit()
        return "updated" if exists else "inserted"

    def selection_count(self) -> int:
        return self._conn.execute("SELECT count(*) FROM source_selections").fetchone()[0]

    def selections(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM source_selections ORDER BY created_at"
        ).fetchall()]

    def production_selections(self) -> list[dict[str, Any]]:
        return [
            s for s in self.selections()
            if not is_non_production(s.get("profile_id") or "", s.get("tenant_id") or "")
        ]
