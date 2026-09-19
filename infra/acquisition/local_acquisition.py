"""Local acquisition path (yt-dlp): same outcome contract, zero provider cost.

The paid path reserves against the Apify ceiling and reconciles an actual charge.
This path has nothing to reserve: it runs one bounded local attempt, writes a
truthful audit row at **$0**, and deliberately never touches the ceiling.

Everything is injectable so the whole flow is testable without yt-dlp, ffmpeg, or
network access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import library_root
import ytdlp_adapter as ya

LOCAL_ADAPTER = "local:yt-dlp"
LOCAL_PROVIDER = "local"
COST_LABEL = "Local acquisition · $0 provider cost"


def default_acquire(*, url: str, expected_id: str, tmp_dir: Path, platform: str = "",
                    library_dir: Path,
                    window: dict[str, Any] | None = None) -> dict[str, Any]:
    """The real bounded attempt (only reached in the live proof)."""
    def probe(cmd):
        # Metadata only: zero media bytes, zero cost. A transient extractor
        # hiccup (which --retries 0 can surface as an empty result set) is
        # retried once here rather than being reported as an unavailable source.
        last: Any = None
        for _attempt in range(3):
            try:
                meta = _json(ya.run_ytdlp_command(cmd, timeout=120))
            except ya.YtDlpError as e:
                last = e
                continue
            if isinstance(meta, dict) and (meta.get("id") or meta.get("entries")):
                return meta
            last = meta
        if isinstance(last, ya.YtDlpError):
            raise last
        return last if isinstance(last, dict) else {}

    return ya.acquire_local(
        url=url, expected_id=expected_id, tmp_dir=tmp_dir, library_dir=library_dir,
        window=window, platform=platform,
        run_probe=probe,
        run_download=lambda cmd: ya.run_ytdlp_command(cmd, timeout=600),
        run_ffprobe=ya.run_ffprobe_json,
    )


def _json(raw: str) -> dict[str, Any]:
    import json as _json_mod
    text = (raw or "").strip()
    if not text:
        raise ya.YtDlpError("BAD_METADATA", "probe produced no output")
    try:
        data = _json_mod.loads(text)
    except ValueError as e:
        raise ya.YtDlpError("BAD_METADATA", f"probe output was not JSON: {e}") from e
    if not isinstance(data, dict):
        raise ya.YtDlpError("BAD_METADATA", "probe JSON was not an object")
    return data


def run_local(*, canonical_url: str, content_id: str, video_id: str = "", platform: str = "",
              fingerprint: str = "", window: dict[str, Any] | None = None,
              acquire_fn: Callable[..., dict[str, Any]] = default_acquire,
              tmp_dir: Path | None = None, library_dir: Path | None = None,
              persist_library: Callable[[dict[str, Any]], Any] | None = None,
              ledger: Any | None = None, run_id: str = "",
              job_id: str = "") -> dict[str, Any]:
    """One bounded local attempt. Returns the same outcome shape as the paid path.

    On success the asset is already finalized in the library (atomically) and the
    library row is persisted; on failure partial files are gone (the adapter owns
    that) and the audit row records a zero-cost failure.
    """
    tmp = Path(tmp_dir or library_root.tmp_dir())
    lib = Path(library_dir or library_root.library_dir())
    expected = video_id or content_id

    if ledger is not None and run_id:
        # Zero-cost attempt: recorded for audit only, never reserved.
        ledger.record_run(run_id=run_id, actor_id=LOCAL_ADAPTER, job_id=job_id,
                          estimate_micro=0,
                          output_ref=f"local yt-dlp attempt for {expected}")

    try:
        result = acquire_fn(url=canonical_url, expected_id=expected, window=window,
                            platform=platform,
                            tmp_dir=tmp, library_dir=lib)
    except ya.YtDlpError as e:
        return _failure(e.code, e.message, ledger=ledger, run_id=run_id,
                        job_id=job_id, expected=expected, window=window)
    except Exception as e:  # noqa: BLE001 — a broken toolchain is a classified failure
        return _failure("LOCAL_TOOLCHAIN_ERROR", f"{type(e).__name__}: {e}"[:200],
                        ledger=ledger, run_id=run_id, job_id=job_id,
                        expected=expected, window=window)

    # Build the library row with the SAME contract the paid path uses, so both
    # lanes persist identical shapes (and uuid columns never receive "").
    import inspect as _inspect
    import approved_lane as _lane
    import reaction_library as _rl

    if persist_library is not None and not job_id:
        raise ya.YtDlpError("NO_JOB_ID",
                            "a library row requires the acquisition job id (uuid)")
    import youtube_url as _yt
    candidates = {
        "duplicate_fingerprint": ((window or {}).get("range_fingerprint")
                                  or fingerprint
                                  or _yt.fingerprint_for(result["canonical_url"])),
        "platform": "youtube",
        "platform_content_id": result["video_id"],
        "canonical_url": result["canonical_url"],
        "filename": result["asset_filename"],
        "digest": result["sha256"],
        "size_bytes": result["size_bytes"],
        "duration_seconds": result["duration_seconds"],
        "content_type": "video/mp4",
        "acquisition_job_id": job_id,
        "provenance": {
            "platform": platform or "youtube",
            "canonical_url": result["canonical_url"],
            "window": ({"start_ms": window["start_ms"], "end_ms": window["end_ms"],
                        "clip_duration_ms": window["clip_duration_ms"],
                        "origin": window.get("origin", "")} if window else None),
            "range_fingerprint": (window or {}).get("range_fingerprint", ""),
            "adapter": LOCAL_ADAPTER,
            "provider": LOCAL_PROVIDER,
            "provider_cost_micro": 0,
            "video_id": result["video_id"],
            "video_codec": result["video_codec"],
            "audio_codec": result["audio_codec"],
        },
    }
    accepted = set(_inspect.signature(_rl.plan_library_entry).parameters)
    record = _rl.plan_library_entry(
        **{k: v for k, v in candidates.items() if k in accepted})
    record = _lane.safe_record(record)

    persisted: Any = {}
    if persist_library is not None:
        persisted = persist_library(record)

    audit = {}
    if ledger is not None and run_id:
        audit = ledger.reconcile_local(
            run_id=run_id, succeeded=True,
            status_reason=f"local yt-dlp acquired {result['asset_filename']}")

    return {
        "ok": True,
        "status": "acquired",
        "attempts": result.get("attempts", 1),
        "library": {"asset_filename": result["asset_filename"],
                    "asset_hash": result["sha256"],
                    "size_bytes": result["size_bytes"],
                    "duration_seconds": result["duration_seconds"],
                    "path": result["path"],
                    "persisted": bool(persisted)},
        "media": {k: result.get(k) for k in ("duration_seconds", "size_bytes",
                                             "video_codec", "audio_codec",
                                             "width", "height")},
        "cost": {"actual_micro": 0, "estimated_micro": 0, "provider": LOCAL_PROVIDER,
                 "label": COST_LABEL},
        "provenance": {"adapter": LOCAL_ADAPTER, "provider": LOCAL_PROVIDER,
                       "run_id": run_id, "provider_cost_micro": 0,
                       "mode": ("bounded_segment" if window else "full_item"),
                       "window": ({"start_ms": window["start_ms"],
                                   "end_ms": window["end_ms"],
                                   "clip_duration_ms": window["clip_duration_ms"],
                                   "origin": window.get("origin", ""),
                                   "range_fingerprint": window.get("range_fingerprint", "")}
                                  if window else None)},
        "ledger": audit,
        "job_patch": {"status": "acquired", "retry_count": 1,
                      "status_reason": f"local yt-dlp acquired {result['asset_filename']}",
                      "asset_provenance": {"adapter": LOCAL_ADAPTER,
                                           "provider": LOCAL_PROVIDER,
                                           "run_id": run_id,
                                           "provider_cost_micro": 0}},
    }


# Stable, operator-facing classifications. A fixed POLICY refusal is not retryable.
POLICY_CODE_MAP = {"TOO_LONG": "DURATION_LIMIT", "TOO_LARGE": "SIZE_LIMIT"}
POLICY_REFUSAL_CODES = frozenset({
    "DURATION_LIMIT", "SIZE_LIMIT", "PLAYLIST_REJECTED", "LIVE_REJECTED",
    "MULTIPLE_VIDEOS", "MULTIPLE_RESULTS", "VIDEO_STREAM_COUNT", "NO_AUDIO",
    "UNSUPPORTED_VIDEO_CODEC", "UNSUPPORTED_AUDIO_CODEC", "BAD_DURATION",
    "UNKNOWN_DURATION", "CONTENT_ID_DRIFT", "ZERO_BYTES", "MISSING_FILE",
    "MISSING_VIDEO_ID", "MALFORMED_ID", "SELECTION_PARAM", "UNSUPPORTED_HOST",
    "UNSUPPORTED_PATH", "EMPTY_URL", "BAD_SCHEME", "CREDENTIALS",
})
RETRYABLE_TRANSIENT_CODES = frozenset({"NO_RESULTS", "MULTIPLE_RESULTS",
                                      "YTDLP_FAILED", "BAD_METADATA"})


def _failure(code: str, message: str, *, ledger: Any | None, run_id: str,
             job_id: str, expected: str,
             window: dict[str, Any] | None = None) -> dict[str, Any]:
    code = POLICY_CODE_MAP.get(code, code)
    policy = code in POLICY_REFUSAL_CODES and code not in RETRYABLE_TRANSIENT_CODES
    audit = {}
    if ledger is not None and run_id:
        audit = ledger.reconcile_local(
            run_id=run_id, succeeded=False,
            status_reason=f"{code}: {message}"[:200])
    return {
        "ok": False,
        "status": "failed",
        "attempts": 1,
        "failure_stage": "local_download",
        "policy_refusal": policy,
        "error": {"code": code, "message": message,
                  "retryable": not policy},
        "cost": {"actual_micro": 0, "estimated_micro": 0, "provider": LOCAL_PROVIDER,
                 "label": COST_LABEL},
        "provenance": {"adapter": LOCAL_ADAPTER, "provider": LOCAL_PROVIDER,
                       "run_id": run_id, "provider_cost_micro": 0,
                       "mode": ("bounded_segment" if window else "full_item"),
                       "window": ({"start_ms": window["start_ms"],
                                   "end_ms": window["end_ms"],
                                   "clip_duration_ms": window["clip_duration_ms"],
                                   "origin": window.get("origin", ""),
                                   "range_fingerprint": window.get("range_fingerprint", "")}
                                  if window else None)},
        "ledger": audit,
        "job_patch": {"status": "failed", "retry_count": 1,
                      "status_reason": f"local_download: {code}: {message}"[:200],
                      "asset_provenance": {"adapter": LOCAL_ADAPTER,
                                           "provider": LOCAL_PROVIDER,
                                           "run_id": run_id,
                                           "provider_cost_micro": 0}},
    }
