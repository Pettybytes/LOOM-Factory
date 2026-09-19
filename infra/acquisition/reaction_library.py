"""Reaction Library contract — tenant-neutral acquired source assets.

The Personal Reaction Library stores assets acquired for Bryan's reaction
workflow. It is tenant-neutral (``destination = 'personal_reaction_library'``);
a tenant association may be added later via a separate audited routing action
without rewriting provenance.

Rules:
  * dedup by content hash (sha256) — the same acquired bytes are stored once.
  * deterministic filename (platform + content id + hash prefix).
  * retention: bounded; assets expire after retention_days unless consumed.
  * cleanup: failed/temp partial files are removed; expired assets are purged.
  * provenance is immutable: source fingerprint, platform, content id, canonical
    URL, and the acquisition job id are retained.

Storage destination (PROPOSAL — requires Bryan review before any download):
  files -> GHL Media Storage (Bryan's primary location, dedicated
  '~/Movies/LOOM-Reaction-Library' folder / filesafe CDN); metadata + provenance ->
  Supabase ``loom_reaction_library``. Reuses the approved canonical storage
  (GHL for media, Supabase for ledger); no new cloud vendor.
"""

from __future__ import annotations

import hashlib
from typing import Any

DESTINATION = "personal_reaction_library"
DEFAULT_RETENTION_DAYS = 30

# Accepted MIME types for acquired reaction assets (video/audio only).
ACCEPTED_CONTENT_TYPES = frozenset({
    "video/mp4", "video/webm", "video/quicktime",
    "audio/mp4", "audio/webm", "audio/mpeg",
})


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def filename_for(platform: str, content_id: str, digest: str, ext: str = "mp4") -> str:
    """Legacy identity-only filename (platform + content id + hash prefix).

    Kept for callers that have no title metadata yet. New acquisitions should use
    ``asset_filename()`` so the asset is human-readable.
    """
    safe_platform = "".join(c for c in (platform or "item").lower() if c.isalnum())[:16]
    safe_id = "".join(c for c in (content_id or "item") if c.isalnum() or c in "-_")[:64]
    return f"{safe_platform}__{safe_id}__{digest[:12]}.{ext}"


def asset_filename(*, platform: str, content_id: str, digest: str,
                   creator_name: str = "", creator_handle: str = "",
                   source_title: str = "", caption: str = "", stored_title: str = "",
                   clip_name: str = "", kind: str = "", ext: str = "mp4",
                   taken: "set[str] | frozenset[str] | None" = None) -> dict[str, Any]:
    """Derive the human-readable filename + title fields for one asset."""
    import title_contract
    return title_contract.title_fields(
        platform=platform, content_id=content_id, digest=digest,
        creator_name=creator_name, creator_handle=creator_handle,
        source_title=source_title, caption=caption, stored_title=stored_title,
        clip_name=clip_name, kind=kind, ext=ext, taken=taken)


def plan_library_entry(
    *,
    duplicate_fingerprint: str,
    platform: str,
    platform_content_id: str,
    canonical_url: str,
    acquisition_job_id: str,
    filename: str,
    digest: str,
    size_bytes: int,
    duration_seconds: float | None = None,
    content_type: str = "video/mp4",
    storage_url: str = "",
    rights_usage_note: str = "",
    retention_days: int = DEFAULT_RETENTION_DAYS,
    source_title: str = "",
    display_title: str = "",
    creator_name: str = "",
    creator_handle: str = "",
) -> dict[str, Any]:
    """Validate + build a Reaction Library asset record (provenance + retention).

    Human-readable fields are DISTINCT from identity: ``display_title`` never
    replaces ``platform_content_id``, and ``source_title`` keeps the platform's own
    words. Missing titles stay empty or fall back per the title contract — never
    fabricated.
    """
    import title_contract
    if not digest or len(digest) != 64:
        raise ValueError("digest must be a 64-hex sha256")
    if not filename:
        raise ValueError("filename is required")
    if size_bytes is None or size_bytes < 0:
        raise ValueError("size_bytes must be >= 0")
    if content_type not in ACCEPTED_CONTENT_TYPES:
        raise ValueError(f"unsupported content_type {content_type!r}")
    if retention_days <= 0:
        raise ValueError("retention_days must be > 0")

    title = title_contract.clean_title(display_title) or \
        title_contract.resolve_display_title(
            platform=platform, content_id=platform_content_id,
            source_title=source_title, creator_name=creator_name,
            creator_handle=creator_handle)
    # Human-readable titles are durable inside ``provenance`` (a real JSONB column)
    # rather than as new top-level columns: the table schema is fixed and the
    # persisted record must never carry a key the table does not have. Migration
    # 036 promotes these to first-class columns when it is applied; read them back
    # through ``asset_titles()`` so both shapes work.
    provenance = {
        "duplicate_fingerprint": duplicate_fingerprint,
        "platform": platform,
        "platform_content_id": platform_content_id,
        "canonical_url": canonical_url,
        "acquisition_job_id": acquisition_job_id,
        "source_title": title_contract.clean_title(source_title),
        "display_title": title,
        "creator_name": title_contract.clean_creator(creator_name),
        "creator_handle": title_contract.clean_creator(creator_handle),
    }
    return {
        "duplicate_fingerprint": duplicate_fingerprint,
        "platform": platform,
        "platform_content_id": platform_content_id,
        "canonical_url": canonical_url,
        "acquisition_job_id": acquisition_job_id,
        "destination": DESTINATION,
        "asset_filename": filename,
        "asset_hash": digest,
        "size_bytes": size_bytes,
        "duration_seconds": duration_seconds,
        "content_type": content_type,
        "storage_url": storage_url,
        "retention_days": retention_days,
        "provenance": provenance,
        "rights_usage_note": (rights_usage_note or "").strip(),
    }


def durable_record(record: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a record for persistence and drop empty uuid-typed columns.

    ``acquisition_job_id`` is a uuid column, and an empty string is NOT a valid
    uuid — PostgREST answers 400 ``22P02``. A record with no job row must therefore
    OMIT the key rather than send ``""``. Shared by every acquisition path so the
    rule cannot drift between them.
    """
    import approved_lane as lane
    safe = lane.safe_record(record)
    for key in ("acquisition_job_id",):
        if not str(safe.get(key) or "").strip():
            safe.pop(key, None)
    return safe


TITLE_FIELDS = ("source_title", "display_title", "creator_name", "creator_handle")


def asset_titles(row: dict[str, Any] | None) -> dict[str, str]:
    """Read the human-readable title fields from whichever shape holds them.

    Prefers first-class columns (migration 036); falls back to ``provenance`` so
    rows written before the migration keep working.
    """
    row = row or {}
    prov = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
    out: dict[str, str] = {}
    for field in TITLE_FIELDS:
        value = row.get(field)
        if value in (None, ""):
            value = prov.get(field)
        out[field] = str(value or "")
    out["display_title"] = out["display_title"] or out["source_title"]
    return out


def dedup_digest(record: dict[str, Any]) -> str:
    """Dedup key: the asset's content hash (identical bytes stored once)."""
    return record["asset_hash"]
