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
import re
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
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("source card requires a tenant-scoped SHA-256 fingerprint")
    return f"{code}_{fingerprint}"


def parse_source_custom_id(custom_id: str) -> dict[str, str] | None:
    """Return {'decision': ..., 'fingerprint': ...} or None if not a source id."""
    if not custom_id or "_" not in custom_id:
        return None
    code, fp = custom_id.split("_", 1)
    if code not in DECISION_CODES or not re.fullmatch(r"[0-9a-f]{64}", fp):
        return None
    return {"decision": DECISION_CODES[code], "fingerprint": fp}


def class_color(outcome: str) -> int:
    return _OUTCOME_COLORS.get(outcome, 0x95A5A6)


def _fmt_seconds(s: float, force_hms: bool = False) -> str:
    """Human display of seconds: MM:SS under 1 hour, H:MM:SS at/over 1 hour.
    Preserve subsecond boundaries to millisecond precision; never make a short
    resolved moment appear to start/end at a different whole second."""
    milliseconds = int(round(max(0.0, float(s)) * 1000))
    s, remainder = divmod(milliseconds, 1000)
    fraction = f".{remainder:03d}".rstrip("0") if remainder else ""
    if force_hms or s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}{fraction}"
    return f"{s // 60:02d}:{s % 60:02d}{fraction}"


def _format_timestamps(ts: list[dict[str, Any]] | None) -> str | None:
    """Format suggested moments one per line: MM:SS–MM:SS (or H:MM:SS–H:MM:SS),
    with an optional reason/label. Never shows raw '110–116.5s' style."""
    if not ts:
        return None
    lines: list[str] = []
    for t in ts[:5]:
        try:
            start = float(t["start_s"])
            end = float(t["end_s"])
            force_hms = end >= 3600
            pair = f"{_fmt_seconds(start, force_hms)}–{_fmt_seconds(end, force_hms)}"
            basis = (t.get("basis") or "").strip()
            lines.append(f"{pair} — {basis}" if basis else pair)
        except (KeyError, TypeError, ValueError):
            continue
    return "\n".join(lines) if lines else None


def _thumbnail_url(platform: str, platform_content_id: str) -> str | None:
    """Safely-available source thumbnail (public CDN). None when unavailable."""
    if (platform or "").lower() == "youtube" and platform_content_id:
        return f"https://i.ytimg.com/vi/{platform_content_id}/hqdefault.jpg"
    return None


