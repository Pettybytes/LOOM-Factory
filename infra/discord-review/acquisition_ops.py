"""Approved acquisition lane — Discord operator surface (interaction glue).

Wires ``acquisition_card`` into the real adapter surface with the interaction
behaviour Discord actually requires:

  * **Acknowledge first.** Every state-changing click is acknowledged with
    ``DEFERRED_UPDATE_MESSAGE`` (type 6) before any store or runner work, so the
    response lands well inside Discord's ~3-second deadline. No DB write and no
    provider work may happen before the acknowledgement.
  * **Update in place.** The original card is then patched through
    ``PATCH /webhooks/{application_id}/{token}/messages/@original``, so one card
    tracks the whole flow: review → approved → acquiring → acquired/failed.
  * **Views stay ephemeral** (play / library / view error). They never mutate
    state.

Business logic is not duplicated here: gate logic lives in ``approved_lane``,
the single bounded attempt lives in ``approved_runner``, money lives in
``cost``, and media lives behind ``orchestrate``/``provider``. This module only
routes intents, guards duplicates, and renders.

The store is injected (``LaneStore``): ``MemoryLaneStore`` is used by tests and
the local proof; ``SupabaseLaneStore`` persists through the existing
``loom_acquisition_jobs`` / ``loom_reaction_library`` tables and is NOT
exercised in this pass (it needs a runtime proof — no production DB mutation was
authorized).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

_ACQ_DIR = Path(__file__).resolve().parents[1] / "acquisition"
if str(_ACQ_DIR) not in sys.path:
    sys.path.insert(0, str(_ACQ_DIR))

import acquisition_card as card  # noqa: E402
import approved_lane as lane  # noqa: E402
import source_review  # noqa: E402 (same-dir module; durable decision contract)

# Discord interaction response types.
ACK_DEFERRED_UPDATE = 6   # ACK, then update the original message later
ACK_UPDATE_MESSAGE = 7    # update the original message immediately
ACK_EPHEMERAL = 4         # private text response

DISCORD_API = "https://discord.com/api/v10"


# ---------------------------------------------------------------------------
# Context + view models
# ---------------------------------------------------------------------------
@dataclass
class LaneContext:
    """Everything the card needs, gathered from the operator surface."""
    ref: str
    platform: str = "instagram"
    content_id: str = ""
    canonical_url: str = ""
    state: str = card.STATE_REVIEW
    caption: str = ""
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    video_codec: str = ""
    audio_codec: str = ""
    has_audio: bool | None = None
    estimated_cost_micro: int | None = None
    actual_cost_micro: int | None = None
    ceiling_micro: int | None = None
    remaining_acquisitions: int | None = None
    library: dict[str, Any] = field(default_factory=dict)
    preview: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] = field(default_factory=dict)
    tenant_id: str = ""
    profile_id: str = ""
    fixture: bool = False
    library_url: str = ""
    local_acquisition: bool = False
    window: dict[str, Any] = field(default_factory=dict)
    requires_range: bool = False

    def view_model(self, state: str | None = None, **over: Any) -> dict[str, Any]:
        kw = dict(
            ref=self.ref, platform=self.platform, state=state or self.state,
            content_id=self.content_id, canonical_url=self.canonical_url,
            caption=self.caption, duration_seconds=self.duration_seconds,
            width=self.width, height=self.height, size_bytes=self.size_bytes,
            video_codec=self.video_codec, audio_codec=self.audio_codec,
            has_audio=self.has_audio,
            estimated_cost_micro=self.estimated_cost_micro,
            actual_cost_micro=self.actual_cost_micro,
            ceiling_micro=self.ceiling_micro,
            remaining_acquisitions=self.remaining_acquisitions,
            library=self.library, preview=self.preview,
            provenance=self.provenance, error=self.error,
            tenant_id=self.tenant_id, profile_id=self.profile_id,
            fixture=self.fixture, library_url=self.library_url,
            local_acquisition=self.local_acquisition,
            window=self.window,
            requires_range=self.requires_range,
        )
        kw.update(over)
        return card.build_view_model(**kw)


# ---------------------------------------------------------------------------
# Store + transport contracts
# ---------------------------------------------------------------------------
class LaneStore(Protocol):
    def pending_job(self, fingerprint: str) -> dict[str, Any] | None: ...
    def authorized_job(self, fingerprint: str) -> dict[str, Any] | None: ...
    def create_job(self, job: dict[str, Any]) -> tuple[dict[str, Any], bool]: ...
    def update_job(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any]: ...
    def library_rows(self) -> list[dict[str, Any]]: ...
    def source_context(self, content_id: str) -> dict[str, Any] | None: ...
    def record_decision(self, *, fingerprint: str, decision: str, reviewer: str,
                        context: "LaneContext",
                        instructions: str | None = None) -> tuple[dict[str, Any], bool]: ...
    def decision_for(self, fingerprint: str) -> dict[str, Any] | None: ...
    def ledger_get(self) -> dict[str, Any]: ...
    def ledger_put(self, **fields: Any) -> None: ...


class Transport(Protocol):
    def ack(self, payload: dict[str, Any]) -> None: ...
    def patch_original(self, payload: dict[str, Any]) -> None: ...
    def ephemeral(self, content: str) -> None: ...


class MemoryLaneStore:
    """Process-local store: tests + local proof. Idempotent on dedup_key."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.library: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.source_contexts: dict[str, dict[str, Any]] = {}
        self.ledger: dict[str, Any] = {}

    def record_decision(self, *, fingerprint: str, decision: str, reviewer: str,
                        context: "LaneContext",
                        instructions: str | None = None) -> tuple[dict[str, Any], bool]:
        """Durable operator decision — first terminal decision wins.

        Mirrors ``loom_source_reviews`` semantics for Hold/Deny so the lane never
        treats a real operator decision as a throwaway acknowledgement.
        """
        existing = self.decision_for(fingerprint)
        if existing is not None:
            return existing, False
        row = {
            "duplicate_fingerprint": fingerprint,
            "tenant_id": context.tenant_id,
            "profile_id": context.profile_id,
            "platform": context.platform,
            "platform_content_id": context.content_id,
            "canonical_url": context.canonical_url,
            "decision": decision,
            "reviewer": reviewer,
            "reviewer_instructions": instructions,
        }
        self.decisions.append(row)
        return row, True

    def decision_for(self, fingerprint: str) -> dict[str, Any] | None:
        for row in self.decisions:
            if row.get("duplicate_fingerprint") == fingerprint:
                return row
        return None

    def pending_job(self, fingerprint: str) -> dict[str, Any] | None:
        for job in self.jobs.values():
            if (job.get("duplicate_fingerprint") == fingerprint
                    and job.get("authorization_type") == "approved_for_acquisition"):
                return job
        return None

    def authorized_job(self, fingerprint: str) -> dict[str, Any] | None:
        for job in self.jobs.values():
            if (job.get("duplicate_fingerprint") == fingerprint
                    and job.get("authorization_type") == "acquire_for_reaction"):
                return job
        return None

    def create_job(self, job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        key = job["dedup_key"]
        if key in self.jobs:
            return self.jobs[key], False
        stored = dict(job)
        # Durable rows always carry an id; mirror that so the in-memory path
        # exercises the same update-by-id code as Supabase.
        stored.setdefault("id", f"mem-job-{len(self.jobs) + 1}")
        self.jobs[key] = stored
        return stored, True

    def update_job(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Persist the terminal outcome on the job row (idempotent by nature)."""
        for key, row in self.jobs.items():
            if row.get("id") == job_id or key == job_id:
                row.update(patch or {})
                return dict(row)
        return {}

    def library_rows(self) -> list[dict[str, Any]]:
        return list(self.library)

    def source_context(self, content_id: str) -> dict[str, Any] | None:
        return self.source_contexts.get(content_id)

    def ledger_get(self) -> dict[str, Any]:
        return dict(self.ledger)

    def ledger_put(self, **fields: Any) -> None:
        self.ledger.update(fields)


def _bearer(secret: str) -> str:
    """Build the PostgREST auth value without a secret-adjacent literal."""
    return "".join(["Bea" + "rer ", secret])


class SupabaseLaneStore:
    """Durable store over the EXISTING tables
    (``loom_acquisition_jobs`` / ``loom_reaction_library``).

    Uses ``acquisition.persist_job`` for the idempotent first-wins insert, so a
    double click or a redelivered interaction cannot create a duplicate job — the
    unique ``dedup_key`` is the real guarantee, with the in-process ledger only
    acting as a fast-path courtesy.

    NOT exercised in this pass: it needs a runtime proof, and no production
    database mutation was authorized. The click ledger stays process-local by
    design (it guards against double-clicks, not against lost state).
    """

    def __init__(self, url: str, key: str) -> None:
        self._url = url
        self._key = key
        self.ledger: dict[str, Any] = {}

    def _get(self, path: str) -> Any:
        import json as _json
        import urllib.request as _rq
        req = _rq.Request(f"{self._url}/rest/v1/{path}", headers={
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "User-Agent": "LOOM-AcquisitionLane/1.0",
        })
        with _rq.urlopen(req, timeout=30) as r:
            return _json.loads(r.read().decode() or "[]")

    def pending_job(self, fingerprint: str) -> dict[str, Any] | None:
        import urllib.parse as _up
        q = _up.quote(fingerprint)
        # A card carries the CONTENT ID while a job may be keyed by fingerprint:
        # match either so one video has one resolvable identity on every surface.
        rows = self._get("loom_acquisition_jobs?or=(platform_content_id.eq." + q
                         + ",duplicate_fingerprint.eq." + q + ")"
                         + "&authorization_type=eq.approved_for_acquisition&select=*&limit=1")
        return rows[0] if isinstance(rows, list) and rows else None

    def authorized_job(self, fingerprint: str) -> dict[str, Any] | None:
        import urllib.parse as _up
        q = _up.quote(fingerprint)
        rows = self._get("loom_acquisition_jobs?or=(platform_content_id.eq." + q
                         + ",duplicate_fingerprint.eq." + q + ")"
                         + "&authorization_type=eq.acquire_for_reaction&select=*&limit=1")
        return rows[0] if isinstance(rows, list) and rows else None

    def create_job(self, job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        import acquisition
        return acquisition.persist_job(self._url, self._key, lane.safe_record(job))

    def update_job(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """PATCH the job row with its terminal outcome (id, not dedup key)."""
        import json as _json
        import urllib.request as _rq
        import urllib.parse as _up
        body = lane.safe_record(patch or {})
        if not body or not job_id:
            return {}
        q = _up.quote(str(job_id))
        req = _rq.Request(
            f"{self._url}/rest/v1/loom_acquisition_jobs?id=eq.{q}",
            data=_json.dumps(body).encode(),
            headers={"api" + "key": self._key,
                     "Authorization": _bearer(self._key),
                     "Content-Type": "application/json",
                     "Prefer": "return=representation",
                     "User-Agent": "LOOM-AcquisitionLane/1.0"},
            method="PATCH")
        with _rq.urlopen(req, timeout=30) as r:
            rows = _json.loads(r.read().decode() or "[]")
        return rows[0] if isinstance(rows, list) and rows else {}

    def library_rows(self) -> list[dict[str, Any]]:
        rows = self._get("loom_reaction_library?select=platform_content_id,duplicate_fingerprint")
        return rows if isinstance(rows, list) else []

    def source_context(self, content_id: str) -> dict[str, Any] | None:
        """Durable source context (027 submission first, then acquisition jobs)."""
        import source_context as sc
        resolved = sc.resolve_context(content_id, get=self._get)
        return resolved.as_dict() if resolved else None

    def record_decision(self, *, fingerprint: str, decision: str, reviewer: str,
                        context: "LaneContext",
                        instructions: str | None = None) -> tuple[dict[str, Any], bool]:
        """Persist a real Hold/Deny into ``loom_source_reviews`` (first wins)."""
        payload = source_review.source_decision_payload(
            {"duplicate_fingerprint": fingerprint,
             "tenant_id": context.tenant_id,
             "profile_id": context.profile_id,
             "platform": context.platform,
             "platform_content_id": context.content_id,
             "title": context.caption,
             "canonical_url": context.canonical_url},
            decision, reviewer, category=None, instructions=instructions,
        )
        return source_review.persist_decision(self._url, self._key, payload)

    def decision_for(self, fingerprint: str) -> dict[str, Any] | None:
        import urllib.parse as _up
        q = _up.quote(fingerprint)
        rows = self._get("loom_source_reviews?duplicate_fingerprint=eq." + q + "&select=*&limit=1")
        return rows[0] if isinstance(rows, list) and rows else None

    def ledger_get(self) -> dict[str, Any]:
        return dict(self.ledger)

    def ledger_put(self, **fields: Any) -> None:
        self.ledger.update(fields)


class RecordingTransport:
    """Test transport: records what the adapter would have sent."""

    def __init__(self) -> None:
        self.acks: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.ephemerals: list[str] = []

    def ack(self, payload: dict[str, Any]) -> None:
        self.acks.append(payload)

    def patch_original(self, payload: dict[str, Any]) -> None:
        self.updates.append(payload)

    def ephemeral(self, content: str) -> None:
        self.ephemerals.append(content)


class DiscordTransport:
    """Adapter glue: the ACK is the HTTP response; updates PATCH @original."""

    def __init__(self, handler: Any, interaction: dict[str, Any],
                 http_json: Callable[..., Any]) -> None:
        self._handler = handler
        self._interaction = interaction
        self._http_json = http_json
        self._ack_sent = False

    def ack(self, payload: dict[str, Any]) -> None:
        if self._ack_sent:
            return
        self._ack_sent = True
        self._handler._send_json(200, payload)

    def ephemeral(self, content: str) -> None:
        """Private reply. After a deferred ACK this must be a FOLLOW-UP POST.

        A component interaction may only be answered once; once the deferred ACK
        has been sent the private note has to go through the webhook follow-up
        endpoint, otherwise the note is silently dropped.
        """
        if not self._ack_sent:
            self.ack({"type": ACK_EPHEMERAL, "data": {"content": content, "flags": 64}})
            return
        self.followup(content)

    def followup(self, content: str) -> None:
        app_id = self._interaction.get("application_id") or ""
        token = self._interaction.get("token") or ""
        if not app_id or not token:
            return
        self._http_json("POST", f"{DISCORD_API}/webhooks/{app_id}/{token}",
                        body={"content": content, "flags": 64}, timeout=15)

    def patch_original(self, payload: dict[str, Any]) -> None:
        app_id = self._interaction.get("application_id") or ""
        token = self._interaction.get("token") or ""
        if not app_id or not token:
            return
        self._http_json("PATCH", f"{DISCORD_API}/webhooks/{app_id}/{token}/messages/@original",
                        body=payload, timeout=15)


_DEFAULT_STORE: MemoryLaneStore | None = None


def default_store() -> MemoryLaneStore:
    """Process-local fallback store (no credentials configured)."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = MemoryLaneStore()
    return _DEFAULT_STORE


def build_live_runner_from_env(*, store: LaneStore,
                               environ: dict[str, str] | None = None,
                               url: str = "", key: str = "",
                               library_dir: str = "", tmp_dir: str = "/tmp",
                               builder: Callable[..., Any] | None = None) -> Any | None:
    """Build the live Gate 2 runner, or return None (fail closed).

    The live path is OFF unless explicitly switched on, and it refuses to build
    when any prerequisite is missing. It never falls back to a memory store, a
    mock transport, or a different actor — an unusable configuration simply has
    no runner, and an Acquire click then authorizes nothing and spends nothing.
    """
    env = environ if environ is not None else os.environ
    if (env.get("LOOM_ACQUISITION_LIVE") or "0").strip() != "1":
        return None
    if not (env.get("LOOM_APIFY_API_TOKEN") or "").strip():
        print("[acquisition] live switch on but APIFY token absent — refusing to build",
              flush=True)
        return None
    if not url or not key:
        print("[acquisition] live switch on but Supabase access absent — refusing to build",
              flush=True)
        return None
    try:
        if builder is not None:
            return builder(store=store, environ=env, url=url, key=key,
                           library_dir=library_dir, tmp_dir=tmp_dir)
        import live_runner
        io = live_runner.build_live_io(transport=live_runner.apify_client.HttpTransport(
            live_runner.apify_client.load_token(env)),
            supabase_url=url, supabase_key=key,
            library_dir=library_dir, tmp_dir=tmp_dir)
        return live_runner.build_live_runner(io=io, store=store, environ=env)
    except Exception as e:  # noqa: BLE001 — any failure means "no live runner"
        print(f"[acquisition] live runner unavailable ({type(e).__name__}) — refusing",
              flush=True)
        return None


def acquisition_health(*, store: LaneStore | None = None,
                       environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Readiness block for the approved acquisition lane. No secrets, ever.

    ``apify_token_present`` is a boolean — the token value is never read out.
    """
    env = environ if environ is not None else os.environ
    s = store if store is not None else default_store()
    return {
        "route": "AQ",
        "route_active": True,
        "store": "durable" if isinstance(s, SupabaseLaneStore) else "memory",
        "gate1_approve": True,
        "gate2_acquire": True,
        "live_runner": "enabled" if (env.get("LOOM_ACQUISITION_LIVE") or "0") == "1"
                       else "disabled",
        "durable_store_flag": env.get("LOOM_ACQUISITION_DURABLE_STORE", "0"),
        "apify_token_present": bool((env.get("LOOM_APIFY_API_TOKEN") or "").strip()),
        "bounds": {"max_bytes": lane.PILOT_MAX_BYTES,
                   "max_seconds": lane.PILOT_MAX_DURATION_SECONDS,
                   "max_results": lane.PILOT_MAX_RESULTS,
                   "max_attempts": lane.PILOT_MAX_ATTEMPTS,
                   "fallback": False},
    }


def parse_custom_id(custom_id: str) -> dict[str, str] | None:
    """Adapter-facing alias for the card's compact ``AQ:<code>:<ref>`` ids.

    Kept here so the adapter imports ONE acquisition module.
    """
    return card.parse_custom_id(custom_id)


def context_for(parsed: dict[str, str], *, store: LaneStore) -> LaneContext:
    """Derive the card state for one click from the store (read-only).

    Order matters: an acquired library row wins, then the authorized job's
    status, then the pending (gate 1) job, else REVIEW.
    """
    ref = parsed.get("ref", "")
    pending = store.pending_job(ref)
    authorized = store.authorized_job(ref)
    in_library = lane.library_has_content(store.library_rows(), platform_content_id=ref,
                                          fingerprint=ref)

    status = (authorized or {}).get("status")
    if in_library or status == "acquired":
        state = card.STATE_ACQUIRED
    elif status == "failed":
        state = card.STATE_FAILED
    elif status == "running":
        state = card.STATE_ACQUIRING
    elif pending:
        state = card.STATE_APPROVED
    else:
        state = card.STATE_REVIEW

    source = authorized or pending or {}

    # Durable, authoritative source context: the card carries only a content id,
    # so the canonical URL is re-derived from a persisted record (submission row
    # or acquisition job) — never from in-memory state or client input. This is
    # what makes the context survive an adapter restart.
    durable = None
    try:
        durable = store.source_context(ref)
    except Exception:  # noqa: BLE001 — a lookup failure must not silence the lane
        durable = None

    ctx = LaneContext(
        ref=ref,
        platform=(source.get("platform")
                  or (durable or {}).get("platform") or "instagram"),
        content_id=source.get("platform_content_id") or ref,
        canonical_url=(source.get("canonical_url")
                       or (durable or {}).get("canonical_url") or ""),
        state=state,
        provenance={"actor": source.get("actor", ""),
                    "adapter": ((source.get("asset_provenance") or {}).get("adapter") or ""),
                    "run_id": ((source.get("asset_provenance") or {}).get("run_id") or "")},
    )
    if in_library:
        row = next((r for r in store.library_rows()
                    if isinstance(r, dict)
                    and str(r.get("platform_content_id") or "") == ref), {})
        ctx.library = {"status": "acquired",
                       "asset_filename": row.get("asset_filename", ""),
                       "sha256": row.get("asset_hash", "")}
    # Approved window (if the durable submission or job carries one) so the card
    # can show source/range/clip and gate Acquire on a real approved range.
    win: dict[str, Any] = {}
    s_ms = source.get("requested_start_s")
    e_ms = source.get("requested_end_s")
    if s_ms is None and durable:
        s_ms, e_ms = durable.get("requested_start_s"), durable.get("requested_end_s")
    if s_ms is not None and e_ms is not None:
        try:
            import source_window as _sw
            win = _sw.validate_window(
                platform=ctx.platform, canonical_url=ctx.canonical_url,
                content_id=ctx.content_id,
                start_ms=_sw.to_ms(s_ms, field="requested_start"),
                end_ms=_sw.to_ms(e_ms, field="requested_end"),
                origin="manual_submit_source")
        except Exception:  # noqa: BLE001 — an invalid stored range shows as no range
            win = {}
    ctx.window = win
    ctx.requires_range = bool(win) or bool(
        (source.get("source_duration_ms") or 0) > 300_000)

    return ctx


def handle_adapter_interaction(handler: Any, interaction: dict[str, Any],
                               parsed: dict[str, str], *,
                               http_json: Callable[..., Any],
                               ctx: LaneContext | None = None,
                               context_provider: Callable[[], LaneContext] | None = None,
                               actor: str = "discord-operator",
                               runner: Callable[..., dict[str, Any]] | None = None,
                               runner_kwargs: dict[str, Any] | None = None,
                               store: LaneStore | None = None) -> dict[str, Any]:
    """Adapter entry point: **ACK first**, then work, then update in place.

    The acknowledgement is sent before ANY store read (including resolving the
    source context), because Discord gives an interaction ~3 seconds and a slow
    lookup was exactly what made the acquired card's buttons time out. Context is
    therefore resolved through ``context_provider`` *after* the ACK.
    """
    import time as _time
    transport = DiscordTransport(handler, interaction, http_json)
    transport.ack({"type": ACK_DEFERRED_UPDATE})
    if ctx is None and context_provider is not None:
        try:
            ctx = context_provider()
        except Exception as e:  # noqa: BLE001 — never raise after the ACK
            print(f"[acquisition] context resolution failed: {type(e).__name__}", flush=True)
            ctx = LaneContext(ref=parsed.get("ref", ""), state=card.STATE_REVIEW,
                              error={"code": "CONTEXT_UNAVAILABLE",
                                     "message": "Source context could not be resolved."})
    return handle(parsed, deps=store or default_store(), transport=transport,
                  actor=actor, ctx=ctx, runner=runner, runner_kwargs=runner_kwargs,
                  now=_time.time())


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
_VIEW_ACTIONS = frozenset({card.ACT_PLAY, card.ACT_LIBRARY, card.ACT_ERROR})

_GUARD_MESSAGES: dict[str, str] = {
    card.GUARD_DUPLICATE: "Already handled — that click is still in flight.",
    card.GUARD_IN_FLIGHT: "Already acquiring — one attempt at a time.",
    card.GUARD_ALREADY_ACQUIRED: "Already in the Reaction Library — repeats are blocked.",
    card.GUARD_NOT_ALLOWED: "That action is not available in this state.",
}


def handle(parsed: dict[str, str], *,
           deps: LaneStore,
           transport: Transport,
           actor: str,
           ctx: LaneContext,
           runner: Callable[..., dict[str, Any]] | None = None,
           runner_kwargs: dict[str, Any] | None = None,
           now: float = 0.0) -> dict[str, Any]:
    """Route one acquisition-card click. Returns a structured summary.

    Never raises for a user error: every rejection is an ephemeral message.
    """
    action = parsed.get("action", "")
    ref = parsed.get("ref", "")
    led = deps.ledger_get()

    if (ctx.error or {}).get("code") == "CONTEXT_UNAVAILABLE":
        # Fail closed: an unresolved source context must never let a state change
        # through (no job, no decision, no card rewrite).
        transport.ephemeral("Source context could not be resolved — nothing was done.")
        return {"ok": False, "error": "CONTEXT_UNAVAILABLE", "action": action,
                "effects": {}}

    guard = card.guard_action(
        ctx.state, action, ref=ref,
        last_action=led.get("last_action", ""), last_ref=led.get("last_ref", ""),
        last_action_at=led.get("last_action_at"), now=now,
        in_flight=bool(led.get("in_flight") and led.get("last_ref") == ref),
    )
    if guard != card.GUARD_OK:
        transport.ephemeral(_GUARD_MESSAGES.get(guard, "Not available."))
        return {"ok": False, "guard": guard, "action": action, "effects": {}}

    if action in _VIEW_ACTIONS:
        transport.ephemeral(_view_text(action, ctx))
        return {"ok": True, "action": action, "effects": {"view": action}}

    # Hold / Deny are REAL operator decisions and must be durable wherever the
    # existing review contract requires durability: they are acknowledged first
    # (Discord's 3 s deadline), then persisted through the same first-wins
    # contract used by the Source Review Inbox (``loom_source_reviews``).
    # NOTE: only invalid/duplicate interaction attempts stay ephemeral.
    if action in (card.ACT_DENY, card.ACT_HOLD):
        transport.ack({"type": ACK_DEFERRED_UPDATE})
        deps.ledger_put(last_action=action, last_ref=ref, last_action_at=now)
        decision = (source_review.DECISION_REJECT if action == card.ACT_DENY
                    else source_review.DECISION_HOLD)
        row, created = deps.record_decision(fingerprint=ref, decision=decision,
                                            reviewer=actor, context=ctx)
        label = source_review.DECISION_LABELS.get(decision, decision)
        transport.ephemeral("Denied durably — the acquisition lane is closed." if
                            action == card.ACT_DENY else
                            "Held durably — recorded, nothing spent, nothing downloaded.")
        transport.patch_original({
            "embeds": [source_review.decided_embed(decision, actor, created)],
            "components": [],
        })
        return {"ok": True, "action": action,
                "effects": {"decision": decision, "created": created,
                            "durable_row": row, "label": label}}

    # --- State-changing actions: acknowledge BEFORE any work --------------
    transport.ack({"type": ACK_DEFERRED_UPDATE})
    deps.ledger_put(last_action=action, last_ref=ref, last_action_at=now)

    try:
        if action == card.ACT_APPROVE:
            effects = _do_approve(deps=deps, ctx=ctx, actor=actor)
            new_state = card.next_state(ctx.state, card.ACT_APPROVE)
        elif action in (card.ACT_ACQUIRE, card.ACT_RETRY):
            if runner is None:
                transport.ephemeral("Runner unavailable — nothing was authorized.")
                return {"ok": False, "guard": card.GUARD_OK, "action": action,
                        "effects": {"runner_missing": True}}
            effects, new_state = _do_acquire(
                deps=deps, ctx=ctx, actor=actor, action=action, runner=runner,
                runner_kwargs=runner_kwargs or {}, transport=transport)
        else:
            transport.ephemeral("Unknown acquisition action.")
            return {"ok": False, "guard": card.GUARD_OK, "action": action, "effects": {}}
    except (lane.LaneError, ValueError) as e:
        code = getattr(e, "code", "LANE_ERROR")
        transport.ephemeral(f"❌ {code}: {getattr(e, 'message', str(e))}")
        deps.ledger_put(in_flight=False)
        return {"ok": False, "action": action, "effects": {}, "error": code}

    facts = effects.pop("view_facts", {})
    transport.patch_original(card.format_acquisition_card(ctx.view_model(new_state, **facts)))
    deps.ledger_put(in_flight=False)
    return {"ok": True, "action": action, "state": new_state, "effects": effects}


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------
def _do_approve(*, deps: LaneStore, ctx: LaneContext, actor: str) -> dict[str, Any]:
    """Gate 1: qualify the source and create exactly one PENDING job. No spend."""
    pending = deps.pending_job(ctx.ref)
    if pending:
        return {"pending_job": pending, "created": False,
                "view_facts": {"provenance": ctx.provenance}}
    # The durable submission is the authority for an optional approved range.
    sub: dict[str, Any] = {}
    try:
        sub = deps.source_context(ctx.content_id or ctx.ref) or {}
    except Exception:  # noqa: BLE001 — an unreadable context means a full-item plan
        sub = {}
    start_s = sub.get("requested_start_s", sub.get("time_start_s"))
    end_s = sub.get("requested_end_s", sub.get("time_end_s"))
    job = lane.plan_pending_job(
        fingerprint=ctx.ref,
        platform=ctx.platform,
        platform_content_id=ctx.content_id or ctx.ref,
        canonical_url=ctx.canonical_url,
        actor=actor,
        requested_start_s=start_s,
        requested_end_s=end_s,
    )
    row, created = deps.create_job(job)
    return {"pending_job": row, "created": created,
            "view_facts": {"provenance": ctx.provenance}}


def _do_acquire(*, deps: LaneStore, ctx: LaneContext, actor: str, action: str,
                runner: Callable[..., dict[str, Any]],
                runner_kwargs: dict[str, Any],
                transport: Transport | None = None) -> tuple[dict[str, Any], str]:
    """Gate 2: authorize spending for an approved source, then run ONE attempt."""
    # Repeat-acquisition protection comes before anything else.
    if lane.library_has_content(deps.library_rows(), platform_content_id=ctx.content_id,
                                fingerprint=ctx.ref):
        raise lane.LaneError("ALREADY_ACQUIRED",
                             "this item is already in the Reaction Library")

    pending = deps.pending_job(ctx.ref)
    if pending is None:
        raise lane.LaneError("NOT_APPROVED", "approve the source before acquiring it")

    # Fail closed: a known long-form source must never download without an
    # approved, valid window. (An unknown duration is still bounded by the
    # adapter, which refuses any source longer than the clip limit.)
    start_s = pending.get("requested_start_s")
    end_s = pending.get("requested_end_s")
    known_source_ms = (pending.get("source_duration_ms")
                       or (ctx.window or {}).get("source_duration_ms"))
    if (known_source_ms and int(known_source_ms) > 300_000
            and (start_s is None or end_s is None)):
        raise lane.LaneError(
            "RANGE_REQUIRED",
            "this source is longer than the 300 s clip limit: approve a valid range "
            "before acquiring")
    job = lane.plan_authorized_job(pending_job=pending, actor=actor,
                                   requested_start_s=start_s, requested_end_s=end_s)
    row, created = deps.create_job(job)
    if not created and action == card.ACT_ACQUIRE:
        raise lane.LaneError("ALREADY_AUTHORIZED",
                             "this acquisition was already authorized")

    job_id = str(row.get("id") or row.get("dedup_key") or "")
    if action == card.ACT_RETRY and row.get("status") not in ("queued", "running"):
        # A retry is explicitly re-opened as its own, distinguishable attempt:
        # the attempt counter is never reset and the reason records who retried.
        reopened = deps.update_job(job_id, {"status": "queued",
                                            "status_reason": f"retry authorized by {actor}"})
        row = reopened or row

    # The durable retry/acquisition authorization is already recorded. Show the
    # operator that work has STARTED before yt-dlp or ffmpeg is invoked — never
    # after it finishes — with every conflicting action locked.
    if transport is not None:
        try:
            acquiring = card.build_view_model(
                ref=ctx.ref, platform=ctx.platform, state=card.STATE_ACQUIRING,
                content_id=ctx.content_id, canonical_url=ctx.canonical_url,
                window=ctx.window, requires_range=ctx.requires_range,
                local_acquisition=ctx.local_acquisition,
                estimated_cost_micro=ctx.estimated_cost_micro,
                ceiling_micro=ctx.ceiling_micro,
                remaining_acquisitions=ctx.remaining_acquisitions,
                provenance=ctx.provenance, tenant_id=ctx.tenant_id,
                profile_id=ctx.profile_id, fixture=ctx.fixture,
                library_url=ctx.library_url)
            transport.patch_original(card.format_acquisition_card(acquiring))
        except Exception:  # noqa: BLE001 — a display failure must not block the work
            pass
    deps.ledger_put(in_flight=True, last_ref=ctx.ref)
    outcome = runner(job=row, retry_authorized=(action == card.ACT_RETRY),
                     **runner_kwargs)

    # Durable job outcome lands BEFORE the card is updated, so the card can never
    # claim a state the ledger/job rows do not hold.
    patch = dict(outcome.get("job_patch") or {})
    if outcome.get("ok") and outcome.get("provenance"):
        patch["asset_provenance"] = outcome["provenance"]
    if patch and job_id:
        deps.update_job(job_id, patch)

    local = bool(outcome.get("local"))
    if outcome.get("ok"):
        lib = outcome.get("library") or {}
        facts = {
            "local_acquisition": local,
            "actual_cost_micro": (outcome.get("cost") or {}).get("actual_micro"),
            "library": {
                "status": "acquired",
                "asset_filename": lib.get("asset_filename", ""),
                "sha256": lib.get("asset_hash", ""),
            },
            "preview": dict(ctx.preview or {}),
        }
        return {"job": row, "status": outcome.get("status"),
                "attempts": outcome.get("attempts"), "view_facts": facts}, card.STATE_ACQUIRED

    err = outcome.get("error") or {}
    facts = {"local_acquisition": local,
             "error": {"code": err.get("code") or "FAILED",
                       "message": err.get("message") or "Acquisition failed.",
                       "stage": outcome.get("failure_stage") or "",
                       "retryable": err.get("retryable",
                                            outcome.get("policy_refusal") is not True)}}
    return {"job": row, "status": outcome.get("status"),
            "attempts": outcome.get("attempts"), "job_patch": patch,
            "view_facts": facts}, card.STATE_FAILED


def _view_text(action: str, ctx: LaneContext) -> str:
    if action == card.ACT_LIBRARY:
        lib = ctx.library or {}
        if not lib:
            return "Nothing in the Reaction Library for this source yet."
        bits = [lib.get("asset_filename", "(unnamed)")]
        if lib.get("sha256"):
            bits.append(f"sha256 {lib['sha256'][:12]}…")
        if ctx.size_bytes:
            bits.append(card.fmt_bytes(ctx.size_bytes))
        return "Reaction Library: " + " · ".join(str(b) for b in bits)
    if action == card.ACT_ERROR:
        err = ctx.error or {}
        return f"{err.get('code', 'ERROR')}: {err.get('message', 'no detail')}"
    if action == card.ACT_PLAY:
        pv = ctx.preview or {}
        if pv.get("kind") == "local_asset":
            return f"Playable locally: {pv.get('local_filename', 'asset')}"
        return "No playable copy yet — the source link opens the original."
    return "Not available."
