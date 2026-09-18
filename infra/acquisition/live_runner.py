"""Live local-Mac acquisition runner — the Gate 2 spend path (wiring).

This is the ONLY component that may perform a real paid acquisition. It is built
so that everything except the two genuinely external calls (the Apify HTTP
transport and the local disk/ffmpeg work) is injected, which lets the entire
fail-closed matrix be proven with fakes.

Enforced by construction, in this order, BEFORE anything can spend:

  1. GATE 1 present        — an approved (pending) job must exist for the source
  2. GATE 2 present        — the job must carry acquire_for_reaction authorization
  3. URL / content id      — unchanged from what was qualified and approved
  4. already acquired      — the Reaction Library must not already hold it
  5. in flight / dispatched— no second attempt, no second provider run
  6. token present         — APIFY token is configured (presence only, never read out)
  7. actor allowlisted     — pinned adapter, active; a fallback can never be used
  8. platform-proven       — only a runtime-proven platform may be dispatched
  9. circuit breaker       — ceiling + max-acquisitions checked before the call
 10. budget reservation    — worst-case cost reserved before the call

Then ONE bounded attempt through the existing ``orchestrate.dispatch`` path:
one exact URL, one item, ≤100 MiB, ≤5 min, one attempt, no retry, no fallback,
with local download → ffprobe validation → SHA-256 → atomic finalize →
Reaction Library record, and provenance persisted without signed URLs.

Nothing here is executed against the live provider by this pass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

import apify_client
import approved_lane as lane
import approved_runner as runner
import cost
import ledger as spend_ledger
import provider
import reaction_library

CONTENT_TYPE_MP4 = "video/mp4"


class LiveRunnerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Injected I/O
# ---------------------------------------------------------------------------
@dataclass
class LiveIO:
    """Every external effect the runner needs. Faked in tests."""
    transport: Any
    download_one_video: Callable[..., tuple]
    run_ffprobe: Callable[[str], dict]
    parse_ffprobe_result: Callable[..., dict]
    checksum_sha256: Callable[[str], str]
    finalize: Callable[[str, str], str]
    cleanup_temp: Callable[[str], None]
    persist_library: Callable[[dict], dict]
    library_rows: Callable[[], list[dict[str, Any]]]
    pending_job: Callable[[str], dict[str, Any] | None]
    ceiling: Callable[[], dict[str, Any]]
    record_run: Callable[[dict[str, Any]], Any] | None = None
    update_ceiling: Callable[[dict[str, Any]], Any] | None = None
    existing_run: Callable[[str], bool] | None = None      # legacy boolean (injected fakes)
    provider_runs_for_job: Callable[[str], list[dict[str, Any]]] | None = None
    ledger: Any | None = None              # DurableLedger (required to spend)
    known_failure_charge_micro: int | None = None
    tmp_dir: str = "/tmp"
    library_dir: str = ""
    download_timeout: float = 60.0
    forbid_media_without_audio: bool = True
    required_video_codec: str | None = None


# ---------------------------------------------------------------------------
# Preflight — fail closed
# ---------------------------------------------------------------------------
def preflight(job: dict[str, Any] | None, *, io: LiveIO,
              environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Run every fail-closed gate and return the dispatch bundle."""
    if not job:
        raise LiveRunnerError("NO_JOB", "no acquisition job")

    platform = job.get("platform") or ""

    # --- 2. Gate 2 authorization (also rejects a Gate 1-only job) ---------
    lane.require_spend_authorization(job)

    # --- 8. Platform must be runtime-proven --------------------------------
    lane.require_runtime_proven(job)

    # --- 1. Gate 1 must exist, and must be the job's own correlation -------
    fingerprint = job.get("duplicate_fingerprint") or ""
    pending = io.pending_job(fingerprint)
    if not pending:
        raise LiveRunnerError("NO_PENDING_JOB",
                              "no approved (Gate 1) job for this source")
    if job.get("correlation_id") and pending.get("dedup_key") \
            and job["correlation_id"] != pending["dedup_key"]:
        raise LiveRunnerError("GATE1_MISMATCH",
                              "Gate 2 authorization does not reference this Gate 1 job")

    # --- 3. The URL and content id must be unchanged ----------------------
    job_url = _strip(job.get("canonical_url") or "")
    pending_url = _strip(pending.get("canonical_url") or "")
    if not job_url:
        raise LiveRunnerError("NO_URL", "authorized job carries no canonical URL")
    if job_url != pending_url:
        raise LiveRunnerError("URL_CHANGED",
                              "the canonical URL differs from the approved source")
    content_id = (job.get("platform_content_id") or "").strip()
    if not content_id or content_id != (pending.get("platform_content_id") or "").strip():
        raise LiveRunnerError("CONTENT_ID_CHANGED",
                              "the platform content id differs from the approved source")

    # --- 7. Actor allowlist (pinned, active; a fallback is never used) -----
    actor = lane.default_provider_actor(platform)
    try:
        provider.actor_active(platform, actor)
    except provider.ProviderError as e:
        raise LiveRunnerError(e.code, e.message) from e

    # --- 4. Already acquired ---------------------------------------------
    if lane.library_has_content(io.library_rows(), platform_content_id=content_id,
                                fingerprint=fingerprint):
        raise LiveRunnerError("ALREADY_ACQUIRED",
                              "this item is already in the Reaction Library")

    # --- 5. In flight / already dispatched — ATTEMPT-SCOPED --------------
    # The lookup returns THIS job's rows only, and each row is classified: a
    # terminal failure whose charge is already settled must not block a NEW
    # explicitly authorized attempt.
    if job.get("status") == "running":
        raise LiveRunnerError("IN_FLIGHT", "an acquisition attempt is already in flight")
    for row in _prior_attempt_rows(io, job.get("id") or ""):
        status = str(row.get("status") or "")
        if status == spend_ledger.STATUS_RESERVED:
            raise LiveRunnerError("ATTEMPT_IN_FLIGHT",
                                  "a provider attempt for this job is still in flight")
        if status == spend_ledger.STATUS_ACQUIRED:
            raise LiveRunnerError("ALREADY_ACQUIRED",
                                  "this job has already acquired its asset")
        if status == spend_ledger.STATUS_FAILED_UNKNOWN:
            raise LiveRunnerError("UNRESOLVED_COST",
                                  "a previous attempt's cost is unresolved; reconcile first")
        # STATUS_FAILED_KNOWN: terminal with a settled charge — does not block.

    # --- 6. Provider token present (presence only) -----------------------
    env = environ if environ is not None else os.environ
    if not apify_client.token_present(env):
        raise LiveRunnerError("TOKEN_MISSING",
                              f"{apify_client.TOKEN_ENV} is not configured")

    # --- 9. Circuit breaker, before any provider call --------------------
    ceiling = io.ceiling() or {}
    lifetime_spent = int(ceiling.get("lifetime_spent_micro") or 0)
    succeeded = int(ceiling.get("acquisitions_succeeded") or 0)
    ceiling_micro = int(ceiling.get("ceiling_micro") or cost.DEFAULT_CEILING_MICRO)
    max_success = int(ceiling.get("max_acquisitions") or cost.DEFAULT_MAX_SUCCESS)
    if ceiling.get("disabled"):
        raise LiveRunnerError("BREAKER_DISABLED", "the acquisition breaker is disabled")
    state = cost.check_circuit(lifetime_spent, succeeded,
                               ceiling_micro=ceiling_micro, max_success=max_success)
    if state != "ok":
        raise LiveRunnerError(state, "circuit breaker refuses dispatch")

    # --- Bounds (clamped down, never widened) ----------------------------
    bounds = lane.enforce_pilot_bounds(job)
    effective = dict(job)
    effective.update(bounds["patch"])
    effective["canonical_url"] = job_url

    # --- 10. Budget reservation (worst case) -----------------------------
    estimate = _estimate_micro(platform)
    try:
        reserved = cost.reserve_worst_case(estimate, True, lifetime_spent,
                                           ceiling_micro=ceiling_micro)
    except cost.CostError as e:
        raise LiveRunnerError(e.code, e.message) from e

    return {
        "job": effective,
        "actor": actor,
        "canonical_url": job_url,
        "content_id": content_id,
        "fingerprint": fingerprint,
        "lifetime_spent_micro": lifetime_spent,
        "acquisitions_succeeded": succeeded,
        "ceiling_micro": ceiling_micro,
        "max_success": max_success,
        "estimate_micro": estimate,
        "reserved_micro": reserved,
        "bounds_clamped": bounds["clamped"],
    }


