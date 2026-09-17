"""Source Review Inbox — pure logic + Supabase I/O.

Surfaces candidates with a COMPLETED Combined Source Selection for Bryan's
review in Discord. Decisions (APPROVE FOR ACQUISITION / HOLD / REJECT) are
persisted to ``loom_source_reviews`` — first terminal decision wins per
``duplicate_fingerprint`` (ON CONFLICT DO NOTHING).

APPROVE FOR ACQUISITION records authorization and triggers NOTHING (no download,
no OpenShorts, no publishing). No LLM, no paid service, no secrets.

Split:
  * pure (testable, no I/O): custom_id encoding, embed/button builders, decision
    payload, outcome color.
  * I/O (url+key args): fetch combined candidates, persist decision, decided set.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

COMMAND_NAME = "source-review"
DEFAULT_LIMIT = 5
MAX_LIMIT = 10

# Decision states + compact button codes.
DECISION_APPROVE = "approved_for_acquisition"
DECISION_HOLD = "hold"
DECISION_REJECT = "rejected"

DECISION_CODES: dict[str, str] = {
    "sa": DECISION_APPROVE,
    "sh": DECISION_HOLD,
    "sr": DECISION_REJECT,
}
CODE_BY_DECISION: dict[str, str] = {v: k for k, v in DECISION_CODES.items()}

DECISION_LABELS: dict[str, str] = {
    DECISION_APPROVE: "Approve for Acquisition",
    DECISION_HOLD: "Hold",
    DECISION_REJECT: "Reject",
}

# Non-production markers (mirrors content_intelligence/store.py; kept inline so
# this module stays self-contained for the adapter).
_NON_PRODUCTION_MARKERS = (
    "acceptance", "test", "proof", "example", "fixture",
    "demo", "sample", "sandbox", "staging", "dev",
)


def _is_non_production(profile_id: str = "", tenant_id: str = "") -> bool:
    hay = f"{(profile_id or '').lower()} {(tenant_id or '').lower()}"
    return any(m in hay for m in _NON_PRODUCTION_MARKERS)

# Outcome -> embed color.
_OUTCOME_COLORS = {
    "fast_track": 0x2ECC71,
    "standard_review": 0x3498DB,
    "emerging": 0x1ABC9C,
    "human_review_low_confidence": 0xF39C12,
    "hold_hype": 0xF1C40F,
    "reject": 0xE74C3C,
    "differentiation_required": 0x9B59B6,
}


def make_source_custom_id(decision: str, fingerprint: str) -> str:
    """Compact custom_id: <code>_<fingerprint> (fingerprint is a SHA-256 hex)."""
    code = CODE_BY_DECISION.get(decision)
    if not code:
        raise ValueError(f"unknown decision {decision!r}")
    return f"{code}_{fingerprint}"


def parse_source_custom_id(custom_id: str) -> dict[str, str] | None:
    """Return {'decision': ..., 'fingerprint': ...} or None if not a source id."""
    if not custom_id or "_" not in custom_id:
        return None
    code, fp = custom_id.split("_", 1)
    if code not in DECISION_CODES or len(fp) != 64:
        return None
    return {"decision": DECISION_CODES[code], "fingerprint": fp}


def class_color(outcome: str) -> int:
    return _OUTCOME_COLORS.get(outcome, 0x95A5A6)


def _compact_timestamps(ts: list[dict[str, Any]] | None) -> str | None:
    if not ts:
        return None
    parts = []
    for t in ts[:5]:
        try:
            parts.append(f"{float(t['start_s']):g}–{float(t['end_s']):g}s")
        except (KeyError, TypeError, ValueError):
            continue
    return " · ".join(parts) if parts else None


def format_source_embed(candidate: dict[str, Any]) -> dict[str, Any]:
    """Build the Discord embed for one combined candidate (both halves)."""
    title = (candidate.get("title") or "(untitled)").strip()
    url = candidate.get("canonical_url") or ""
    market = candidate.get("market_opportunity_score")
    content = candidate.get("content_worthiness_score")
    selection = candidate.get("source_selection_score")
    sel_conf = candidate.get("source_selection_confidence")
    market_conf = candidate.get("market_confidence")
    content_conf = candidate.get("content_confidence")
    outcome = candidate.get("outcome") or ""
    fmt = candidate.get("recommended_format") or "hold_unassigned"
    angle = candidate.get("recommended_angle") or ""

    fields = []

    score_parts = []
    if market is not None:
        score_parts.append(f"market **{market}**")
    if content is not None:
        score_parts.append(f"content **{content}**")
    if selection is not None:
        score_parts.append(f"selection **{selection}**")
    if score_parts:
        fields.append({"name": "Score", "value": " · ".join(score_parts), "inline": True})

    if sel_conf is not None:
        conf_parts = [f"selection {float(sel_conf):.2f}"]
        if market_conf is not None:
            conf_parts.append(f"market {float(market_conf):.2f}")
        if content_conf is not None:
            conf_parts.append(f"content {float(content_conf):.2f}")
        fields.append({"name": "Confidence", "value": " · ".join(conf_parts), "inline": True})

    if outcome:
        fields.append({"name": "Outcome", "value": f"`{outcome}`", "inline": True})
    if fmt:
        fields.append({"name": "Format", "value": f"`{fmt}`", "inline": True})
    if angle:
        fields.append({"name": "Angle", "value": angle[:280], "inline": False})

    reasons = candidate.get("top_reasons") or []
    if reasons:
        lines = [f"• {r}" for r in reasons[:5]]
        fields.append({"name": "Top signals", "value": "\n".join(lines)[:900], "inline": False})

    ts_line = _compact_timestamps(candidate.get("suggested_timestamps"))
    if ts_line:
        fields.append({"name": "Timestamps", "value": ts_line, "inline": False})

    footer = []
    if candidate.get("tenant_id"):
        footer.append(str(candidate["tenant_id"]))
    if candidate.get("profile_id"):
        footer.append(str(candidate["profile_id"]))
    if candidate.get("platform"):
        footer.append(str(candidate["platform"]))

    embed = {
        "title": "🎬 SOURCE REVIEW · CANDIDATE",
        "description": title[:256],
        "color": class_color(outcome),
        "fields": fields,
    }
    if url:
        embed["url"] = url
    if footer:
        embed["footer"] = {"text": " · ".join(footer)[:2048]}
    return embed


def format_source_buttons(fingerprint: str) -> list[dict[str, Any]]:
    buttons = [
        {"type": 2, "style": 3, "label": DECISION_LABELS[DECISION_APPROVE],
         "custom_id": make_source_custom_id(DECISION_APPROVE, fingerprint)},
        {"type": 2, "style": 2, "label": DECISION_LABELS[DECISION_HOLD],
         "custom_id": make_source_custom_id(DECISION_HOLD, fingerprint)},
        {"type": 2, "style": 4, "label": DECISION_LABELS[DECISION_REJECT],
         "custom_id": make_source_custom_id(DECISION_REJECT, fingerprint)},
    ]
    return [{"type": 1, "components": buttons}]


def source_decision_payload(candidate: dict[str, Any], decision: str, reviewer: str,
                            category: str | None = None,
                            instructions: str | None = None) -> dict[str, Any]:
    return {
        "tenant_id": candidate.get("tenant_id", ""),
        "profile_id": candidate.get("profile_id", ""),
        "duplicate_fingerprint": candidate["duplicate_fingerprint"],
        "platform": candidate.get("platform", ""),
        "platform_content_id": candidate.get("platform_content_id", ""),
        "title": candidate.get("title", ""),
        "canonical_url": candidate.get("canonical_url", ""),
        "decision": decision,
        "reviewer": reviewer,
        "rejection_category": category if decision == DECISION_REJECT else None,
        "reviewer_instructions": instructions,
    }


def decided_embed(decision: str, reviewer: str, created: bool) -> dict[str, Any]:
    import time
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    label = DECISION_LABELS.get(decision, decision)
    color = {"approved_for_acquisition": 0x2ECC71, "hold": 0xF1C40F, "rejected": 0xE74C3C}.get(decision, 0x95A5A6)
    return {
        "title": "✅ Decision recorded" if created else "ℹ️ Already decided",
        "color": color,
        "fields": [
            {"name": "Decision", "value": label, "inline": True},
            {"name": "Reviewer", "value": reviewer, "inline": True},
            {"name": "Timestamp", "value": ts, "inline": True},
        ],
    }


# ---------------------------------------------------------------------------
# Supabase I/O (url + key explicit; no secret logging)
# ---------------------------------------------------------------------------
class SourceReviewStoreError(RuntimeError):
    pass


def _headers(key: str, body: bool = False) -> dict[str, str]:
    h = {"apikey": key, "Authorization": f"Bearer {key}", "User-Agent": "LOOM-SourceReview/1.0"}
    if body:
        h["Content-Type"] = "application/json"
    return h


def _get(url: str, key: str, path: str) -> Any:
    req = urllib.request.Request(f"{url}/rest/v1/{path}", headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        raise SourceReviewStoreError(f"HTTP {e.code}: {e.read().decode()[:200]}") from e


def _post(url: str, key: str, table: str, body: dict, *, on_conflict: str,
          ignore_duplicates: bool = False) -> tuple[int, Any]:
    prefer = "resolution=ignore-duplicates,return=representation" if ignore_duplicates \
        else "resolution=merge-duplicates,return=representation"
    req = urllib.request.Request(
        f"{url}/rest/v1/{table}?on_conflict={on_conflict}",
        data=json.dumps(body).encode(),
        headers=_headers(key, body=True) | {"Prefer": prefer},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        raise SourceReviewStoreError(f"HTTP {e.code}: {e.read().decode()[:200]}") from e


def decided_fingerprints(url: str, key: str) -> set[str]:
    rows = _get(url, key, "loom_source_reviews?select=duplicate_fingerprint")
    return {r["duplicate_fingerprint"] for r in rows if isinstance(r, dict)} if isinstance(rows, list) else set()


def fetch_pending_candidates(url: str, key: str, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Return top pending (completed-selection, production-excluded, not-decided)
    candidates ordered by source_selection_score desc."""
    sel_rows = _get(url, key, "loom_source_selections?select=*&order=source_selection_score.desc")
    src_rows = _get(url, key, "loom_discovered_sources?select=duplicate_fingerprint,title,canonical_url,platform_content_id")
    ca_rows = _get(url, key, "loom_content_assessments?select=duplicate_fingerprint,reasons,content_confidence,evidence_level")
    decided = decided_fingerprints(url, key)

    src_by_fp = {r["duplicate_fingerprint"]: r for r in (src_rows or []) if isinstance(r, dict)}
    ca_by_fp = {r["duplicate_fingerprint"]: r for r in (ca_rows or []) if isinstance(r, dict)}

    out: list[dict[str, Any]] = []
    for s in (sel_rows or []):
        if not isinstance(s, dict):
            continue
        fp = s.get("duplicate_fingerprint", "")
        if _is_non_production(s.get("profile_id") or "", s.get("tenant_id") or ""):
            continue
        if fp in decided:
            continue
        src = src_by_fp.get(fp, {})
        ca = ca_by_fp.get(fp, {})
        reasons = [r.get("message") for r in (ca.get("reasons") or []) if isinstance(r, dict) and r.get("message")]
        out.append({
            "duplicate_fingerprint": fp,
            "tenant_id": s.get("tenant_id", ""),
            "profile_id": s.get("profile_id", ""),
            "platform": s.get("platform", src.get("platform", "")),
            "platform_content_id": src.get("platform_content_id", s.get("platform_content_id", "")),
            "title": src.get("title", ""),
            "canonical_url": src.get("canonical_url", ""),
            "market_opportunity_score": s.get("market_opportunity_score"),
            "market_confidence": s.get("market_confidence"),
            "content_worthiness_score": s.get("content_worthiness_score"),
            "content_confidence": s.get("content_confidence"),
            "source_selection_score": s.get("source_selection_score"),
            "source_selection_confidence": s.get("source_selection_confidence"),
            "outcome": s.get("outcome", ""),
            "recommended_format": s.get("recommended_format", ""),
            "recommended_angle": s.get("recommended_angle", ""),
            "suggested_timestamps": s.get("suggested_timestamps") or [],
            "top_reasons": reasons,
        })
        if len(out) >= limit:
            break
    return out