def format_source_embed(candidate: dict[str, Any], *, fixture: bool = False) -> dict[str, Any]:
    """Build the Discord embed for one combined candidate (mirrors #clip-review)."""
    title = (candidate.get("title") or "(untitled)").strip()
    url = candidate.get("canonical_url") or ""
    platform = candidate.get("platform") or ""
    content_id = candidate.get("platform_content_id") or ""
    creator = candidate.get("creator_channel_identity") or ""
    published = candidate.get("publication_timestamp") or ""
    market = candidate.get("market_opportunity_score")
    content = candidate.get("content_worthiness_score")
    selection = candidate.get("source_selection_score")
    content_conf = candidate.get("content_confidence")
    outcome = candidate.get("outcome") or ""
    fmt = candidate.get("recommended_format") or "hold_unassigned"
    angle = candidate.get("recommended_angle") or ""

    header = "🔎 SOURCE REVIEW · " + fmt.replace("_", " ").upper()

    fields: list[dict[str, Any]] = []

    # 2. Source identity (platform · creator · freshness).
    meta = [platform] if platform else []
    if creator:
        meta.append(creator)
    if published:
        try:
            import datetime as _dt
            dt = _dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
            meta.append(dt.strftime("%Y-%m-%d"))
        except (ValueError, AttributeError):
            meta.append(published[:10])
    if meta:
        fields.append({"name": "Source", "value": " · ".join(meta), "inline": True})

    # 4-6. Combined + sub-scores (important first).
    score_bits = []
    if selection is not None:
        score_bits.append(f"Selection **{selection}**")
    if market is not None:
        score_bits.append(f"Market **{market}**")
    if content is not None:
        score_bits.append(f"Content **{content}**")
    if score_bits:
        fields.append({"name": "Scores", "value": " · ".join(score_bits), "inline": True})

    # 7. Confidence (honest: transcript-only stays low).
    if content_conf is not None:
        pct = int(round(float(content_conf) * 100))
        fields.append({"name": "Confidence", "value": f"**{pct}%** · Transcript only", "inline": True})

    # 8. Why selected (outcome + top reasons).
    reasons = candidate.get("top_reasons") or []
    why = f"`{outcome}`" if outcome else ""
    if reasons:
        why += ("\n" if why else "") + "\n".join(f"• {r}" for r in reasons[:3])
    if why:
        fields.append({"name": "Why selected", "value": why[:600], "inline": False})

    # 9. Suggested timestamps.
    plan = candidate.get("boundary_plan") or {}
    start, end = plan.get("recommended_clip_start"), plan.get("recommended_clip_end")
    resolved = ([{"start_s": start, "end_s": end, "basis": "resolved moment"}]
                if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start else None)
    ts_line = _format_timestamps(resolved or candidate.get("suggested_timestamps"))
    if ts_line:
        fields.append({"name": "Clip window" if resolved else "Suggested timestamps", "value": ts_line, "inline": False})

    # 10. Recommended angle.
    if angle:
        fields.append({"name": "Recommended angle", "value": angle[:280], "inline": False})

    embed = {
        "title": header,
        "description": title[:256],
        "color": class_color(outcome),
        "fields": fields,
    }
    if url:
        embed["url"] = url
    thumb = _thumbnail_url(platform, content_id)
    if thumb:
        embed["thumbnail"] = {"url": thumb}

    footer = []
    if fixture:
        footer.append("🧪 TEST FIXTURE · downstream locked")
    if candidate.get("tenant_id"):
        footer.append(str(candidate["tenant_id"]))
    if candidate.get("profile_id"):
        footer.append(str(candidate["profile_id"]))
    if footer:
        embed["footer"] = {"text": " · ".join(footer)[:2048]}
    evidence = candidate.get("moment_evidence") or {}
    if evidence.get("intent"):
        embed = format_mining_embed(candidate, embed)
    elif (candidate.get("profile_id") == "manual-intake"
          or evidence.get("lineage", {}).get("origin") == "manual_media_intake"):
        # Manual Intake: the operator deliberately supplied this asset for human
        # review. CI declining it (or being unable to score it) must render as the
        # TRUTH, never as "HOLD UNASSIGNED / 0 / 0 / 0" — those zeros are coerced
        # defaults, not measurements. Unknown stays unknown; the human still decides.
        embed = format_manual_not_recommended_embed(candidate, embed)
    return embed


