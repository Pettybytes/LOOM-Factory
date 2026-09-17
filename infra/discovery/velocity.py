"""Velocity computation from observation history.

Topic Intelligence computes velocity ONLY from valid, time-separated
observations already persisted in ``metric_history``. It never estimates,
extrapolates, or invents a velocity.

An "observation" is one entry of ``metric_history``: ``{"observed_at": ISO,
"metrics": {<metric_name>: <numeric value>, ...}}``. Velocity is the change in
one numeric metric per hour between the earliest and latest distinct
observations.

If fewer than two time-separated observations exist, or no numeric metric is
present, velocity is **unavailable** — reported honestly with a reason, never
fabricated. This is the observation-history contract that makes *real*
velocity computable later: connectors already append one observation per
discovery pass, so as LOOM re-observes a source over time, velocity becomes
available automatically.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VelocityResult:
    """Deterministic velocity result. ``available`` is False unless velocity
    can be computed from >=2 valid time-separated observations."""

    available: bool
    metric: str = ""            # metric name used (e.g. "views")
    value_per_hour: float = 0.0  # signed change per hour
    earliest_obs: str = ""       # ISO timestamp of earliest observation
    latest_obs: str = ""         # ISO timestamp of latest observation
    n_observations: int = 0      # number of distinct time-separated observations
    reason: str = ""             # human-readable; '' when available

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "metric": self.metric,
            "value_per_hour": self.value_per_hour,
            "earliest_obs": self.earliest_obs,
            "latest_obs": self.latest_obs,
            "n_observations": self.n_observations,
            "reason": self.reason,
        }


def _parse_ts(value: Any) -> _dt.datetime | None:
    """Parse an ISO-8601 timestamp defensively. Returns None if unusable."""
    if not value:
        return None
    if isinstance(value, _dt.datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def _numeric(value: Any) -> float | None:
    """Coerce a metric value to float; reject booleans and non-numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def compute_velocity(
    history: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    metric_key: str = "views",
) -> VelocityResult:
    """Compute velocity of ``metric_key`` across persisted observations.

    Velocity = (latest_value - earliest_value) / hours_elapsed, using the
    earliest and latest *distinct* observations. Returns ``VelocityResult``
    with ``available=False`` (and a reason) when it cannot be computed.
    """
    if not history:
        return VelocityResult(
            available=False, metric=metric_key, reason="no observation history"
        )

    # Collect (timestamp, numeric value) pairs for the requested metric.
    points: list[tuple[_dt.datetime, float]] = []
    for obs in history:
        if not isinstance(obs, dict):
            continue
        ts = _parse_ts(obs.get("observed_at"))
        metrics = obs.get("metrics")
        if ts is None or not isinstance(metrics, dict):
            continue
        val = _numeric(metrics.get(metric_key))
        if val is not None:
            points.append((ts, val))

    if not points:
        return VelocityResult(
            available=False,
            metric=metric_key,
            reason=f"metric {metric_key!r} absent from all observations",
        )

    # Sort by timestamp; collapse duplicate timestamps (keep the later value,
    # which is the more recent observation in an append-only history).
    points.sort(key=lambda p: p[0])
    deduped: list[tuple[_dt.datetime, float]] = []
    for ts, val in points:
        if deduped and deduped[-1][0] == ts:
            deduped[-1] = (ts, val)
        else:
            deduped.append((ts, val))

    if len(deduped) < 2:
        return VelocityResult(
            available=False,
            metric=metric_key,
            n_observations=len(deduped),
            reason="only one distinct observation (need >=2 time-separated)",
        )

    earliest_ts, earliest_val = deduped[0]
    latest_ts, latest_val = deduped[-1]
    hours = (latest_ts - earliest_ts).total_seconds() / 3600.0
    if hours <= 0:
        return VelocityResult(
            available=False,
            metric=metric_key,
            n_observations=len(deduped),
            earliest_obs=earliest_ts.isoformat(),
            latest_obs=latest_ts.isoformat(),
            reason="observations are not time-separated (zero/negative elapsed time)",
        )

    value_per_hour = (latest_val - earliest_val) / hours
    return VelocityResult(
        available=True,
        metric=metric_key,
        value_per_hour=value_per_hour,
        earliest_obs=earliest_ts.isoformat(),
        latest_obs=latest_ts.isoformat(),
        n_observations=len(deduped),
        reason="",
    )
