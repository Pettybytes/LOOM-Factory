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
    """Deterministic, disambiguating filename (never trusts user input for ids)."""
    safe_platform = "".join(c for c in (platform or "item").lower() if c.isalnum())[:16]
    safe_id = "".join(c for c in (content_id or "item") if c.isalnum() or c in "-_")[:64]
    return f"{safe_platform}__{safe_id}__{digest[:12]}.{ext}"


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
) -> dict[str, Any]:
    """Validate + build a Reaction Library asset record (provenance + retention)."""
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
        "provenance": {
            "duplicate_fingerprint": duplicate_fingerprint,
            "platform": platform,
            "platform_content_id": platform_content_id,
            "canonical_url": canonical_url,
            "acquisition_job_id": acquisition_job_id,
        },
        "rights_usage_note": (rights_usage_note or "").strip(),
    }


def dedup_digest(record: dict[str, Any]) -> str:
    """Dedup key: the asset's content hash (identical bytes stored once)."""
    return record["asset_hash"]
