"""Acquisition Card — premium operator surface for the approved acquisition lane.

PRESENTATION ONLY. This module owns the *view model*, the *state machine for
operator intents*, and the *Discord payload builders*. It performs no I/O,
spends nothing, downloads nothing, and calls no provider.

Business logic stays behind the existing boundary:
  * `provider.py`   — actor allowlist, input contract, output validation, provenance
  * `cost.py`       — ceiling, reservation, circuit breaker, reconciliation
  * `orchestrate.py`— dispatch (the ONLY path that spends)
  * `reaction_library.py` — final asset record

Division of responsibility:
  * `approve`  → records authorization ONLY (never spends, never downloads).
  * `acquire`  → the single explicit spend/download intent; requires authorization.
  * `retry`    → explicit, single-shot; never automatic, never a loop.

The card is platform-agnostic where practical: platform, creator, caption, media
facts, cost and library state all arrive as view-model fields, so a second
platform (TikTok/YouTube) needs data, not new rendering code.

Visual language is inherited from the existing operator surface
(`infra/discord-review/source_review.py`): Discord embed, inline field pairs,
outcome colours, button-click custom_ids. Nothing new is invented here.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# States (the five spec'd operator states)
# ---------------------------------------------------------------------------
STATE_REVIEW = "review"
STATE_APPROVED = "approved"
STATE_ACQUIRING = "acquiring"
STATE_ACQUIRED = "acquired"
STATE_FAILED = "failed"

CARD_STATES = (STATE_REVIEW, STATE_APPROVED, STATE_ACQUIRING, STATE_ACQUIRED, STATE_FAILED)

# Decision outcome outside the card's five render states. Deny/Hold decisions are
# rendered by `source_review.decided_embed` (existing pattern — reused, not
# reinvented); the card only needs to know the lane is no longer active.
STATE_DENIED = "denied"

# ---------------------------------------------------------------------------
# State palette (Discord embed colour)
# ---------------------------------------------------------------------------
STATE_COLORS: dict[str, int] = {
    STATE_REVIEW: 0x3498DB,     # blue  — awaiting human decision
    STATE_APPROVED: 0x2ECC71,   # green — authorized, not yet spent
    STATE_ACQUIRING: 0xF1C40F,  # amber — in flight
    STATE_ACQUIRED: 0x1ABC9C,   # teal  — asset in the Reaction Library
    STATE_FAILED: 0xE74C3C,     # red   — failed, retry is manual
}

STATE_TITLES: dict[str, str] = {
    STATE_REVIEW: "🔎 ACQUISITION · REVIEW",
    STATE_APPROVED: "✅ ACQUISITION · APPROVED",
    STATE_ACQUIRING: "⏳ ACQUISITION · IN PROGRESS",
    STATE_ACQUIRED: "🎬 ACQUISITION · ACQUIRED",
    STATE_FAILED: "⚠️ ACQUISITION · FAILED",
}

STATE_STATUS: dict[str, str] = {
    STATE_REVIEW: "Awaiting your decision — nothing has been spent or downloaded.",
    STATE_APPROVED: "Authorized. Acquisition is a separate, explicit action.",
    STATE_ACQUIRING: "Provider run in flight — buttons locked until it resolves.",
    STATE_ACQUIRED: "Stored in the Reaction Library — proven and hashed.",
    STATE_FAILED: "Acquisition failed. Retry is manual and separately authorized.",
}

# ---------------------------------------------------------------------------
# Platform labels (badge) — generic with a friendly default
# ---------------------------------------------------------------------------
PLATFORM_BADGES: dict[str, str] = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "reddit": "Reddit",
    "twitch": "Twitch",
    "kick": "Kick",
}


def platform_badge(platform: str) -> str:
    p = (platform or "").strip().lower()
    return PLATFORM_BADGES.get(p, p.title() or "Unknown")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
ACT_APPROVE = "approve"
ACT_DENY = "deny"
ACT_HOLD = "hold"
ACT_ACQUIRE = "acquire"
ACT_PLAY = "play"
ACT_LIBRARY = "library"
ACT_RETRY = "retry"
ACT_ERROR = "error"
ACT_SOURCE = "source"

ACTION_CODES: dict[str, str] = {
    ACT_APPROVE: "ap", ACT_DENY: "dn", ACT_HOLD: "hd", ACT_ACQUIRE: "aq",
    ACT_PLAY: "py", ACT_LIBRARY: "lb", ACT_RETRY: "rt", ACT_ERROR: "er",
}
CODE_BY_ACTION: dict[str, str] = {v: k for k, v in ACTION_CODES.items()}

# Discord button styles.
STYLE_PRIMARY = 1     # warning-intent actions (retry)
STYLE_SECONDARY = 2   # neutral (hold / library / view error)
STYLE_SUCCESS = 3     # positive (approve / acquire / preview)
STYLE_DANGER = 4      # destructive (deny)
STYLE_LINK = 5        # navigation only — cannot mutate state

# Discord limits.
_EMBED_FIELD_VALUE_MAX = 1024
_EMBED_FIELD_NAME_MAX = 256
_EMBED_FOOTER_MAX = 2048
_BUTTON_LABEL_MAX = 80
_CUSTOM_ID_MAX = 100

CUSTOM_ID_PREFIX = "AQ"
_REF_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# (action, label, style) per state. Positive=approve/acquire/preview,
# neutral=hold/library/view-error, destructive=deny, warning=retry.
STATE_BUTTONS: dict[str, tuple[tuple[str, str, int], ...]] = {
    STATE_REVIEW: (
        (ACT_APPROVE, "Approve", STYLE_SUCCESS),
        (ACT_DENY, "Deny", STYLE_DANGER),
        (ACT_HOLD, "Hold", STYLE_SECONDARY),
    ),
    STATE_APPROVED: (
        (ACT_ACQUIRE, "Acquire for Reaction", STYLE_SUCCESS),
        (ACT_HOLD, "Hold", STYLE_SECONDARY),
        (ACT_DENY, "Deny", STYLE_DANGER),
    ),
    # Acquiring: interactive actions locked. Rendered disabled so the operator
    # sees the lane is busy without being able to re-trigger anything.
    STATE_ACQUIRING: (
        (ACT_ACQUIRE, "Acquiring…", STYLE_SECONDARY),
        (ACT_HOLD, "Hold", STYLE_SECONDARY),
    ),
    STATE_ACQUIRED: (
        (ACT_PLAY, "Preview / Play", STYLE_SUCCESS),
        (ACT_LIBRARY, "Open in Reaction Library", STYLE_SECONDARY),
        (ACT_HOLD, "Hold", STYLE_SECONDARY),
    ),
    STATE_FAILED: (
        (ACT_RETRY, "⚠️ Retry Manually", STYLE_PRIMARY),
        (ACT_HOLD, "Hold", STYLE_SECONDARY),
        (ACT_ERROR, "View Error", STYLE_SECONDARY),
    ),
}

# States in which interactive buttons must be disabled.
LOCKED_STATES = frozenset({STATE_ACQUIRING})

# Actions that spend money or download media: acquire, and an explicit manual
# retry (a retry is a NEW paid attempt — never automatic, never a loop).
SPEND_ACTIONS = frozenset({ACT_ACQUIRE, ACT_RETRY})

SPEND_FREE_ACTIONS = frozenset({
    ACT_APPROVE, ACT_DENY, ACT_HOLD, ACT_PLAY, ACT_LIBRARY, ACT_SOURCE, ACT_ERROR,
})


def actions_for_state(state: str) -> tuple[str, ...]:
    """Interactive actions available in a state (link actions excluded)."""
    return tuple(a for (a, _l, _s) in STATE_BUTTONS.get(state, ()))


def requires_spend(action: str) -> bool:
    return action in SPEND_ACTIONS


def requires_authorization(action: str) -> bool:
    """Only the explicit spend/download intent needs an authorization record."""
    return action in SPEND_ACTIONS


def is_safe_without_spend(action: str) -> bool:
    return action in SPEND_FREE_ACTIONS


# ---------------------------------------------------------------------------
# custom_id encoding  →  AQ:<code>:<ref>
# ---------------------------------------------------------------------------
class CardIdError(ValueError):
    pass


def make_custom_id(action: str, ref: str) -> str:
    code = ACTION_CODES.get(action)
    if not code:
        raise CardIdError(f"unknown action {action!r}")
    if not _REF_RE.match(ref or ""):
        raise CardIdError(f"unsafe ref {ref!r}")
    cid = f"{CUSTOM_ID_PREFIX}:{code}:{ref}"
    if len(cid) > _CUSTOM_ID_MAX:
        raise CardIdError("custom_id too long")
    return cid


def parse_custom_id(custom_id: str) -> dict[str, str] | None:
    parts = (custom_id or "").split(":")
    if len(parts) != 3 or parts[0] != CUSTOM_ID_PREFIX:
        return None
    code, ref = parts[1], parts[2]
    if code not in CODE_BY_ACTION or not _REF_RE.match(ref):
        return None
    return {"action": CODE_BY_ACTION[code], "ref": ref}


# ---------------------------------------------------------------------------
# Guards — duplicate clicks, double jobs, illegal transitions
# ---------------------------------------------------------------------------
GUARD_OK = "ok"
GUARD_DUPLICATE = "duplicate_click"
GUARD_IN_FLIGHT = "in_flight"
GUARD_ALREADY_ACQUIRED = "already_acquired"
GUARD_NOT_ALLOWED = "not_allowed"

DUPLICATE_WINDOW_SECONDS = 20


def guard_action(state: str, action: str, *, ref: str = "",
                 last_action: str = "", last_ref: str = "",
                 last_action_at: float | None = None, now: float = 0.0,
                 in_flight: bool = False) -> str:
    """Pure pre-flight check for an operator click.

    Prevents: illegal transitions, duplicate clicks inside a short window,
    re-entry while a run is in flight, and a second job for an acquired asset.
    """
    # Spend-specific guards first so the operator gets the informative signal
    # (already acquired / in flight) rather than a generic rejection.
    if action == ACT_ACQUIRE:
        if state == STATE_ACQUIRED:
            return GUARD_ALREADY_ACQUIRED
        if state == STATE_ACQUIRING or in_flight:
            return GUARD_IN_FLIGHT
    if action not in actions_for_state(state):
        return GUARD_NOT_ALLOWED
    if (last_action == action and last_ref == ref
            and last_action_at is not None
            and (now - last_action_at) < DUPLICATE_WINDOW_SECONDS):
        return GUARD_DUPLICATE
    return GUARD_OK


def next_state(state: str, action: str, *, succeeded: bool = True) -> str:
    """State transition for a *guarded* action. ``deny`` leaves the card lane."""
    if action == ACT_APPROVE and state == STATE_REVIEW:
        return STATE_APPROVED
    if action == ACT_DENY:
        return STATE_DENIED
    if action == ACT_ACQUIRE and state == STATE_APPROVED:
        return STATE_ACQUIRING
    if action == ACT_RETRY and state == STATE_FAILED:
        return STATE_ACQUIRING
    if state == STATE_ACQUIRING and action in (ACT_ACQUIRE, ACT_RETRY):
        return STATE_ACQUIRED if succeeded else STATE_FAILED
    return state


ACKNOWLEDGEMENTS: dict[tuple[str, str], str] = {
    (ACT_APPROVE, STATE_APPROVED): "Approved — no spend, no download. Acquire to authorize the run.",
    (ACT_HOLD, STATE_REVIEW): "Held — nothing spent, nothing downloaded.",
    (ACT_ACQUIRE, STATE_ACQUIRING): "Acquisition started — one URL, one item. Buttons locked until it resolves.",
    (ACT_RETRY, STATE_ACQUIRING): "Retry authorized — one attempt, no automatic loop.",
    (ACT_LIBRARY, STATE_ACQUIRED): "Opening the Reaction Library record.",
}


def acknowledgement(action: str, state: str) -> str:
    return ACKNOWLEDGEMENTS.get((action, state), "Recorded.")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "unknown"
    s = max(0, int(float(seconds)))
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return "unknown"
    mb = float(n) / (1024 * 1024)
    return f"{mb:.1f} MB" if mb >= 1 else f"{float(n) / 1024:.0f} KB"


def fmt_micro(micro: int | None) -> str:
    if micro is None:
        return "unknown"
    return f"${micro / 1_000_000:.4f}"


def aspect_label(width: int | None, height: int | None) -> str:
    if not width or not height:
        return "unknown"
    if height > width:
        ratio = height / width
        return "9:16 vertical" if abs(ratio - 16 / 9) < 0.35 else "vertical"
    if width > height:
        return "16:9 landscape" if abs(width / height - 16 / 9) < 0.35 else "landscape"
    return "square"


def fmt_resolution(width: int | None, height: int | None) -> str:
    if not width or not height:
        return "unknown"
    return f"{width}×{height} · {aspect_label(width, height)}"


# ---------------------------------------------------------------------------
# Provenance hygiene — never surface a signed/credentialed URL
# ---------------------------------------------------------------------------
_UNSAFE_URL_MARKERS = ("sig=", "oh=", "token=", "signature=", "x-amz-", "key=")
_SAFE_URL_RE = re.compile(r"^https://[\w.-]+(/[^\s?#]*)?$")
# Discord's own attachment reference (e.g. attachment://poster.jpg). It refers to
# a file the bot attached to the message, so it cannot carry a credential.
_ATTACHMENT_RE = re.compile(r"^attachment://[\w.-]{1,64}$")


_YOUTUBE_WATCH_RE = re.compile(r"^https://(?:www\.)?youtube\.com/watch$")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def sanitize_display_url(url: str) -> str:
    """Strip query/fragment and reject URLs carrying signature parameters.

    Discord attachment references are allowed through: they name a file on the
    message itself and cannot encode a credential.
    """
    u = (url or "").strip()
    if not u:
        return ""
    if _ATTACHMENT_RE.match(u):
        return u
    low = u.lower()
    if any(m in low for m in _UNSAFE_URL_MARKERS):
        return ""
    import urllib.parse as _up
    head, _, query = u.split("#", 1)[0].partition("?")
    # Identity-preserving exception: for a YouTube WATCH url the video id IS the
    # query. Only ?v=<11-char id> is kept — every other parameter is dropped, so
    # no credential-bearing value can survive this path.
    if _YOUTUBE_WATCH_RE.match(head):
        for k, v in _up.parse_qsl(query, keep_blank_values=True):
            if k == "v" and _VIDEO_ID_RE.match(v):
                return f"{head}?v={v}"
        return ""
    return head if _SAFE_URL_RE.match(head) else ""


_OPERATOR_ALIASES = {"passportpetty": "Bryan", "bryan": "Bryan", "bpetty": "Bryan",
                     "gateway-owner": "Bryan"}


def operator_label(actor: str) -> str:
    """Operator display name — never a raw Discord username#discriminator or snowflake.

    The card shows who authorized an action ("Authorized by Bryan"); the raw
    Discord identity is an internal detail and stays out of the operator surface.
    A bare numeric id is never rendered as if it were a name.
    """
    name = str(actor or "").split("#", 1)[0].strip()
    if not name or name.isdigit():
        return "unknown"
    return _OPERATOR_ALIASES.get(name.lower(), name)


def provenance_line(vm: dict[str, Any]) -> str:
    """Compact provenance: content id + adapter + who authorized. No URLs, no secrets."""
    prov = vm.get("provenance") or {}
    bits = []
    if vm.get("content_id"):
        bits.append(f"ID `{vm['content_id']}`")
    adapter = str(prov.get("adapter") or "")
    if adapter:
        bits.append(f"`{adapter.replace('apify:', 'apify/')}`")
    actor = str(prov.get("actor") or "")
    if actor and not actor.startswith("apify"):
        bits.append(f"Authorized by {operator_label(actor)}")
    if prov.get("run_id"):
        bits.append(f"run `{prov['run_id']}`")
    return " · ".join(bits)[:300] or "n/a"


# ---------------------------------------------------------------------------
# View model
# ---------------------------------------------------------------------------
def build_view_model(
    *,
    ref: str,
    platform: str,
    state: str,
    content_id: str = "",
    canonical_url: str = "",
    creator: str = "",
    caption: str = "",
    duration_seconds: float | None = None,
    width: int | None = None,
    height: int | None = None,
    size_bytes: int | None = None,
    video_codec: str = "",
    audio_codec: str = "",
    has_audio: bool | None = None,
    estimated_cost_micro: int | None = None,
    actual_cost_micro: int | None = None,
    ceiling_micro: int | None = None,
    remaining_acquisitions: int | None = None,
    library: dict[str, Any] | None = None,
    preview: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    tenant_id: str = "",
    profile_id: str = "",
    fixture: bool = False,
    library_url: str = "",
    local_acquisition: bool = False,
) -> dict[str, Any]:
    """Assemble the presentation view model (no I/O, no validation of media)."""
    if state not in CARD_STATES:
        raise ValueError(f"unsupported card state {state!r}")
    if not _REF_RE.match(ref or ""):
        raise ValueError(f"unsafe ref {ref!r}")
    return {
        "ref": ref,
        "state": state,
        "platform": (platform or "").lower(),
        "platform_label": platform_badge(platform),
        "content_id": content_id,
        "canonical_url": sanitize_display_url(canonical_url),
        "creator": creator,
        "caption": caption,
        "duration_seconds": duration_seconds,
        "width": width,
        "height": height,
        "size_bytes": size_bytes,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "has_audio": has_audio,
        "estimated_cost_micro": estimated_cost_micro,
        "actual_cost_micro": actual_cost_micro,
        "ceiling_micro": ceiling_micro,
        "remaining_acquisitions": remaining_acquisitions,
        "library": library or {},
        "preview": preview or {},
        "provenance": provenance or {},
        "error": error or {},
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "fixture": bool(fixture),
        "library_url": library_url,
        "local_acquisition": bool(local_acquisition),
    }


def media_summary(vm: dict[str, Any]) -> str:
    """One-line media facts: duration · resolution · size · audio."""
    parts = [fmt_duration(vm.get("duration_seconds"))]
    res = fmt_resolution(vm.get("width"), vm.get("height"))
    if res != "unknown":
        parts.append(res)
    parts.append(fmt_bytes(vm.get("size_bytes")))
    if vm.get("has_audio") is True:
        codec = (vm.get("audio_codec") or "").upper()
        parts.append(f"Audio present{' (' + codec + ')' if codec else ''}")
    elif vm.get("has_audio") is False:
        parts.append("No audio")
    else:
        parts.append("Audio unknown")
    return " · ".join(parts)


def cost_line(vm: dict[str, Any]) -> str:
    if vm.get("local_acquisition"):
        return "Local acquisition · $0 provider cost"
    """Estimated cost before spending; actual cost after.

    A failed run is reported honestly: the estimate is shown, but "not yet
    spent" is never claimed because a failed attempt may still have been billed.
    """
    est = vm.get("estimated_cost_micro")
    actual = vm.get("actual_cost_micro")
    ceiling = vm.get("ceiling_micro")
    left = vm.get("remaining_acquisitions")
    if actual is not None:
        line = f"Actual **{fmt_micro(actual)}**"
        if est is not None:
            line += f" · est. {fmt_micro(est)}"
    elif vm.get("state") == STATE_FAILED:
        line = (f"Est. **{fmt_micro(est)}** · actual not reconciled"
                if est is not None else "Actual not reconciled — check the provider run")
    elif est is not None:
        line = f"Est. **{fmt_micro(est)}** (not yet spent)"
    else:
        line = "Cost unknown — authorization required"
    tail = []
    if ceiling is not None:
        tail.append(f"ceiling {fmt_micro(ceiling)}")
    if left is not None:
        tail.append(f"{left} acquisitions left")
    return line + ("\n" + " · ".join(tail) if tail else "")


def library_line(vm: dict[str, Any]) -> str:
    lib = vm.get("library") or {}
    if vm.get("state") == STATE_ACQUIRED and lib:
        bits = ["✅ Acquired"]
        if lib.get("asset_filename"):
            bits.append(f"`{lib['asset_filename']}`")
        if lib.get("sha256"):
            bits.append(f"sha256 `{str(lib['sha256'])[:12]}…`")
        return " · ".join(bits)[:300]
    if lib.get("status"):
        return str(lib["status"])[:300]
    return "Not acquired"


def preview_field(vm: dict[str, Any]) -> str:
    """Honest preview affordance — never claims playback that isn't available."""
    pv = vm.get("preview") or {}
    kind = pv.get("kind") or "none"
    if kind == "video_url":
        return "Inline preview available."
    if kind == "video_attachment":
        return "Playable in this message — native Discord player."
    if kind == "thumbnail":
        if str(pv.get("thumbnail_url") or "").startswith("attachment://"):
            return "Poster frame attached to this message."
        return "Source thumbnail (pre-acquisition)."
    if kind == "local_asset":
        name = pv.get("local_filename") or "local asset"
        reason = str(pv.get("attach_reason") or "")
        if reason:
            return (f"Video not attached — {reason}\n"
                    f"Playable locally in the Reaction Library · `{name}`")
        return (f"Playable locally · `{name}`\n"
                "Discord cannot embed a local file — attach or host it to preview in-client.")
    return "No preview yet — open the source link to view it."


