"""Connector interface — the single shared, compliant contract every source
discovery connector must satisfy.

A connector turns a discovery request (a Source Brief + a query) into a stream
of normalized ``DiscoveryRecord`` objects. Connectors never download media,
never publish, and never authorize distribution.

Each connector declares its own:
  * name + semantic version;
  * pagination/cursor handling;
  * rate limits;
  * authentication requirements (name only, never a secret value);
  * provenance capture;
  * retry behavior;
  * health check;
  * cost reporting;
  * policy restrictions.

Mandatory connectors (roadmap): YouTube/search, Reddit, TikTok. Instagram/
Facebook planned; X deferred. Only YouTube/search is implemented in this
segment.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Iterator

from .discovery_record import DiscoveryRecord
from .source_brief import SourceBrief


class ConnectorError(Exception):
    """Deterministic, non-secret connector error."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.retryable = retryable


class AuthRequiredError(ConnectorError):
    """A connector needs a credential that is not present. The credential is
    referenced by NAME ONLY — never its value."""

    def __init__(self, credential_name: str) -> None:
        super().__init__(
            "AUTH_REQUIRED",
            f"connector requires credential '{credential_name}' (referenced by name only)",
            retryable=False,
        )
        self.credential_name = credential_name


@dataclass(frozen=True)
class RateLimit:
    """Declarative rate-limit envelope (not enforced here; informational)."""

    requests_per_window: int
    window_seconds: int


@dataclass(frozen=True)
class ConnectorHealth:
    ok: bool
    detail: str = ""


@dataclass
class DiscoveryRequest:
    """A single discovery operation bound to one Source Brief.

    ``query`` is a platform-appropriate search/discovery expression. ``cursor``
    carries pagination state; ``limit`` bounds results.
    """

    brief: SourceBrief
    query: str
    cursor: str | None = None
    limit: int = 50

    def to_scope(self) -> dict[str, Any]:
        return {
            "tenant_id": self.brief.tenant_id,
            "profile_id": self.brief.profile_id,
            "brief_version": self.brief.version,
            "query": self.query,
        }


class Connector(abc.ABC):
    """Base contract for all discovery connectors."""

    #: Machine-readable name (e.g. "youtube/search").
    name: str = "connector"
    #: Semantic version of the connector implementation.
    version: str = "1.0.0"
    #: Platforms this connector targets (lowercase).
    platforms: tuple[str, ...] = ()
    #: Credential names this connector may require (names only — never values).
    required_credentials: tuple[str, ...] = ()
    #: Policy restrictions that must be honored by callers.
    policy_restrictions: tuple[str, ...] = ()
    #: Declarative rate limit (informational).
    rate_limit: RateLimit | None = None

    @abc.abstractmethod
    def discover(self, request: DiscoveryRequest) -> Iterator[DiscoveryRecord]:
        """Yield normalized records for a discovery request.

        Must never download media, invoke OpenShorts, generate clips, publish,
        or distribute. Pagination is the connector's responsibility; raise
        ``ConnectorError`` with a deterministic code on failure.
        """

    @abc.abstractmethod
    def health(self) -> ConnectorHealth:
        """Report connector health (no secret values)."""

    def cost_report(self) -> dict[str, Any]:
        """Per-request cost estimate. Free connectors report 0."""
        return {"connector": self.name, "version": self.version, "estimated_cost_usd": 0.0}

    def provenance_note(self, request: DiscoveryRequest) -> str:
        """Deterministic provenance string for evidence capture."""
        return (
            f"connector={self.name} v{self.version}; "
            f"tenant={request.brief.tenant_id}; "
            f"profile={request.brief.profile_id} v{request.brief.version}; "
            f"query={request.query!r}"
        )

    def check_credentials(self, have: set[str]) -> None:
        """Raise ``AuthRequiredError`` (name only) if a required credential is
        missing from ``have`` (a set of credential NAMES available)."""
        missing = [c for c in self.required_credentials if c not in have]
        if missing:
            raise AuthRequiredError(missing[0])

    def retry_backoff_seconds(self, attempt: int) -> float:
        """Deterministic bounded backoff for retryable failures."""
        return float(min(60.0, 2.0 ** max(0, attempt)))