def format_mining_embed(c, base):
    """Acceptance source card from durable resolved evidence, not start anchors."""
    p, me = c.get("boundary_plan") or {}, c.get("moment_evidence") or {}
    market = me.get("market_evidence") or {}
    context = me.get("review_context") or {}
    fields = []
    def add(name, value, inline=False):
        fields.append({"name": name, "value": str(value or "Unavailable")[:1000], "inline": inline})
    def val(key):
        v = market.get(key)
        return "Unavailable" if v is None else str(v)
    add("Identity", f"Tenant: {c.get('tenant_id')} · {c.get('platform')}\nCreator: {c.get('creator_channel_identity')}\nTitle: {c.get('title')}\nIntent: {me.get('intent')}")
    funnel = context.get("funnel") or {}
    add("Funnel", " · ".join(f"{k.replace('_', ' ')}: {v}" for k,v in funnel.items()) or "See recorded run evidence")
    # Market is only a MEASUREMENT when real demand evidence was observed. With no
    # evidence the score is an absence, not a zero result — rendering "0/100"
    # would state poor performance the data never showed.
    coverage = market.get("evidence_coverage")
    fields_seen = market.get("evidence_fields") or {}
    if market.get("evidence_available") is False:
        market_line = ("Market **unavailable** — no market evidence observed "
                       "(not a zero score)")
    else:
        market_line = f"Market **{c.get('market_opportunity_score')} / 100**"
    if isinstance(coverage, (int, float)) and fields_seen:
        market_line += (f" · Coverage: "
                        f"{sum(1 for v in fields_seen.values() if v)}/{len(fields_seen)}")
    if market.get("provenance"):
        market_line += f" · Provenance: {market.get('provenance')}"
    add("Market evidence", "\n".join([
        f"Views/plays {val('views')} · Likes {val('likes')} · Comments {val('comments')}",
        f"Native demand {val('demand_volume')} ({val('demand_metric')})",
        f"Shares {val('shares')} · Saves {val('saves')} · Age {val('age_hours')} h",
        f"Lifetime avg VPH {val('lifetime_average_vph')} · Observed velocity/h {val('observed_velocity_per_hour')}",
        f"Engagement {val('engagement')} · Outlier {val('outlier_multiple')} · Route {val('route')}",
        market_line]))
    assessment = c.get("media_assessment") or {}
    _reasons = [r.get("message") for r in (assessment.get("reasons") or [])
                if isinstance(r, dict) and r.get("message")]
    qualification = me.get("qualification") or {}
    _concepts = [x.get("observation") for x in (qualification.get("concept_evidence") or [])
                 if isinstance(x, dict) and x.get("observation")]
    _analyzed = bool(assessment or qualification)
    _had_moment = bool(me.get("what_happens") or me.get("inspected_moments")
                       or assessment.get("moments"))
    # The analyzer ran and found no qualifying moment. Say exactly that, with its
    # own reason, instead of rendering real analysis as missing data.
    # None-safe: a candidate with no analyzer output at all (empty media_assessment
    # AND no qualification evidence) previously produced `None + str` → TypeError
    # while rendering, so a REAL durable candidate died at review_card. Render an
    # explicit value instead; `add()` already maps empty values to "Unavailable".
    _moment_text = (me.get("what_happens")
                    or ("No qualifying moment identified" if _analyzed else "Unavailable"))
    add("Moment", f"{_moment_text}\nType: {(c.get('moment_type') or {}).get('moment_type', 'unknown')}")
    if all(isinstance(p.get(k), (int,float)) for k in ('recommended_clip_start','recommended_clip_end')):
        s,e=p['recommended_clip_start'],p['recommended_clip_end']
        event = p.get('event_timestamp')
        event_label = _fmt_seconds(event) if isinstance(event, (int, float)) else "Unavailable"
        add("Resolved window", f"Event {event_label}\nStart {_fmt_seconds(s)} · End {_fmt_seconds(e)} · Duration {e-s:g}s")
    add("Why selected", me.get("why_selected") or ("; ".join(_reasons) if _reasons else None))
    add("First 0–3 second hook", me.get("hook_0_3s") or (
        "Not established — no qualifying moment" if (_analyzed and not _had_moment) else None))
    add("Payoff / resolution", me.get("payoff") or (
        "Not established — no qualifying moment" if (_analyzed and not _had_moment) else None))
    if _analyzed and not _had_moment and (_reasons or _concepts):
        _lines = []
        if _concepts:
            _lines.append("Observed: " + "; ".join(_concepts[:3]))
        _lines.extend(f"• {r}" for r in _reasons[:3])
        _scores = assessment.get("scores") or {}
        _keys = ("hook", "relatability", "story_progression", "narrative_completeness")
        _shown = [f"{k} {_scores[k]}" for k in _keys if k in _scores]
        if _shown:
            _lines.append("Dimensions: " + " · ".join(_shown))
        add("Analyzer findings", "\n".join(_lines))
    add("Reaction / commentary angle", me.get("reaction_angle"))
    add("Completeness & boundary evidence", f"{c.get('completeness_verdict')}\nStart: {p.get('start_reason')}\nEnd: {p.get('end_reason')}")
    add("Quality", f"Content {c.get('content_worthiness_score')} · Selection {c.get('source_selection_score')} · Confidence {c.get('source_selection_confidence')} · FIRE {'PASS' if c.get('fire_passed') else 'FAIL'}")
    if context.get("review_mode") == "human_curated":
        add("Review status", "HUMAN REVIEW OPTION — scores are advisory; not automatic Grade-A acceptance.\n"
            + str(context.get("review_reason") or "Operator requested review of this complete inspected moment."))
        add("Ranking", f"Review option #{context.get('review_rank')} of {context.get('review_count')} complete inspected options")
        header = "SEGMENT 5 — SOURCE MOMENT FOR YOUR REVIEW"
    else:
        add("Ranking", f"Moment rank #{c.get('source_relative_rank')} of {c.get('source_candidate_count', 0)} complete qualifying moments")
        header = "SEGMENT 5 — VIRAL MOMENT MINING ACCEPTANCE"
    base.update(title=header, fields=fields)
    # A platform-derived thumbnail URL alone is not verification that the image
    # exists or represents this candidate. Strict cards only embed a verified
    # candidate thumbnail, or a verified still from within the resolved window.
    base.pop("thumbnail", None)
    base.pop("image", None)
    if context.get("verified_thumbnail_url"):
        base["image"] = {"url": context["verified_thumbnail_url"]}
    elif context.get("verified_frame_url"):
        frame_time = context.get("verified_frame_timestamp_s")
        start, end = p.get("recommended_clip_start"), p.get("recommended_clip_end")
        if all(isinstance(v, (int, float)) for v in (frame_time, start, end)) and start <= frame_time <= end:
            base["image"] = {"url": context["verified_frame_url"]}
    return base


