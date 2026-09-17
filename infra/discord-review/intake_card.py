"""Card-based Manual Source Intake UI — ephemeral interactive wizard.

Pure rendering + in-memory pending-session state (no database clutter). The
adapter owns HTTP/Discord transport; this module owns the card model and the
session state machine.

Flow:
  /submit-source url=<url>  -> new session -> ephemeral card (destination/format
                               selects, Add Details, Submit [disabled until
                               format chosen], Cancel)
  select / modal / submit    -> update session -> re-render card in place
  Submit Source              -> atomically consume session -> persist -> final card

Security:
  * Pending sessions hold only small, non-secret data (canonical URL, platform,
    destination, format, optional timestamps/note, submitter label).
  * Sessions are keyed by an unguessable token, never written to the DB, and
    expired after a bounded idle window (no database clutter).
  * Custom IDs carry only ``<prefix>:<action>:<session token>`` — no secrets or
    large user data.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import manual_intake
from manual_intake import (
    CONTENT_FORMATS,
    DESTINATION_UNASSIGNED_LABEL,
    DESTINATION_UNASSIGNED_TOKEN,
    SubmissionError,
    is_non_production_tenant,
)

# ---------------------------------------------------------------------------
# Custom id encoding (compact, deterministic, safe)
# ---------------------------------------------------------------------------
PREFIX = "mi"

ACTIONS = frozenset({"dest", "fmt", "details", "submit", "cancel"})

FORMAT_LABELS: dict[str, str] = {
    "reaction_single": "Reaction (single)",
    "reaction_compilation": "Reaction (compilation)",
    "commentary_story": "Commentary / story",
    "ranking_top5": "Ranking (top 5)",
    "stream_highlight": "Stream highlight",
    "hold_unassigned": "Hold (unassigned)",
}

SESSION_TTL_SECONDS = 15 * 60  # abandon idle intakes after 15 minutes

_SESSIONS: dict[str, dict[str, Any]] = {}


def cid(action: str, sid: str) -> str:
    return f"{PREFIX}:{action}:{sid}"


def parse_cid(custom_id: str) -> tuple[str, str] | None:
    """Return (action, session_id) for an intake custom_id, else None."""
    parts = (custom_id or "").split(":")
    if len(parts) != 3 or parts[0] != PREFIX:
        return None
    action, sid = parts[1], parts[2]
    if action not in ACTIONS or not sid:
        return None
    return (action, sid)


# ---------------------------------------------------------------------------
# Session store (in-memory, expiring)
# ---------------------------------------------------------------------------
def _purge_expired() -> None:
    now = time.time()
    for sid in [s for s, v in _SESSIONS.items()
                if now - v["created_at"] > SESSION_TTL_SECONDS]:
        _SESSIONS.pop(sid, None)


def new_session(url: str, submitter: str, active_tenants: set[str]) -> dict[str, Any]:
    """Validate + canonicalize the URL (no persistence) and create a session."""
    v = manual_intake.validate_and_canonicalize(url)
    _purge_expired()
    sid = secrets.token_hex(8)
    session: dict[str, Any] = {
        "id": sid,
        "submitter": submitter,
        "platform": v["platform"],
        "canonical_url": v["canonical_url"],
        "original_url": (url or "").strip(),
        "platform_content_id": v["content_id"],
        "destination": "",  # '' == Unassigned / General
        "format": "",       # '' until chosen (Submit disabled until set)
        "start_s": None,
        "end_s": None,
        "note": "",
        "active_tenants": set(active_tenants),
        "created_at": time.time(),
    }
    _SESSIONS[sid] = session
    return session


def get_session(sid: str) -> dict[str, Any] | None:
    _purge_expired()
    return _SESSIONS.get(sid)


def consume_session(sid: str) -> dict[str, Any] | None:
    """Atomically remove a session (double-submit / replay guard)."""
    _purge_expired()
    return _SESSIONS.pop(sid, None)


def is_submittable(session: dict[str, Any]) -> bool:
    """Required: a format has been chosen (destination always has a valid value)."""
    return bool((session.get("format") or "").strip())


# ---------------------------------------------------------------------------
# Select / button component builders
# ---------------------------------------------------------------------------
def _select(custom_id: str, placeholder: str, options: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": 3,
        "custom_id": custom_id,
        "placeholder": placeholder,
        "min_values": 1,
        "max_values": 1,
        "options": options,
    }


def _button(custom_id: str, label: str, style: int, disabled: bool = False) -> dict[str, Any]:
    return {"type": 2, "custom_id": custom_id, "label": label, "style": style,
            "disabled": disabled}


def _dest_options(session: dict[str, Any]) -> list[dict[str, Any]]:
    options = [{"label": DESTINATION_UNASSIGNED_LABEL, "value": DESTINATION_UNASSIGNED_TOKEN,
                "default": session["destination"] == ""}]
    tenants = sorted(t for t in session["active_tenants"] if not is_non_production_tenant(t))
    for t in tenants:
        options.append({"label": t, "value": t, "default": session["destination"] == t})
    return options[:25]


def _fmt_options(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"label": FORMAT_LABELS[f], "value": f, "default": session["format"] == f}
            for f in sorted(CONTENT_FORMATS)]


# ---------------------------------------------------------------------------
# State transitions (mutate the stored session; raise ValueError on bad input)
# ---------------------------------------------------------------------------
def apply_select(session: dict[str, Any], action: str, values: list[str]) -> None:
    value = values[0] if values else ""
    if action == "dest":
        dest = (value or "").strip()
        if dest.lower() == DESTINATION_UNASSIGNED_TOKEN:
            session["destination"] = ""
            return
        if dest not in session["active_tenants"]:
            raise ValueError(f"{dest!r} is not an active tenant")
        if is_non_production_tenant(dest):
            raise ValueError(f"{dest!r} is a non-production tenant")
        session["destination"] = dest
    elif action == "fmt":
        if value not in CONTENT_FORMATS:
            raise ValueError(f"unknown format {value!r}")
        session["format"] = value
    else:
        raise ValueError("unknown select")


def _parse_ts(raw: str) -> float | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        raise ValueError("timestamps must be numbers (seconds)")
    if v < 0:
        raise ValueError("timestamps must be >= 0")
    return v


def apply_details(session: dict[str, Any], start_s: str, end_s: str, note: str) -> None:
    session["start_s"] = _parse_ts(start_s)
    session["end_s"] = _parse_ts(end_s)
    if (session["start_s"] is not None and session["end_s"] is not None
            and session["end_s"] < session["start_s"]):
        raise ValueError("end timestamp must be >= start timestamp")
    session["note"] = (note or "").strip()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _ts_display(session: dict[str, Any]) -> str:
    s, e = session.get("start_s"), session.get("end_s")
    if s is None and e is None:
        return "—"
    def fmt(v: float) -> str:
        return f"{v:g}"
    if s is not None and e is not None:
        return f"{fmt(s)}s → {fmt(e)}s"
    if s is not None:
        return f"start {fmt(s)}s"
    return f"end {fmt(e)}s"


def _dest_display(session: dict[str, Any]) -> str:
    return DESTINATION_UNASSIGNED_LABEL if session["destination"] == "" else session["destination"]


def _safe_url(url: str) -> str:
    return (url or "")[:1000]


def render_card(session: dict[str, Any]) -> dict[str, Any]:
    submittable = is_submittable(session)
    embed = {
        "title": "📥 Manual Source Intake",
        "color": 0x3498DB,
        "fields": [
            {"name": "Platform", "value": f"`{session['platform']}`", "inline": True},
            {"name": "Destination", "value": f"`{_dest_display(session)}`", "inline": True},
            {"name": "Format", "value": f"`{FORMAT_LABELS.get(session['format'], '—')}`", "inline": True},
            {"name": "Source URL", "value": _safe_url(session["canonical_url"])},
            {"name": "Timestamps", "value": f"`{_ts_display(session)}`", "inline": True},
            {"name": "Note", "value": (session["note"] or "—")[:1000], "inline": True},
        ],
        "footer": {"text": "Candidate only — no media downloaded · not yet submitted"},
    }
    components = [
        {"type": 1, "components": [
            _select(cid("dest", session["id"]), "Choose Destination", _dest_options(session)),
        ]},
        {"type": 1, "components": [
            _select(cid("fmt", session["id"]), "Choose Format", _fmt_options(session)),
        ]},
        {"type": 1, "components": [
            _button(cid("details", session["id"]), "Add Details", style=1),
            _button(cid("submit", session["id"]), "Submit Source", style=3,
                    disabled=not submittable),
            _button(cid("cancel", session["id"]), "Cancel", style=2),
        ]},
    ]
    return {"embeds": [embed], "components": components}


def render_cancelled(session: dict[str, Any]) -> dict[str, Any]:
    embed = {
        "title": "🚫 Intake cancelled",
        "color": 0x95A5A6,
        "description": "Nothing was submitted or saved.",
        "footer": {"text": "Candidate only — no media downloaded"},
    }
    return {"embeds": [embed], "components": []}


def details_modal(sid: str, session: dict[str, Any]) -> dict[str, Any]:
    def ts(v: float | None) -> str:
        return "" if v is None else f"{v:g}"
    return {
        "custom_id": cid("details", sid),
        "title": "Submission details (optional)",
        "components": [
            {"type": 1, "components": [{
                "type": 4, "custom_id": "mi_start", "style": 1,
                "label": "Start timestamp (seconds)", "required": False,
                "placeholder": "e.g. 12.5", "value": ts(session.get("start_s")),
            }]},
            {"type": 1, "components": [{
                "type": 4, "custom_id": "mi_end", "style": 1,
                "label": "End timestamp (seconds)", "required": False,
                "placeholder": "e.g. 22.5", "value": ts(session.get("end_s")),
            }]},
            {"type": 1, "components": [{
                "type": 4, "custom_id": "mi_note", "style": 2,
                "label": "Note", "required": False,
                "placeholder": "Optional note", "max_length": 500,
                "value": session.get("note", ""),
            }]},
        ],
    }


def render_confirmation(payload: dict[str, Any], row: dict[str, Any], created: bool) -> dict[str, Any]:
    sub_id = row.get("id", "?")
    routing = manual_intake.routing_label(payload.get("tenant_id", ""))
    support = manual_intake.platform_support_label(payload["platform"])
    dest = DESTINATION_UNASSIGNED_LABEL if (payload.get("tenant_id") or "") == "" else payload["tenant_id"]
    result = "New submission" if created else "Duplicate (already accepted)"
    embed = {
        "title": "✅ Source submitted (candidate only)",
        "color": 0x2ECC71 if created else 0xF1C40F,
        "fields": [
            {"name": "Submission ID", "value": f"`{sub_id}`"},
            {"name": "Platform", "value": f"`{payload['platform']}`", "inline": True},
            {"name": "Destination", "value": f"`{dest}`", "inline": True},
            {"name": "Format", "value": f"`{payload['content_format']}`", "inline": True},
            {"name": "Routing", "value": routing, "inline": True},
            {"name": "Platform support", "value": support, "inline": True},
            {"name": "Result", "value": result, "inline": True},
            {"name": "Source URL", "value": _safe_url(payload["source_url"])},
        ],
        "footer": {"text": "Candidate only — no analysis, score, acquisition, or download. No media downloaded."},
    }
    return {"embeds": [embed], "components": []}