def _prior_attempt_rows(io: LiveIO, job_id: str) -> list[dict[str, Any]]:
    """Provider-run rows for THIS job only (attempt-scoped), never another job's.

    A lookup failure fails closed: unverifiable prior state must not be guessed at.
    """
    if not job_id:
        return []
    if io.provider_runs_for_job is not None:
        try:
            rows = io.provider_runs_for_job(job_id) or []
        except Exception as e:  # noqa: BLE001
            raise LiveRunnerError("RUN_LOOKUP_FAILED",
                                  f"could not verify prior attempts ({type(e).__name__})") from e
        return [r for r in rows if isinstance(r, dict)
                and str(r.get("acquisition_job_id") or job_id) == job_id]
    if io.existing_run and io.existing_run(job_id):
        # Legacy injected boolean: a blocking run is treated as an active attempt.
        return [{"status": spend_ledger.STATUS_RESERVED, "acquisition_job_id": job_id}]
    return []


def _strip(url: str) -> str:
    return (url or "").split("#", 1)[0].split("?", 1)[0].strip()


def _estimate_micro(platform: str) -> int:
    """Pinned per-result cost for the proven adapter (exact, not a guess)."""
    if platform == "instagram":
        return cost.REEL_SCRAPER_RESULT_MICRO
    raise LiveRunnerError("NO_ESTIMATE", f"no pinned cost estimate for platform {platform!r}")


