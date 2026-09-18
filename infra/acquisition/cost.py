"""Provider cost reservation, ledger, and circuit breaker (deterministic).

Unit is microdollars (1e-6 USD) so sub-cent provider prices are exact. The
circuit breaker auto-disables dispatch when either the lifetime ceiling or the
max-acquisitions limit is reached. A timeout/partial result must never force a
paid retry: retry is manual and separately authorized.
"""

from __future__ import annotations

MICRO_PER_DOLLAR = 1_000_000

# Pilot bounds (Bryan's decision, 2026-09-17).
DEFAULT_CEILING_MICRO = 1_000_000          # $1.00 lifetime across all Apify charges
DEFAULT_MAX_SUCCESS = 10

# apify/instagram-reel-scraper result cost. Re-baselined to the provider's own
# reported charge: Apify reports usageTotalUsd = 0.0036 for a single-result run
# (observed on runs 0DPOKk2bEwxqVTS0i and gpsMyaQqiBVs8Ardo). The earlier pinned
# $0.0026 was the actor's advertised price and under-stated the real charge.
REEL_SCRAPER_RESULT_MICRO = 3_600


class CostError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def check_circuit(lifetime_spent_micro: int, acquisitions_succeeded: int,
                  *, ceiling_micro: int = DEFAULT_CEILING_MICRO,
                  max_success: int = DEFAULT_MAX_SUCCESS) -> str:
    """Pre-dispatch check. Returns 'ok' or a disabled reason code."""
    if lifetime_spent_micro >= ceiling_micro:
        return "CEILING_REACHED"
    if acquisitions_succeeded >= max_success:
        return "MAX_ACQUISITIONS_REACHED"
    return "ok"


def reserve_worst_case(estimate_micro: int, is_exact: bool,
                       lifetime_spent_micro: int,
                       *, ceiling_micro: int = DEFAULT_CEILING_MICRO) -> int:
    """Reserve the estimated cost, or the full remaining ceiling when unknown.

    Returns reserved_micro (>= 0). Raises CostError when no budget remains or
    the estimate is negative.
    """
    if estimate_micro < 0:
        raise CostError("NEGATIVE_ESTIMATE", "cost estimate must be >= 0")
    remaining = ceiling_micro - lifetime_spent_micro
    if remaining <= 0:
        raise CostError("NO_BUDGET", "no budget remaining")
    reserved = estimate_micro if is_exact else remaining  # unknown -> worst case
    return max(0, min(reserved, remaining))


def reconcile_actual(reserved_micro: int, actual_micro: int) -> int:
    """Return the actual cost to add to lifetime spend, bounded by the reserve."""
    if actual_micro < 0:
        raise CostError("NEGATIVE_ACTUAL", "actual cost must be >= 0")
    return min(actual_micro, reserved_micro)
