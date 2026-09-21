"""Sourcing effectiveness instrumentation, per seed/query.

Instrumentation and durable evidence ONLY: no ML, no automatic query mutation,
no ranking changes. Counters accumulate per (tenant, platform, provider,
seed_key) so sourcing yield over time is measurable.

``seed_key`` comes from ``moment_queries.MomentQuery.seed_key`` — one naming
contract, no parallel key scheme.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

COUNTERS: tuple[str, ...] = (
    "discovered", "market_passed", "market_rejected", "tenant_passed",
    "tenant_rejected", "inspected", "fire_passed", "carded", "approved",
    "held", "rejected",
)
SPEND: tuple[str, ...] = ("provider_cost_usd", "vidiq_credits")


@dataclass(frozen=True)
class SeedYield:
    tenant_id: str
    platform: str
    provider: str
    seed_key: str
    query_text: str = ""
    creator_seed: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    spend: dict[str, float] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        row = {"tenant_id": self.tenant_id, "platform": self.platform,
               "provider": self.provider, "seed_key": self.seed_key,
               "query_text": self.query_text, "creator_seed": self.creator_seed}
        row.update({c: int(self.counts.get(c, 0)) for c in COUNTERS})
        row.update({s: float(self.spend.get(s, 0.0)) for s in SPEND})
        return row


def accumulate(existing: dict[str, Any] | None, events: Iterable[dict[str, Any]],
               identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add a batch of already-deduplicated events onto an existing yield row.

    Counters are integers and spend is summed; identity fields are preserved from
    ``existing`` (or taken from ``identity`` on first sight). Callers pass each
    real event exactly once, so a replay must not re-send the same event.
    """
    base = dict(existing or {})
    if identity:
        for k in ("tenant_id", "platform", "provider", "seed_key", "query_text", "creator_seed"):
            if base.get(k) in (None, "") and identity.get(k) is not None:
                base[k] = identity[k]
    for c in COUNTERS:
        base[c] = int(base.get(c, 0) or 0)
    for s in SPEND:
        base[s] = float(base.get(s, 0.0) or 0.0)
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        for c in COUNTERS:
            v = ev.get(c)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
                base[c] += int(v)
        for s in SPEND:
            v = ev.get(s)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
                base[s] = round(base[s] + float(v), 6)
    return base


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def derived(row: dict[str, Any]) -> dict[str, Any]:
    """Derived effectiveness metrics. None (not 0) when the denominator is 0."""
    g = lambda k: int(row.get(k, 0) or 0)  # noqa: E731
    gs = lambda k: float(row.get(k, 0.0) or 0.0)  # noqa: E731
    discovered, mp, mr = g("discovered"), g("market_passed"), g("market_rejected")
    tp, tr = g("tenant_passed"), g("tenant_rejected")
    fired, approved = g("fire_passed"), g("approved")
    market_seen, tenant_seen = mp + mr, tp + tr
    return {
        "market_pass_rate": _rate(mp, market_seen),
        "market_reject_rate": _rate(mr, market_seen),
        "tenant_pass_rate": _rate(tp, tenant_seen),
        "tenant_reject_rate": _rate(tr, tenant_seen),
        "fire_yield": _rate(fired, discovered),
        "carded_yield": _rate(g("carded"), discovered),
        "approval_rate": _rate(approved, g("carded")),
        "cost_per_discovered": (round(gs("provider_cost_usd") / discovered, 6) if discovered else None),
        "cost_per_fire": (round(gs("provider_cost_usd") / fired, 6) if fired else None),
        "cost_per_approved_moment": (round(gs("provider_cost_usd") / approved, 6) if approved else None),
        "credits_per_discovered": (round(gs("vidiq_credits") / discovered, 4) if discovered else None),
    }


def event(**counts_and_spend: Any) -> dict[str, Any]:
    """Build one event payload, rejecting unknown keys so counters stay honest."""
    unknown = set(counts_and_spend) - set(COUNTERS) - set(SPEND)
    if unknown:
        raise KeyError(f"unknown seed-yield fields: {', '.join(sorted(unknown))}")
    return dict(counts_and_spend)


def event_id(payload: dict[str, Any]) -> str:
    """Stable identity for one sourcing event.

    Retries, callback replays and workflow replays all re-deliver the SAME event,
    so callers pass the id back on the next accumulate and it is applied once.
    Identity is the event's meaningful content (identity fields + deltas), so two
    genuinely different events never collide.
    """
    import hashlib
    import json as _json
    ident = {k: payload.get(k) for k in
             ("tenant_id", "platform", "provider", "seed_key", "query_text", "creator_seed")}
    deltas = {k: payload.get(k, 0) for k in (*COUNTERS, *SPEND)}
    deltas = {k: v for k, v in deltas.items() if v}
    blob = _json.dumps({"i": ident, "d": deltas}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def accumulate_idempotent(existing: dict[str, Any] | None, events: Iterable[dict[str, Any]],
                          identity: dict[str, Any] | None = None,
                          applied_ids: Iterable[str] | None = None,
                          ) -> tuple[dict[str, Any], set[str]]:
    """Replay-safe accumulate: each distinct event is applied at most once.

    Returns the updated row plus the set of applied event ids, which the caller
    persists and passes back on replay. Smallest change that removes
    double-counting without redesigning the event system.
    """
    seen = set(applied_ids or ())
    fresh: list[dict[str, Any]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        eid = ev.get("event_id") or event_id(ev)
        if eid in seen:
            continue
        seen.add(eid)
        fresh.append(ev)
    return accumulate(existing, fresh, identity), seen