# ---------------------------------------------------------------------------
# Bounded download + validation
# ---------------------------------------------------------------------------
def build_bounded_downloader(io: LiveIO, *, max_bytes: int, max_duration: float) -> Callable:
    def download(url: str) -> dict[str, Any]:
        tmp_path = ""
        try:
            tmp_path, written, http_status, content_type = io.download_one_video(
                url, io.tmp_dir, max_bytes=max_bytes, timeout=io.download_timeout)
            meta = io.parse_ffprobe_result(io.run_ffprobe(tmp_path),
                                           actual_size_bytes=written,
                                           max_bytes=max_bytes,
                                           max_duration=max_duration)
            if not meta.get("video_codec"):
                raise LiveRunnerError("MEDIA_INVALID", "no decodable video codec")
            if io.forbid_media_without_audio and not meta.get("has_audio"):
                raise LiveRunnerError("AUDIO_MISSING", "downloaded media has no audio stream")
            if io.required_video_codec and meta.get("video_codec") != io.required_video_codec:
                raise LiveRunnerError("CODEC_MISMATCH",
                                      "video codec does not match the required codec")
            digest = io.checksum_sha256(tmp_path)
            if not digest or len(digest) != 64:
                raise LiveRunnerError("BAD_HASH", "checksum failed")
        except Exception:
            if tmp_path:
                io.cleanup_temp(tmp_path)
            raise
        return {
            "path": tmp_path,
            "size_bytes": written,
            "digest": digest,
            "http_status": http_status,
            "content_type": content_type or CONTENT_TYPE_MP4,
            "duration_seconds": meta.get("duration_seconds"),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "frame_rate": meta.get("frame_rate"),
            "has_audio": meta.get("has_audio"),
            "video_codec": meta.get("video_codec"),
        }
    return download