# ---------------------------------------------------------------------------
# Discord payload builders
# ---------------------------------------------------------------------------
def _clip(value: str, limit: int) -> str:
    value = value or ""
    return value if len(value) <= limit else value[: limit - 1] + "…"


def format_acquisition_embed(vm: dict[str, Any]) -> dict[str, Any]:
    """Build the Discord embed for a card state (pure)."""
    state = vm["state"]
    fields: list[dict[str, Any]] = []

    if vm.get("creator"):
        fields.append({"name": "Creator", "value": _clip(str(vm["creator"]), _EMBED_FIELD_VALUE_MAX),
                       "inline": True})
    fields.append({"name": "Media", "value": media_summary(vm), "inline": True})
    fields.append({"name": "Cost", "value": cost_line(vm), "inline": True})
    fields.append({"name": "Reaction Library", "value": library_line(vm), "inline": False})

    if state == STATE_ACQUIRING:
        fields.append({"name": "Progress", "value": "Provider run in flight — this card updates in place.",
                       "inline": False})
    if state == STATE_FAILED:
        err = vm.get("error") or {}
        code = str(err.get("code") or "").strip()
        stage = str(err.get("stage") or "").strip()
        message = str(err.get("message") or "Acquisition failed.")
        prefix = " · ".join(b for b in (f"`{code}`" if code else "",
                                         f"at *{stage}*" if stage else "") if b)
        summary = f"{prefix} — {message}" if prefix else message
        fields.append({"name": "Failure", "value": _clip(summary, _EMBED_FIELD_VALUE_MAX),
                       "inline": False})

    fields.append({"name": "Preview", "value": _clip(preview_field(vm), _EMBED_FIELD_VALUE_MAX),
                   "inline": False})
    fields.append({"name": "Provenance", "value": provenance_line(vm), "inline": False})

    embed: dict[str, Any] = {
        "title": STATE_TITLES[state],
        "description": _clip(vm.get("caption") or f"{vm['platform_label']} source", 400),
        "color": STATE_COLORS[state],
        "fields": fields,
    }
    if vm.get("canonical_url"):
        embed["url"] = vm["canonical_url"]

    pv = vm.get("preview") or {}
    hosted = sanitize_display_url(pv.get("hosted_url") or "")
    thumb = sanitize_display_url(pv.get("thumbnail_url") or "")
    if (pv.get("kind") == "video_url") and hosted:
        embed["video"] = {"url": hosted}
    elif (pv.get("kind") == "thumbnail") and thumb:
        embed["thumbnail"] = {"url": thumb}

    footer: list[str] = [f"{vm['platform_label']} · {STATE_STATUS[state]}"]
    if vm.get("tenant_id"):
        footer.append(str(vm["tenant_id"]))
    if vm.get("profile_id"):
        footer.append(str(vm["profile_id"]))
    footer.append("1 URL · 1 item · ≤5 min · ≤100 MiB · breaker $1.00 / 10")
    if vm.get("fixture"):
        footer.append("🧪 FIXTURE — downstream locked")
    embed["footer"] = {"text": _clip(" · ".join(footer), _EMBED_FOOTER_MAX)}
    return embed


