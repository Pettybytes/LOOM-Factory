"""Provider acquisition orchestration (deterministic given injected I/O).

Wires authorization → actor allowlist → circuit breaker → idempotency → input →
cost reservation → run → output validation → local retrieval/validation →
Reaction Library finalization → cost reconciliation, all with injected
transport/downloader/finalizer so the whole path is testable with mocks.
"""

from __future__ import annotations

import cost
import provider


def dispatch(*, job, canonical_url, content_id, platform, actor,
             lifetime_spent_micro, acquisitions_succeeded, already_dispatched,
             transport, downloader, finalizer,
             estimate_micro, is_exact=True) -> dict:
    """Run one provider acquisition. Returns a structured result dict."""
    try:
        provider.verify_authorization(job, canonical_url)
    except provider.ProviderError as e:
        return {"ok": False, "error": e.code, "message": e.message}

    try:
        provider.actor_active(platform, actor)
    except provider.ProviderError as e:
        return {"ok": False, "error": e.code, "message": e.message}

    cs = cost.check_circuit(lifetime_spent_micro, acquisitions_succeeded)
    if cs != "ok":
        return {"ok": False, "error": cs}

    if already_dispatched:
        return {"ok": False, "error": "ALREADY_DISPATCHED", "status": "duplicate"}

    inp = provider.build_input(canonical_url, platform=platform)

    try:
        reserved = cost.reserve_worst_case(estimate_micro, is_exact, lifetime_spent_micro)
    except cost.CostError as e:
        return {"ok": False, "error": e.code, "message": e.message}

    try:
        run = transport.start_run(actor, inp)
        items = transport.get_dataset_items(run["defaultDatasetId"])
    except Exception as e:
        return {"ok": False, "error": getattr(e, "code", None) or type(e).__name__,
                "message": getattr(e, "message", "") or str(e)}

    try:
        out = provider.validate_output(items, content_id=content_id)
    except provider.ProviderError as e:
        return {"ok": False, "error": e.code, "message": e.message}

    try:
        meta = downloader(out["video_url"])
    except Exception as e:
        return {"ok": False, "error": getattr(e, "code", None) or type(e).__name__}

    provenance = provider.build_provenance(actor, out["video_url"])

    try:
        lib = finalizer(meta, provenance)
    except Exception as e:
        return {"ok": False, "error": getattr(e, "code", None) or type(e).__name__}

    actual = cost.reconcile_actual(reserved, estimate_micro)
    return {"ok": True, "status": "acquired", "library": lib,
            "provenance": provenance, "reserved_micro": reserved,
            "actual_micro": actual}