# ---------------------------------------------------------------------------
# Reaction Library finalization
# ---------------------------------------------------------------------------
def build_finalizer(io: LiveIO, *, pre: dict[str, Any]) -> Callable:
    def finalize_asset(meta: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
        job = pre["job"]
        filename = reaction_library.filename_for(pre["job"]["platform"],
                                                 pre["content_id"], meta["digest"])
        final_path = os.path.join(io.library_dir, filename) if io.library_dir else filename
        io.finalize(meta["path"], final_path)
        record = reaction_library.plan_library_entry(
            duplicate_fingerprint=pre["fingerprint"] or pre["content_id"],
            platform=job.get("platform") or "",
            platform_content_id=pre["content_id"],
            canonical_url=pre["canonical_url"],
            acquisition_job_id=str(job.get("id") or job.get("dedup_key") or ""),
            filename=filename,
            digest=meta["digest"],
            size_bytes=meta["size_bytes"],
            duration_seconds=meta.get("duration_seconds"),
            content_type=CONTENT_TYPE_MP4,
            storage_url="",                       # local Mac; no hosted URL yet
            rights_usage_note=job.get("rights_usage_note", ""),
        )
        safe = lane.safe_record(record)
        persisted = io.persist_library(safe) or safe
        return {"asset_filename": filename, "asset_hash": meta["digest"],
                "size_bytes": meta["size_bytes"],
                "duration_seconds": meta.get("duration_seconds"),
                "path": final_path, "library_id": (persisted or {}).get("id", "")}
    return finalize_asset


# ---------------------------------------------------------------------------
# Runner assembly
# ---------------------------------------------------------------------------
class _RunIdCapture:
    """Transport wrapper for the provider boundary (no behaviour change otherwise).

    Does two things:
      * translates the pinned, human-readable actor name to the **numeric REST
        actor id** — the API rejects a slug containing ``/`` (that produced the
        live ``404`` at the Actor *start* endpoint);
      * remembers the provider run id so the audit row can be keyed on it.

    The readable slug is still what the allowlist checks, the provenance record
    and the ledger ``actor_id`` use — only the wire call is translated.
    """

    def __init__(self, transport: Any, sink: dict[str, str]) -> None:
        self._transport = transport
        self._sink = sink

    def start_run(self, actor: str, input_: dict) -> dict:
        run = self._transport.start_run(provider.actor_api_id(actor), input_)
        # Tolerate both the enveloped ({"data": {...}}) and bare shapes so the run
        # id is captured whatever the transport returns.
        source = run
        if isinstance(run, dict) and isinstance(run.get("data"), dict):
            source = run["data"]
        if isinstance(source, dict):
            self._sink["run_id"] = str(source.get("id") or "")
            self._sink["dataset_id"] = str(source.get("defaultDatasetId") or "")
        return run

    def get_dataset_items(self, dataset_id: str) -> list:
        return self._transport.get_dataset_items(dataset_id)


def _now_iso() -> str:
    """UTC timestamp for the durable job patch (the job table has no trigger)."""
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())