_POLICY_REFUSAL_CODES = frozenset({
    "DURATION_LIMIT", "SIZE_LIMIT", "PLAYLIST_REJECTED", "LIVE_REJECTED",
    "MULTIPLE_VIDEOS", "MULTIPLE_RESULTS", "NO_AUDIO", "UNSUPPORTED_VIDEO_CODEC",
    "UNSUPPORTED_AUDIO_CODEC", "BAD_DURATION", "UNKNOWN_DURATION",
    "CONTENT_ID_DRIFT", "SELECTED_PARAM", "SELECTION_PARAM",
})


def _is_policy_refusal(vm: dict[str, Any]) -> bool:
    """Fixed-policy refusals are final: no Retry, only View Error / View Source."""
    err = vm.get("error") or {}
    return (str(err.get("code") or "") in _POLICY_REFUSAL_CODES
            or err.get("retryable") is False)


def _is_valid_http_url(url: str) -> bool:
    """A link button must point somewhere real (localhost http is legitimate here)."""
    u = (url or "").strip()
    return (u.startswith("http://") or u.startswith("https://")) and " " not in u


def format_acquisition_buttons(vm: dict[str, Any], *,
                               disabled: bool | None = None,
                               library_url: str = "") -> list[dict[str, Any]]:
    """State-dependent action row. Acquiring locks every interactive button.

    Acquired cards follow two rules:
      * no Preview / Play button — playback is either the native Discord
        attachment or the library page, so the button would be redundant or dead;
      * Open in Reaction Library is a LINK button (no custom_id, never invokes a
        handler) and is omitted entirely when no real destination exists.
    """
    state = vm["state"]
    locked = state in LOCKED_STATES if disabled is None else disabled
    url = library_url or (vm.get("library_url") or "")
    buttons: list[dict[str, Any]] = []
    for action, label, style in STATE_BUTTONS.get(state, ()):
        if state == STATE_ACQUIRED and action == ACT_PLAY:
            continue
        if state == STATE_FAILED and action == ACT_RETRY and _is_policy_refusal(vm):
            # A fixed policy refusal (e.g. duration > 300 s) is not retryable.
            continue
        if state == STATE_ACQUIRED and action == ACT_LIBRARY:
            if _is_valid_http_url(url):
                buttons.append({"type": 2, "style": STYLE_LINK,
                                "label": _clip(label, _BUTTON_LABEL_MAX), "url": url})
            continue
        buttons.append({
            "type": 2,
            "style": style,
            "label": _clip(label, _BUTTON_LABEL_MAX),
            "custom_id": make_custom_id(action, vm["ref"]),
            "disabled": bool(locked),
        })
    src = vm.get("canonical_url") or ""
    if src and state in (STATE_REVIEW, STATE_APPROVED, STATE_FAILED):
        buttons.append({"type": 2, "style": STYLE_LINK, "label": "View Source", "url": src})
    return [{"type": 1, "components": buttons}]


