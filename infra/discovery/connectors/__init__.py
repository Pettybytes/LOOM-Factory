"""Connector registry — map connector names to implementations."""

from __future__ import annotations

from typing import Any

from ..connector import Connector
from .youtube import YouTubeConnector


def available_connectors() -> dict[str, Connector]:
    """Connectors implemented in this segment. Only YouTube is wired; Reddit and
    TikTok are planned (not implemented) and X is deferred."""
    return {"youtube": YouTubeConnector()}


def get_connector(name: str) -> Connector:
    connectors = available_connectors()
    if name not in connectors:
        from ..connector import ConnectorError

        raise ConnectorError(
            "UNKNOWN_CONNECTOR",
            f"connector {name!r} not implemented (planned: youtube, reddit, tiktok)",
            retryable=False,
        )
    return connectors[name]