def _reason_text(value: Any) -> str:
    """Normalize ANY failure shape to a short, loggable string.

    Handles dicts (``{"code", "message"}``), bare strings, integer HTTP codes,
    exceptions and ``None`` — the shape that previously broke settlement was an
    integer code reaching a slice.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        parts = [value.get("code"), value.get("message")]
        return ": ".join(str(p) for p in parts if p not in (None, ""))[:200]
    return str(value)[:200]


def _refusal(code: str, message: str = "") -> dict[str, Any]:
    return {"ok": False, "status": "failed", "attempts": 0,
            "error": {"code": code, "message": message}}


def build_live_runner(*, io: LiveIO, store: Any,
                      environ: dict[str, str] | None = None,
                      dispatch: Callable[..., dict[str, Any]] | None = None,
                      approve_lookup: Callable[[str], dict[str, Any] | None] | None = None,
                      ) -> Callable[..., dict[str, Any]]:
    """Return the callable the operator surface invokes for Acquire/Retry."""
    import orchestrate

    dispatch_fn = dispatch or orchestrate.dispatch

    def run(*, job: dict[str, Any] | None = None, retry_authorized: bool = False,
            **kwargs: Any) -> dict[str, Any]:
        try:
            pre = preflight(job, io=io, environ=environ)
        except (LiveRunnerError, lane.LaneError, runner.RunnerError) as e:
            return {"ok": False, "status": "failed", "attempts": 0,
                    "error": {"code": getattr(e, "code", "FAILED"),
                              "message": getattr(e, "message", str(e))}}
        downloader = build_bounded_downloader(io, max_bytes=pre["job"]["max_bytes"],
                                              max_duration=pre["job"]["max_duration_seconds"])
        finalizer = build_finalizer(io, pre=pre)

        # --- Durable reservation BEFORE the provider call ------------------
        # No ledger, no spend: an unaccounted acquisition is never attempted.
        if io.ledger is None:
            return _refusal("LEDGER_UNAVAILABLE",
                            "durable spend ledger not configured; refusing to spend")
        # Attempts are checked BEFORE any reservation, so a refusal can never hold
        # budget or leave an audit trail for a run that was never permitted.
        if not lane.attempts_allowed(pre["job"], retry_authorized=retry_authorized):
            return _refusal("ATTEMPTS_EXHAUSTED",
                            "one attempt only; a retry needs a separate explicit "
                            "authorization")
        job_id = str(pre["job"].get("id") or "")
        try:
            io.ledger.reserve(amount_micro=pre["estimate_micro"], job_id=job_id)
        except Exception as e:  # noqa: BLE001 — breaker/CAS refusals are final
            return _refusal(getattr(e, "code", "LEDGER_ERROR"),
                            getattr(e, "message", str(e)))

        capture: dict[str, str] = {}
        fallback_run_id = (f"{pre['job'].get('dedup_key') or job_id or pre['content_id']}"
                           f":{int(pre['job'].get('retry_count') or 0) + 1}")

        try:
            outcome = runner.run_approved_job(
                job=pre["job"],
                lifetime_spent_micro=pre["lifetime_spent_micro"],
                acquisitions_succeeded=pre["acquisitions_succeeded"],
                already_dispatched=False,
                retry_authorized=retry_authorized,
                transport=_RunIdCapture(io.transport, capture),
                downloader=downloader,
                finalizer=finalizer,
                dispatch=dispatch_fn,
                estimate_micro=pre["estimate_micro"],
                is_exact=True,
            )
        except (runner.RunnerError, lane.LaneError) as e:
            # The reservation is NOT restored: the provider may already have billed.
            self_rec = _settle(io, capture, fallback_run_id, pre, job_id,
                               ok=False, actual=None,
                               reason=getattr(e, "code", "FAILED"))
            out = _refusal(getattr(e, "code", "FAILED"), getattr(e, "message", str(e)))
            out["ledger"] = self_rec
            return out
        except Exception as e:  # noqa: BLE001 — unknown failure is reconciled as unknown
            self_rec = _settle(io, capture, fallback_run_id, pre, job_id,
                               ok=False, actual=None, reason=type(e).__name__)
            out = _refusal("RUNNER_ERROR", type(e).__name__)
            out["ledger"] = self_rec
            return out

        # --- Record + reconcile (exactly once) ----------------------------
        ok = bool(outcome.get("ok"))
        # Evidence-based failure stage: no provider run id means the Actor START
        # call is where the attempt died (a dataset fetch needs a run first).
        if not ok and not capture.get("run_id"):
            outcome["failure_stage"] = "actor_start"
        elif not ok:
            outcome["failure_stage"] = "provider_run"
        actual = ((outcome.get("cost") or {}).get("actual_micro") if ok
                  else io.known_failure_charge_micro)
        outcome["ledger"] = _settle(io, capture, fallback_run_id, pre, job_id,
                                   ok=ok, actual=actual,
                                   reason=_reason_text(outcome.get("error")))
        patch = dict(outcome.get("job_patch") or {})
        stage = outcome.get("failure_stage") or ""
        patch["updated_at"] = _now_iso()
        if stage:
            patch["status_reason"] = f"{stage}: {patch.get('status_reason') or ''}".strip()
            patch["asset_provenance"] = {"adapter": provider.adapter_name(pre["actor"]),
                                         "provider": provider.PROVIDER_APIFY,
                                         "stage": stage,
                                         "run_id": capture.get("run_id") or ""}
        if retry_authorized:
            # A retry is its own explicit authorization; it stays visible in the
            # durable reason so an attempt can never be mistaken for a first run.
            patch["status_reason"] = (f"retry-authorized · "
                                      f"{patch.get('status_reason') or ''}").strip(" ·")
        outcome["job_patch"] = patch
        return outcome

    run.preflight = preflight  # type: ignore[attr-defined]
    return run


def _settle(io: LiveIO, capture: dict[str, str], fallback_run_id: str,
            pre: dict[str, Any], job_id: str, *, ok: bool,
            actual: int | None, reason: Any) -> dict[str, Any]:
    """Record the provider run and reconcile the ledger, exactly once.

    Runs for success AND every failure class (HTTP error, integer code, timeout,
    exception, malformed response). A settlement failure is **never silent**: it
    is logged without secrets and surfaced on the returned record.
    """
    reason_text = _reason_text(reason)[:200]
    run_id = capture.get("run_id") or fallback_run_id
    try:
        io.ledger.record_run(run_id=run_id, actor_id=pre["actor"], job_id=job_id,
                             estimate_micro=pre["estimate_micro"], output_ref=reason_text)
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", type(e).__name__)
        print(f"[acquisition] settlement: audit row NOT written ({code})", flush=True)
        return {"applied": False, "settlement_failed": True, "error": code,
                "run_id": run_id}
    try:
        result = io.ledger.reconcile(run_id=run_id, actual_micro=actual,
                                     succeeded=ok, status_reason=reason_text)
        result.setdefault("settlement_failed", False)
        result["run_id"] = run_id
        return result
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", type(e).__name__)
        print(f"[acquisition] settlement: reconciliation NOT applied ({code})", flush=True)
        return {"applied": False, "settlement_failed": True, "error": code,
                "run_id": run_id}


def build_live_io(*, transport: Any, supabase_url: str, supabase_key: str,
                  library_dir: str, tmp_dir: str) -> LiveIO:
    """Real I/O bundle for the local Mac.

    NOTE: assembling this does not call the provider. It is only reached when the
    live switch is explicitly enabled, and the provider call happens solely on an
    authorized Gate 2 acquisition.
    """
    import sys
    from pathlib import Path
    helper_dir = Path(__file__).resolve().parents[1] / "local-helper"
    if str(helper_dir) not in sys.path:
        sys.path.insert(0, str(helper_dir))
    import acquire as acq  # local-helper
    import ledger as ledger_mod

    def _get(path: str):
        import json as _json
        import urllib.request as _rq
        req = _rq.Request(f"{supabase_url}/rest/v1/{path}", headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "User-Agent": "LOOM-LiveRunner/1.0",
        })
        with _rq.urlopen(req, timeout=30) as r:
            return _json.loads(r.read().decode() or "[]")

    def _post(table: str, body: dict) -> dict:
        import json as _json
        import urllib.request as _rq
        req = _rq.Request(f"{supabase_url}/rest/v1/{table}", data=_json.dumps(body).encode(),
                          headers={"apikey": supabase_key,
                                   "Authorization": f"Bearer {supabase_key}",
                                   "Content-Type": "application/json",
                                   "Prefer": "return=representation"},
                          method="POST")
        with _rq.urlopen(req, timeout=30) as r:
            rows = _json.loads(r.read().decode() or "[]")
        return rows[0] if isinstance(rows, list) and rows else {}

    return LiveIO(
        transport=apify_client.HttpTransport(apify_client.load_token()),
        download_one_video=acq.download_one_video,
        run_ffprobe=acq.run_ffprobe,
        parse_ffprobe_result=acq.parse_ffprobe_result,
        checksum_sha256=acq.checksum_sha256,
        finalize=acq.finalize,
        cleanup_temp=acq.cleanup_temp,
        persist_library=lambda rec: _post("loom_reaction_library", rec),
        library_rows=lambda: _get("loom_reaction_library?select=platform_content_id,"
                                  "duplicate_fingerprint"),
        pending_job=lambda fp: (_get("loom_acquisition_jobs?duplicate_fingerprint=eq."
                                     f"{fp}&authorization_type=eq.approved_for_acquisition"
                                     "&select=*&limit=1") or [None])[0],
        ceiling=lambda: (_get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0],
        record_run=lambda row: _post("loom_provider_runs", lane.safe_record(row)),
        ledger=ledger_mod.http_ledger(supabase_url, supabase_key),
        existing_run=lambda job_id: bool(_get("loom_provider_runs?select=id&acquisition_job_id=eq."
                                              f"{job_id}")),
        provider_runs_for_job=lambda job_id: _get(
            "loom_provider_runs?select=run_id,status,acquisition_job_id,"
            f"cost_actual_micro&acquisition_job_id=eq.{job_id}"),
        tmp_dir=tmp_dir,
        library_dir=library_dir,
    )