def format_acquisition_card(vm: dict[str, Any]) -> dict[str, Any]:
    """Full message payload: embed + components (what the adapter sends)."""
    return {
        "embeds": [format_acquisition_embed(vm)],
        "components": format_acquisition_buttons(vm, library_url=vm.get("library_url") or ""),
    }


# ---------------------------------------------------------------------------
# Fixture helper — the already-acquired Reel (no download, no provider call)
# ---------------------------------------------------------------------------
ACQUIRED_REEL_FIXTURE: dict[str, Any] = {
    "ref": "DdZS1QRhrXE",
    "platform": "instagram",
    "content_id": "DdZS1QRhrXE",
    "canonical_url": "https://www.instagram.com/reel/DdZS1QRhrXE/",
    # Creator handle: rendered when the platform safely provides one. The
    # retained Instagram proof record did NOT store a handle, so it is left
    # empty here rather than invented — the card omits unknown fields instead of
    # presenting unverified data as fact.
    "creator": "",
    "caption": "Vertical Instagram Reel — proof asset for the approved acquisition lane.",
    "duration_seconds": 55.260635,
    "width": 720,
    "height": 1280,
    "size_bytes": 23721338,
    "video_codec": "h264",
    "audio_codec": "aac",
    "has_audio": True,
    "estimated_cost_micro": 2600,
    "ceiling_micro": 1_000_000,
    "remaining_acquisitions": 9,
    "library_filename": "instagram__DdZS1QRhrXE__9b22f887b2ff.mp4",
    "sha256": "9b22f887b2ff6a3b78d91529c1d9c3105497af2a4298ba9241394686239ae096",
    "actor": "instagram-reel-scraper",
    "run_id": "0DPOKk2bEwxqVTS0i",
}