def fetch_candidate_by_fingerprint(url: str, key: str, fp: str) -> dict[str, Any] | None:
    """Look up one combined candidate by duplicate_fingerprint (for button clicks)."""
    q = urllib.parse.quote(fp)
    sel = _get(url, key, f"loom_source_selections?duplicate_fingerprint=eq.{q}&select=*&limit=1")
    src = _get(url, key, f"loom_discovered_sources?duplicate_fingerprint=eq.{q}&select=title,canonical_url,platform_content_id&limit=1")
    ca = _get(url, key, f"loom_content_assessments?duplicate_fingerprint=eq.{q}&select=reasons,content_confidence,evidence_level&limit=1")
    s = (sel or [{}])[0] if isinstance(sel, list) else {}
    if not isinstance(s, dict) or not s.get("duplicate_fingerprint"):
        return None
    src_r = (src or [{}])[0] if isinstance(src, list) else {}
    ca_r = (ca or [{}])[0] if isinstance(ca, list) else {}
    reasons = [r.get("message") for r in (ca_r.get("reasons") or []) if isinstance(r, dict) and r.get("message")]
    return {
        "duplicate_fingerprint": fp,
        "tenant_id": s.get("tenant_id", ""),
        "profile_id": s.get("profile_id", ""),
        "platform": s.get("platform", ""),
        "platform_content_id": src_r.get("platform_content_id", ""),
        "title": src_r.get("title", ""),
        "canonical_url": src_r.get("canonical_url", ""),
        "market_opportunity_score": s.get("market_opportunity_score"),
        "market_confidence": s.get("market_confidence"),
        "content_worthiness_score": s.get("content_worthiness_score"),
        "content_confidence": s.get("content_confidence"),
        "source_selection_score": s.get("source_selection_score"),
        "source_selection_confidence": s.get("source_selection_confidence"),
        "outcome": s.get("outcome", ""),
        "recommended_format": s.get("recommended_format", ""),
        "recommended_angle": s.get("recommended_angle", ""),
        "suggested_timestamps": s.get("suggested_timestamps") or [],
        "top_reasons": reasons,
    }


def persist_decision(url: str, key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Persist a source decision (first terminal decision wins). Returns (row, created)."""
    status, rows = _post(url, key, "loom_source_reviews", payload,
                         on_conflict="duplicate_fingerprint", ignore_duplicates=True)
    inserted = isinstance(rows, list) and len(rows) > 0
    if inserted:
        return rows[0], True
    # Already decided — return existing authoritative row.
    fp = payload["duplicate_fingerprint"]
    q = urllib.parse.quote(fp)
    existing = _get(url, key, f"loom_source_reviews?duplicate_fingerprint=eq.{q}&select=*")
    if isinstance(existing, list) and existing:
        return existing[0], False
    return payload, True