def format_source_buttons(fingerprint: str, *, disabled: bool = False,
                          source_url: str = "", manual_media: bool = False) -> list[dict[str, Any]]:
    """Buttons in order: Approve (green), Reject (red), Hold (gray), Open Source (link).

    ``manual_media`` candidates are CLIP approvals (their media is already stored),
    so the action is named for what it does. The action itself is unchanged and
    still creates zero acquisition jobs for supplied media.
    """
    approve_label = "Approve Clip" if manual_media else DECISION_LABELS[DECISION_APPROVE]
    buttons = [
        {"type": 2, "style": 3, "label": approve_label,
         "custom_id": make_source_custom_id(DECISION_APPROVE, fingerprint), "disabled": disabled},
        {"type": 2, "style": 4, "label": DECISION_LABELS[DECISION_REJECT],
         "custom_id": make_source_custom_id(DECISION_REJECT, fingerprint), "disabled": disabled},
        {"type": 2, "style": 2, "label": DECISION_LABELS[DECISION_HOLD],
         "custom_id": make_source_custom_id(DECISION_HOLD, fingerprint), "disabled": disabled},
    ]
    if source_url:
        buttons.append({"type": 2, "style": 5, "label": "Open Source", "url": source_url})
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


def _candidates_table_available(url: str, key: str) -> bool:
    """Is the authoritative candidate table live (migration 040 applied)?"""
    try:
        rows = _get(url, key, "loom_source_candidates?select=duplicate_fingerprint&limit=1")
        return isinstance(rows, list)
    except SourceReviewStoreError:
        return False