def fixture_view_model(state: str, *, local_path: str = "",
                       hosted_url: str = "", thumbnail_url: str = "",
                       poster_url: str = "",
                       error: dict[str, Any] | None = None,
                       fixture: bool = True) -> dict[str, Any]:
    """Build a view model from the retained Reel record for any card state."""
    f = ACQUIRED_REEL_FIXTURE
    acquired = state == STATE_ACQUIRED
    kind = "none"
    if acquired and hosted_url:
        kind = "video_url"
    elif poster_url:
        kind = "thumbnail"
    elif acquired and local_path:
        kind = "local_asset"
    elif thumbnail_url:
        kind = "thumbnail"
    return build_view_model(
        ref=f["ref"],
        platform=f["platform"],
        state=state,
        content_id=f["content_id"],
        canonical_url=f["canonical_url"],
        creator=f["creator"],
        caption=f["caption"],
        duration_seconds=f["duration_seconds"],
        width=f["width"],
        height=f["height"],
        size_bytes=f["size_bytes"],
        video_codec=f["video_codec"],
        audio_codec=f["audio_codec"],
        has_audio=f["has_audio"],
        estimated_cost_micro=f["estimated_cost_micro"],
        actual_cost_micro=f["estimated_cost_micro"] if acquired else None,
        ceiling_micro=f["ceiling_micro"],
        remaining_acquisitions=f["remaining_acquisitions"] - 1 if acquired else f["remaining_acquisitions"],
        library=({"status": "acquired", "asset_filename": f["library_filename"],
                  "sha256": f["sha256"], "path": local_path} if acquired else {}),
        preview={"kind": kind, "local_path": local_path,
                 "local_filename": f["library_filename"] if local_path else "",
                 "hosted_url": hosted_url,
                 "thumbnail_url": poster_url or thumbnail_url},
        provenance={"actor": f["actor"], "run_id": f["run_id"]},
        error=error or {},
        tenant_id="dreemzy",
        profile_id="acquisition-card-proof",
        fixture=fixture,
    )
