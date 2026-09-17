"""Local acquisition helper — HTTP server (127.0.0.1 only).

POST /acquire — the LOOM Chrome extension sends the canonical Instagram source +
the captured media candidate. The helper validates, downloads exactly one MP4
(validated redirects + byte ceiling), validates via ffprobe, records the
Acquisition Job + Personal Reaction Library ledger, and returns a REDACTED result
(never the signed CDN URL, never full auth headers).

Run:  ./run.sh  (sources ~/.openclaw/.env for Supabase creds, never printed)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import acquisition
import acquire as acq
import assemble as asm
import fmp4
from validate import ValidationError, validate_candidate, validate_media_url

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(HERE, ".token")
HOST = "127.0.0.1"
PORT = 8787

MAX_BODY = 8 * 1024
MAX_BYTES = 100 * 1024 * 1024      # 100 MiB
MAX_DURATION = 300                 # 5 minutes
DOWNLOAD_TIMEOUT = 60              # seconds
MAX_REDIRECTS = 5

CACHE_ROOT = os.path.expanduser("~/.openclaw/loom-reaction-library")
LIBRARY_DIR = os.path.join(CACHE_ROOT, "library")
TMP_DIR = os.path.join(CACHE_ROOT, ".tmp")

# Supabase creds are read by NAME from the environment at persist time (never
# printed, never logged). Names only here.
SUPABASE_URL_ENV = "LOOM_SUPABASE_URL"
SUPABASE_KEY_ENV = "LOOM_SUPABASE_SERVICE_ROLE_KEY"


def read_token() -> str:
    try:
        with open(TOKEN_PATH) as f:
            return f.read().strip()
    except OSError:
        return ""


def validate_request(auth_header, origin_header, body_bytes, token: str) -> str | None:
    """Pure request validation. Returns an error string, or None if valid."""
    if not token:
        return "helper token not configured"
    if not auth_header or auth_header != f"Bearer {token}":
        return "unauthorized"
    if origin_header and not origin_header.startswith("chrome-extension://"):
        return "invalid origin"
    if len(body_bytes) > MAX_BODY:
        return "request body too large"
    return None


def parse_body(body_bytes: bytes):
    """Strict JSON schema. Returns (payload, error)."""
    try:
        payload = json.loads(body_bytes.decode() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "invalid JSON"
    if not isinstance(payload, dict):
        return None, "body must be a JSON object"
    canonical = payload.get("canonical_url", "")
    if not isinstance(canonical, str) or not canonical:
        return None, "missing canonical_url"
    has_manifest = isinstance(payload.get("manifest_url"), str) and bool(payload["manifest_url"])
    has_segments = isinstance(payload.get("segments"), list) and bool(payload["segments"])
    has_candidate = isinstance(payload.get("candidate_url"), str) and bool(payload["candidate_url"])
    if not (has_manifest or has_segments or has_candidate):
        return None, "need manifest_url, segments, or candidate_url"
    actor = payload.get("actor", "bryan")
    payload["actor"] = (actor or "").strip() or "bryan"
    return payload, None


def _cleanup_tmp_dir():
    """Remove all temp files (manifests, init, fragments, intermediates)."""
    try:
        for name in os.listdir(TMP_DIR):
            p = os.path.join(TMP_DIR, name)
            if os.path.isfile(p):
                os.remove(p)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Supabase I/O (url + key explicit; never log values)
# ---------------------------------------------------------------------------
def _headers(key: str, body: bool = False) -> dict:
    h = {"apikey": key, "Authorization": f"Bearer {key}", "User-Agent": "LOOM-LocalHelper/1.0"}
    if body:
        h["Content-Type"] = "application/json"
        h["Prefer"] = "return=representation"
    return h


def persist_library_entry(supabase_url: str, key: str, record: dict):
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/loom_reaction_library",
        data=json.dumps(record).encode(),
        headers=_headers(key, body=True),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.loads(r.read().decode() or "[]")
    return rows[0] if isinstance(rows, list) and rows else record


def update_job_status(supabase_url: str, key: str, dedup_key: str, status: str, reason: str) -> None:
    q = urllib.parse.quote(dedup_key)
    body = {"status": status, "status_reason": reason}
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/loom_acquisition_jobs?dedup_key=eq.{q}",
        data=json.dumps(body).encode(),
        headers=_headers(key, body=True),
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "LOOM-LocalHelper/1.0"

    def log_message(self, fmt, *args):  # redact: no query strings in logs
        try:
            print(f"[helper] {self.address_string()} {fmt % args}", flush=True)
        except Exception:
            pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if self.path != "/acquire":
            self._send(404, {"ok": False, "error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length else b""
        err = validate_request(self.headers.get("Authorization"),
                               self.headers.get("Origin"), body, read_token())
        if err:
            self._send(401 if err == "unauthorized" else 400, {"ok": False, "error": err})
            return
        payload, perr = parse_body(body)
        if perr:
            self._send(400, {"ok": False, "error": perr})
            return
        self._handle_acquire(payload)

    def _handle_acquire(self, payload):
        try:
            validate_candidate(payload["canonical_url"], payload["candidate_url"])
        except ValidationError as e:
            self._send(400, {"ok": False, "error": e.code, "message": e.message})
            return
        try:
            result = self._acquire(payload)
            self._send(200, result)
        except acq.DownloadError as e:
            self._send(422, {"ok": False, "error": e.code, "message": e.message})
        except asm.AssemblyError as e:
            self._send(422, {"ok": False, "error": e.code, "message": e.message})
        except acquisition.AcquisitionError as e:
            self._send(400, {"ok": False, "error": e.code, "message": e.message})
        except ValidationError as e:
            self._send(400, {"ok": False, "error": e.code, "message": e.message})
        except Exception as e:
            self._send(500, {"ok": False, "error": "internal", "message": type(e).__name__})

    def _acquire(self, payload):
        supabase_url = os.environ.get(SUPABASE_URL_ENV, "")
        key = os.environ.get(SUPABASE_KEY_ENV, "")

        job = acq.build_acquisition_payload(
            canonical_url=payload["canonical_url"],
            candidate_host=payload.get("candidate_host", ""),
            actor=payload["actor"],
            max_bytes=MAX_BYTES,
            max_duration=MAX_DURATION,
        )

        # Idempotency: first-wins on dedup_key (duplicate click -> no new job).
        job_row, created = acquisition.persist_job(supabase_url, key, job) if (supabase_url and key) else (job, True)
        if not created:
            action = asm.plan_job_retry(job_row.get("status"))
            if action == "duplicate":
                return {"ok": True, "status": "duplicate", "submission_id": job_row.get("id"),
                        "job_status": job_row.get("status"),
                        "message": "already acquired (no new download)"}
            # Idempotent retry: reset a failed/blocked job back to running.
            if supabase_url and key:
                update_job_status(supabase_url, key, job["dedup_key"],
                                  acquisition.STATUS_RUNNING, "retry")

        os.makedirs(LIBRARY_DIR, exist_ok=True)
        os.makedirs(TMP_DIR, exist_ok=True)

        final_tmp = None
        try:
            manifest_url = payload.get("manifest_url", "")
            segments = payload.get("segments") or []
            if manifest_url:
                validate_media_url(manifest_url)
                final_tmp, written = fmp4.assemble_from_manifest(
                    manifest_url=manifest_url, tmp_dir=TMP_DIR,
                    max_bytes=MAX_BYTES, timeout=DOWNLOAD_TIMEOUT)
            elif segments:
                init_url = payload.get("init_url", "")
                audio_init = payload.get("audio_init_url", "")
                audio_segments = payload.get("audio_segments") or []
                for u in [init_url] + list(segments) + ([audio_init] if audio_init else []) + list(audio_segments):
                    validate_media_url(u)
                final_tmp, written = fmp4.assemble_fmp4(
                    video_init=init_url, video_segments=segments,
                    audio_init=audio_init, audio_segments=audio_segments,
                    tmp_dir=TMP_DIR, max_bytes=MAX_BYTES, timeout=DOWNLOAD_TIMEOUT)
            else:
                tmp, written, status, ct = acq.download_one_video(
                    payload["candidate_url"], TMP_DIR, max_bytes=MAX_BYTES,
                    timeout=DOWNLOAD_TIMEOUT, max_redirects=MAX_REDIRECTS)
                acq.classify_downloaded_file(tmp, http_status=status,
                                             content_type=ct, size_bytes=written)
                final_tmp = tmp

            meta = acq.parse_ffprobe_result(
                acq.run_ffprobe(final_tmp), actual_size_bytes=written,
                max_bytes=MAX_BYTES, max_duration=MAX_DURATION)

            digest = acq.checksum_sha256(final_tmp)
            filename = acq.library_filename(payload["canonical_url"], digest)
            final_path = os.path.join(LIBRARY_DIR, filename)
            acq.finalize(final_tmp, final_path)  # atomic rename after validation
            final_tmp = None
        except Exception as e:
            # Guarantee no final or partial asset on ANY failure.
            if final_tmp:
                acq.cleanup_temp(final_tmp)
            try:
                if supabase_url and key:
                    reason = getattr(e, "code", None) or type(e).__name__
                    msg = getattr(e, "message", "")
                    if msg:
                        reason = f"{reason}: {msg}"
                    update_job_status(supabase_url, key, job["dedup_key"],
                                      acquisition.STATUS_FAILED, reason)
            except Exception:
                pass
            raise
        finally:
            _cleanup_tmp_dir()

        # Persist the Reaction Library ledger entry.
        rec = acq.build_library_entry(
            canonical_url=payload["canonical_url"],
            filename=filename,
            digest=digest,
            size_bytes=written,
            duration_seconds=meta["duration_seconds"],
            content_type="video/mp4",
            local_path=final_path,
            job_id=job_row.get("id"),
            is_non_production=True,
        )
        if supabase_url and key:
            lib_row = persist_library_entry(supabase_url, key, rec)
        else:
            lib_row = rec

        if supabase_url and key:
            update_job_status(supabase_url, key, job["dedup_key"],
                              acquisition.STATUS_ACQUIRED, "")

        # REDACTED result — never the signed CDN URL.
        return {
            "ok": True,
            "status": "acquired",
            "destination": "personal_reaction_library",
            "routing": "Awaiting tenant assignment",
            "adapter": "chrome_network_capture",
            "submission_id": job_row.get("id"),
            "library": {
                "filename": filename,
                "size_bytes": written,
                "duration_seconds": meta["duration_seconds"],
                "width": meta["width"],
                "height": meta["height"],
                "frame_rate": meta["frame_rate"],
                "has_audio": meta["has_audio"],
                "content_type": "video/mp4",
                "sha256": digest,
                "local_path": final_path,
            },
        }


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"LOOM local acquisition helper listening on {HOST}:{PORT} (token configured: {bool(read_token())})",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
