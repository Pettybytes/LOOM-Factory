"""Source Brief contract — tenant-scoped, versioned, shareable.

A Source Brief expresses WHAT a tenant wants to discover (topics, keywords,
channels, freshness window, rights policy) without prescribing HOW. It is the
single shared input contract for every discovery connector.

Versioning:
  * ``SCHEMA_VERSION`` — the shape of this contract (bumped on breaking change).
  * ``brief.version`` — the tenant's own revision of a specific brief.

Non-production example briefs live in ``fixtures/`` and are design/test fixtures
only — they do NOT represent provisioned tenants.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0.0"

# Structural fields every brief must carry. Values are validated on load.
REQUIRED_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "profile_id",
    "version",
    "target_platforms",
    "topics",
    "entities",
    "keywords",
    "exclusions",
    "creator_channel_allowlist",
    "creator_channel_blocklist",
    "freshness_window_days",
    "content_type_requirements",
    "language",
    "provenance_rights_requirements",
    "acquisition_eligibility",
    "cost_ceiling_usd",
    "manual_review_required",
    "connector_restrictions",
)


class SourceBriefError(ValueError):
    """Raised when a Source Brief fails structural validation."""


def _require_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise SourceBriefError(f"{field!r} must be a list of strings")
    return [x.strip() for x in value if isinstance(x, str) and x.strip()]


class SourceBrief:
    """A validated, tenant-scoped discovery request.

    Instantiate with ``SourceBrief(**data)`` or ``SourceBrief.from_dict(d)``.
    The object is immutable-by-convention (mutate via ``copy`` if needed).
    """

    def __init__(self, **data: Any) -> None:
        self.schema_version: str = data.get("schema_version") or SCHEMA_VERSION

        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise SourceBriefError(f"missing required fields: {', '.join(missing)}")

        if not isinstance(data["tenant_id"], str) or not data["tenant_id"].strip():
            raise SourceBriefError("tenant_id must be a non-empty string")
        if not isinstance(data["profile_id"], str) or not data["profile_id"].strip():
            raise SourceBriefError("profile_id must be a non-empty string")
        if not isinstance(data["version"], (str, int)):
            raise SourceBriefError("version must be a string or integer")

        self.tenant_id: str = data["tenant_id"].strip()
        self.profile_id: str = data["profile_id"].strip()
        self.version: str = str(data["version"])

        platforms = data["target_platforms"]
        if not isinstance(platforms, list) or not platforms or not all(
            isinstance(p, str) for p in platforms
        ):
            raise SourceBriefError("target_platforms must be a non-empty list of strings")
        self.target_platforms: list[str] = [p.strip().lower() for p in platforms]

        self.topics: list[str] = _require_string_list(data["topics"], "topics")
        self.entities: list[str] = _require_string_list(data["entities"], "entities")
        self.keywords: list[str] = _require_string_list(data["keywords"], "keywords")
        self.exclusions: list[str] = _require_string_list(data["exclusions"], "exclusions")

        self.creator_channel_allowlist: list[str] = _require_string_list(
            data["creator_channel_allowlist"], "creator_channel_allowlist"
        )
        self.creator_channel_blocklist: list[str] = _require_string_list(
            data["creator_channel_blocklist"], "creator_channel_blocklist"
        )

        fw = data["freshness_window_days"]
        if not isinstance(fw, (int, float)) or fw < 0:
            raise SourceBriefError("freshness_window_days must be a number >= 0")
        self.freshness_window_days: float = float(fw)

        self.content_type_requirements: list[str] = _require_string_list(
            data["content_type_requirements"], "content_type_requirements"
        )
        self.language: str = str(data["language"] or "").strip()
        self.provenance_rights_requirements: str = str(
            data["provenance_rights_requirements"] or ""
        ).strip()

        ae = data["acquisition_eligibility"]
        if not isinstance(ae, bool):
            raise SourceBriefError("acquisition_eligibility must be a boolean")
        self.acquisition_eligibility: bool = ae

        cc = data["cost_ceiling_usd"]
        if not isinstance(cc, (int, float)) or cc < 0:
            raise SourceBriefError("cost_ceiling_usd must be a number >= 0")
        self.cost_ceiling_usd: float = float(cc)

        mr = data["manual_review_required"]
        if not isinstance(mr, bool):
            raise SourceBriefError("manual_review_required must be a boolean")
        self.manual_review_required: bool = mr

        self.connector_restrictions: list[str] = _require_string_list(
            data["connector_restrictions"], "connector_restrictions"
        )

        # Optional free-form provenance note, preserved but not required.
        self.min_evidence_requirements: list[str] = _require_string_list(
            data.get("min_evidence_requirements"), "min_evidence_requirements"
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceBrief":
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "profile_id": self.profile_id,
            "version": self.version,
            "target_platforms": list(self.target_platforms),
            "topics": list(self.topics),
            "entities": list(self.entities),
            "keywords": list(self.keywords),
            "exclusions": list(self.exclusions),
            "creator_channel_allowlist": list(self.creator_channel_allowlist),
            "creator_channel_blocklist": list(self.creator_channel_blocklist),
            "freshness_window_days": self.freshness_window_days,
            "content_type_requirements": list(self.content_type_requirements),
            "language": self.language,
            "provenance_rights_requirements": self.provenance_rights_requirements,
            "acquisition_eligibility": self.acquisition_eligibility,
            "cost_ceiling_usd": self.cost_ceiling_usd,
            "manual_review_required": self.manual_review_required,
            "connector_restrictions": list(self.connector_restrictions),
            "min_evidence_requirements": list(self.min_evidence_requirements),
        }