def minimal_decision_embed(decision_embed: dict[str, Any],
                           candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    """A safe, never-raising card that still SHOWS a persisted decision.

    Safety net for the case that matters: the decision is already durable and the
    rich render fails. The operator must never click a successful button and see
    nothing happen, so this reads only plain scalars from the candidate and falls
    back to a fixed shape if anything at all is unexpected.
    """
    candidate = candidate or {}
    embed: dict[str, Any] = {
        "title": decision_embed.get("title") or "Decision recorded",
        "color": decision_embed.get("color", 0x95A5A6),
        "fields": list(decision_embed.get("fields") or []),
    }
    try:
        bits = []
        if candidate.get("platform"):
            bits.append(str(candidate["platform"]))
        if candidate.get("platform_content_id"):
            bits.append(str(candidate["platform_content_id"]))
        if candidate.get("tenant_id"):
            bits.append(str(candidate["tenant_id"]))
        desc = (candidate.get("canonical_url") or candidate.get("title") or "")
        if desc:
            embed["description"] = str(desc)[:256]
        if bits:
            embed.setdefault("fields", []).append(
                {"name": "Source", "value": " · ".join(bits)[:256], "inline": False})
    except Exception:  # noqa: BLE001 — a fallback card must not itself fail
        pass
    return embed


def format_manual_not_recommended_embed(c, base: dict[str, Any]) -> dict[str, Any]:
    """Manual Intake card when Content Intelligence did not recommend the asset.

    Renders the true state and keeps the operator override. An unmeasured score is
    reported as UNAVAILABLE — never as 0. No score is manufactured and no CI
    threshold is weakened; the human decision is what remains.
    """
    reason = str(c.get("rejection_reason") or "").strip()
    status = str(c.get("status") or "").strip()
    outcome = "CI: NOT RECOMMENDED" if reason else "CI: ANALYSIS UNAVAILABLE"

    fields: list[dict[str, Any]] = []
    platform, creator = c.get("platform") or "", c.get("creator_channel_identity") or ""
    src = " · ".join(x for x in (platform, creator) if x)
    if src:
        fields.append({"name": "Source", "value": src[:256], "inline": True})
    fields.append({"name": "Outcome", "value": outcome[:256], "inline": True})
    if reason:
        fields.append({"name": "Reason", "value": f"`{reason}`"[:256], "inline": False})

    # Available source engagement evidence, from the durable candidate evidence or
    # the ingress-captured source metadata. One missing field must NOT hide the
    # others, and an unmeasured field stays Unavailable rather than becoming 0.
    evidence = (c.get("moment_evidence") or {}).get("market_evidence") or {}
    lineage = (c.get("moment_evidence") or {}).get("lineage") or {}
    captured = lineage.get("source_metadata") or {}
    captured_metrics = captured.get("metrics") or {}

    def _ev(key: str) -> str:
        value = evidence.get(key)
        if value is None:
            value = captured_metrics.get(key)
        return "Unavailable" if value is None else str(value)

    if evidence or captured_metrics:
        published = captured.get("publication_timestamp") or ""
        published_line = "Published Unavailable"
        if published:
            try:
                import datetime as _dt
                _p = _dt.datetime.fromisoformat(str(published).replace("Z", "+00:00"))
                _age = (_dt.datetime.now(_dt.timezone.utc) - _p).total_seconds() / 3600.0
                published_line = f"Published {_p.strftime('%Y-%m-%d')} · Age {_age:.1f} h"
            except (ValueError, AttributeError):
                published_line = f"Published {str(published)[:10]}"
        fields.append({"name": "Source engagement", "value": "\n".join([
            f"Views/plays {_ev('views')} · Likes {_ev('likes')} · Comments {_ev('comments')}",
            f"Shares {_ev('shares')} · Saves {_ev('saves')}",
            published_line])[:512], "inline": False})

    # Honest availability, read from the row itself. `has_real` is true only when a
    # score was genuinely measured, so a coerced 0 cannot masquerade as a value.
    def _score(field: str) -> str:
        v = c.get(field)
        return str(v) if isinstance(v, (int, float)) and v else "unavailable"
    fields.append({"name": "Analysis", "value": "\n".join([
        f"Market: {_score('market_opportunity_score')}",
        f"Content: {_score('content_worthiness_score')}",
        f"Selection: {_score('source_selection_score')}"])[:512], "inline": False})
    fields.append({"name": "Status", "value": (f"`{status}`" if status else "no scoring state")[:128],
                   "inline": True})
    fields.append({"name": "Human decision required", "value": (
        "LOOM informs. You decide.\n"
        "Approve Clip / Reject / Hold — the asset stays reviewable either way.")[:512],
        "inline": False})

    out = dict(base)
    out["title"] = f"📥 MANUAL INTAKE · {outcome}"
    out["fields"] = fields
    return out


def remember_card_message(url: str, key: str, fingerprint: str, message_id: str) -> bool:
    """Record the Discord review card id on the candidate row.

    Enables ONE manual submission -> ONE active card: a later re-analysis edits
    that same card instead of posting a second one. Stored inside the existing
    `moment_evidence.lineage` JSONB (no new column, no DDL). Never raises — a
    failed bookkeeping write must not fail an intake that already succeeded.
    """
    if not (fingerprint and message_id):
        return False
    try:
        rows = _get(url, key, "loom_source_candidates?duplicate_fingerprint=eq."
                    + urllib.parse.quote(fingerprint) + "&select=moment_evidence&limit=1")
        if not isinstance(rows, list) or not rows:
            return False
        me = dict(rows[0].get("moment_evidence") or {})
        lineage = dict(me.get("lineage") or {})
        lineage["card_message_id"] = str(message_id)
        me["lineage"] = lineage
        body = json.dumps({"moment_evidence": me}).encode()
        req = urllib.request.Request(
            f"{url}/rest/v1/loom_source_candidates?duplicate_fingerprint=eq."
            + urllib.parse.quote(fingerprint),
            data=body, method="PATCH",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20):
            return True
    except Exception:  # noqa: BLE001
        return False


def _candidate_from_row(s: dict[str, Any]) -> dict[str, Any]:
    """Map one authoritative candidate row onto the queue's card contract."""
    return {
        "duplicate_fingerprint": s.get("duplicate_fingerprint", ""),
        "tenant_id": s.get("tenant_id", ""),
        "profile_id": s.get("profile_id", ""),
        "platform": s.get("platform", ""),
        "platform_content_id": s.get("platform_content_id", ""),
        "title": s.get("title", ""),
        "canonical_url": s.get("canonical_url", ""),
        "creator_channel_identity": s.get("creator_channel_identity", ""),
        "publication_timestamp": s.get("publication_timestamp", ""),
        "market_opportunity_score": s.get("market_score"),
        "content_worthiness_score": s.get("content_score"),
        "source_selection_score": s.get("selection_score"),
        "source_selection_confidence": s.get("confidence_score"),
        "outcome": s.get("selection_outcome", ""),
        "fire_passed": s.get("fire_passed", False),
        "fire_reasons": s.get("fire_reasons") or [],
        "completeness_verdict": s.get("completeness_verdict", ""),
        "duplicate_state": s.get("duplicate_state", ""),
        # Carried so the card can state the TRUTH for a CI-declined manual asset
        # instead of rendering coerced zeros as if they were measurements.
        "status": s.get("status", ""),
        "rejection_reason": s.get("rejection_reason", ""),
        "source_relative_rank": s.get("source_relative_rank"),
        "source_candidate_count": s.get("source_candidate_count", 0),
        "moment_evidence": s.get("moment_evidence") or {},
        "moment_type": s.get("moment_type") or {},
        "media_assessment": s.get("media_assessment") or {},
        "boundary_plan": s.get("boundary_plan") or {},
        "suggested_timestamps": (s.get("boundary_plan") or {}).get("start_anchors") or [],
        "top_reasons": [],
    }


def fetch_pending_candidates(url: str, key: str, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Return top pending candidates ordered by final selection score.

    Reads the AUTHORITATIVE post-FIRE candidate table (migration 040) when it
    exists: FIRE-passing, non-suppressed, production-excluded, not-decided.
    Falls back to the legacy selections join only while the table is absent.
    """
    decided = decided_fingerprints(url, key)
    if _candidates_table_available(url, key):
        rows = _get(url, key,
                    "loom_source_candidates?select=*&or=(fire_passed.is.true,moment_evidence->review_context->>review_mode.eq.human_curated)"
                    "&duplicate_state=neq.DUPLICATE_SUPPRESSED"
                    "&order=selection_score.desc")
        out: list[dict[str, Any]] = []
        for s in (rows or []):
            if not isinstance(s, dict):
                continue
            context = (s.get("moment_evidence") or {}).get("review_context") or {}
            if not s.get("fire_passed") and (context.get("review_mode") != "human_curated"
                    or s.get("completeness_verdict") != "PASS"
                    or not s.get("boundary_plan")):
                continue
            if _is_non_production(s.get("profile_id") or "", s.get("tenant_id") or ""):
                continue
            if s.get("duplicate_fingerprint", "") in decided:
                continue
            out.append(_candidate_from_row(s))
            if len(out) >= limit:
                break
        return out

    sel_rows = _get(url, key, "loom_source_selections?select=*&order=source_selection_score.desc")
    src_rows = _get(url, key, "loom_discovered_sources?select=duplicate_fingerprint,title,canonical_url,platform_content_id,creator_channel_identity,publication_timestamp")
    ca_rows = _get(url, key, "loom_content_assessments?select=duplicate_fingerprint,reasons,content_confidence,evidence_level")

    src_by_fp = {r["duplicate_fingerprint"]: r for r in (src_rows or []) if isinstance(r, dict)}
    ca_by_fp = {r["duplicate_fingerprint"]: r for r in (ca_rows or []) if isinstance(r, dict)}

    out = []
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
            "creator_channel_identity": src.get("creator_channel_identity", ""),
            "publication_timestamp": src.get("publication_timestamp", ""),
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
    if _candidates_table_available(url, key):
        q = urllib.parse.quote(fp)
        rows = _get(url, key, f"loom_source_candidates?duplicate_fingerprint=eq.{q}&select=*&limit=1")
        if isinstance(rows, list) and rows:
            return _candidate_from_row(rows[0])
        return None
    q = urllib.parse.quote(fp)
    sel = _get(url, key, f"loom_source_selections?duplicate_fingerprint=eq.{q}&select=*&limit=1")
    src = _get(url, key, f"loom_discovered_sources?duplicate_fingerprint=eq.{q}&select=title,canonical_url,platform_content_id,creator_channel_identity,publication_timestamp&limit=1")
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
        "creator_channel_identity": src_r.get("creator_channel_identity", ""),
        "publication_timestamp": src_r.get("publication_timestamp", ""),
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
