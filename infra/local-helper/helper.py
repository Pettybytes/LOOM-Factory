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
from library_root import library_root
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

CACHE_ROOT = str(library_root())
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
# Reaction Library read routes (READ-ONLY, 127.0.0.1 only)
#
# These serve the already-acquired local asset so a human can actually watch it
# and inspect its provenance. They never mutate anything, never ingest, and never
# accept a path from the caller: the hash is looked up in the library ledger and
# the file is resolved inside LIBRARY_DIR only.
# ---------------------------------------------------------------------------
def _creds() -> tuple[str, str]:
    return (os.environ.get(SUPABASE_URL_ENV, ""), os.environ.get(SUPABASE_KEY_ENV, ""))


def _rest_get(path: str) -> list:
    url, key = _creds()
    if not url or not key:
        return []
    req = urllib.request.Request(f"{url}/rest/v1/{path}", headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode() or "[]")
    except Exception:
        return []


def library_record(asset_hash: str) -> dict | None:
    rows = _rest_get(f"loom_reaction_library?asset_hash=eq.{urllib.parse.quote(asset_hash)}"
                     "&select=*")
    return rows[0] if rows else None


def acquisition_job(job_id: str) -> dict | None:
    if not job_id:
        return None
    rows = _rest_get(f"loom_acquisition_jobs?id=eq.{urllib.parse.quote(str(job_id))}&select=*")
    return rows[0] if rows else None


def _safe_media_path(record: dict) -> str | None:
    """Resolve the library file for a record — never outside LIBRARY_DIR."""
    name = os.path.basename(str(record.get("asset_filename") or ""))
    if not name:
        return None
    path = os.path.join(LIBRARY_DIR, name)
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(LIBRARY_DIR) + os.sep):
        return None
    return real if os.path.isfile(real) else None


def library_rows() -> list:
    """Every acquired asset, newest first."""
    return _rest_get("loom_reaction_library?select=*&order=created_at.desc")


def job_statuses() -> dict:
    rows = _rest_get("loom_acquisition_jobs?select=id,status,status_reason")
    return {str(r.get("id")): r for r in rows if isinstance(r, dict)}


def poster_path_for(record: dict) -> str | None:
    """Poster frame beside the asset, if one has been produced."""
    name = os.path.basename(str(record.get("asset_filename") or ""))
    if not name:
        return None
    stem = os.path.splitext(name)[0]
    candidate = os.path.join(LIBRARY_DIR, f"{stem}__poster.jpg")
    real = os.path.realpath(candidate)
    if not real.startswith(os.path.realpath(LIBRARY_DIR) + os.sep):
        return None
    return real if os.path.isfile(real) else None


def _fmt_duration(seconds) -> str:
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    return f"{int(s // 60)}:{int(s % 60):02d}"


def render_gallery_page(records: list, jobs: dict) -> bytes:
    """Browsable index of the whole library; every card opens its detail page."""
    cards = []
    for r in records:
        if not isinstance(r, dict):
            continue
        digest = str(r.get("asset_hash") or "")
        if not digest:
            continue
        job = jobs.get(str(r.get("acquisition_job_id") or "")) or {}
        status = job.get("status") or ("acquired" if r.get("asset_filename") else "unknown")
        poster = poster_path_for(r)
        thumb = (f'<img src="/library/{_esc(digest)}/poster" alt="poster">'
                 if poster else '<div class="noposter">no poster</div>')
        size = r.get("size_bytes")
        size_txt = f"{int(size) / (1024*1024):.1f} MiB" if size else "unknown"
        acquired = str(r.get("created_at") or "")[:19].replace("T", " ")
        cards.append(f"""
  <a class="card" href="/library/{_esc(digest)}">
    {thumb}
    <div class="meta">
      <div class="name">{_esc(r.get('asset_filename'))}</div>
      <div class="row"><span class="k">platform</span>{_esc(r.get('platform') or '')}</div>
      <div class="row"><span class="k">duration</span>{_fmt_duration(r.get('duration_seconds'))}</div>
      <div class="row"><span class="k">size</span>{_esc(size_txt)}</div>
      <div class="row"><span class="k">status</span><span class="badge">{_esc(status)}</span></div>
      <div class="row"><span class="k">acquired</span>{_esc(acquired)}</div>
    </div>
  </a>""")
    body = "".join(cards) or '<p class="empty">No assets in the library yet.</p>'
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Reaction Library · {len(records)} assets</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; padding:28px; background:#0f1115; color:#e6e8ec;
        font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
 h1 {{ font-size:20px; margin:0 0 4px; }}
 .sub {{ color:#8b93a1; font-size:13px; margin-bottom:22px; }}
 .grid {{ display:grid; gap:18px; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); }}
 a.card {{ display:block; text-decoration:none; color:inherit; background:#161a20;
           border:1px solid #232830; border-radius:14px; overflow:hidden;
           transition:border-color .15s ease, transform .15s ease; }}
 a.card:hover {{ border-color:#3d4756; transform:translateY(-2px); }}
 a.card img {{ display:block; width:100%; aspect-ratio:9/16; object-fit:cover; background:#000; }}
 .noposter {{ display:flex; align-items:center; justify-content:center; aspect-ratio:9/16;
              background:#11151a; color:#5d6672; font-size:12px; }}
 .meta {{ padding:10px 12px 14px; }}
 .name {{ font-size:12px; color:#aab2bf; word-break:break-all; margin-bottom:8px; }}
 .row {{ display:flex; justify-content:space-between; font-size:12.5px; padding:2px 0; }}
 .k {{ color:#7f8794; }}
 .badge {{ background:#1f2a24; color:#7fd6a5; border-radius:999px; padding:0 8px; font-size:11px; }}
 .empty {{ color:#8b93a1; }}
</style></head><body>
<h1>Reaction Library</h1>
<div class="sub">Local gallery · {len(records)} assets · served read-only from this Mac</div>
<div class="grid">{body}
</div>
</body></html>"""
    return html.encode("utf-8")


def _esc(value) -> str:
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def render_library_page(record: dict, job: dict | None) -> bytes:
    """Self-contained detail page: player, metadata, hash, provenance, source."""
    digest = str(record.get("asset_hash") or "")
    filename = str(record.get("asset_filename") or "")
    size = record.get("size_bytes")
    duration = record.get("duration_seconds")
    prov = record.get("provenance") or {}
    job = job or {}
    asset_prov = job.get("asset_provenance") or {}
    rows = [
        ("Filename", filename),
        ("Size", f"{int(size):,} bytes" if size else "unknown"),
        ("Duration", f"{float(duration):.2f} s" if duration else "unknown"),
        ("Content type", record.get("content_type") or "video/mp4"),
        ("SHA-256", digest),
        ("Platform", record.get("platform") or ""),
        ("Source URL", record.get("canonical_url") or ""),
        ("Provenance (adapter)", asset_prov.get("adapter") or prov.get("adapter") or ""),
        ("Provider run", asset_prov.get("run_id") or prov.get("run_id") or ""),
        ("Acquisition status", job.get("status") or ""),
        ("Acquisition job", job.get("id") or record.get("acquisition_job_id") or ""),
        ("Acquired at", record.get("created_at") or ""),
    ]
    body_rows = "".join(
        f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in rows)
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Reaction Library · {_esc(filename)}</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; padding:28px; background:#0f1115; color:#e6e8ec;
        font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
 h1 {{ font-size:19px; margin:0 0 4px; }}
 .sub {{ color:#8b93a1; font-size:13px; margin-bottom:18px; }}
 .wrap {{ display:flex; gap:28px; flex-wrap:wrap; align-items:flex-start; }}
 video {{ width:min(340px,92vw); border-radius:12px; background:#000;
          border:1px solid #262b33; }}
 table {{ border-collapse:collapse; min-width:min(520px,90vw); }}
 th {{ text-align:left; color:#8b93a1; font-weight:600; padding:7px 18px 7px 0;
       vertical-align:top; white-space:nowrap; }}
 td {{ padding:7px 0; word-break:break-all; }}
 tr {{ border-top:1px solid #1c2027; }}
 code {{ background:#171b21; padding:2px 6px; border-radius:6px; }}
 .empty {{ color:#f0a4a4; }}
</style></head><body>
<h1>Reaction Library</h1>
<div class="sub">Local asset detail · read-only view served from this Mac</div>
<div class="wrap">
  <video controls preload="metadata" playsinline
         src="/library/{_esc(digest)}/media"></video>
  <table>{body_rows}</table>
</div>
</body></html>"""
    return html.encode("utf-8")


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

    def do_GET(self):
        path = (self.path or "").split("?", 1)[0]
        if path == "/health":
            self._send(200, {"ok": True, "service": "loom-local-helper"})
            return
        if path.startswith("/library/"):
            parts = [p for p in path.split("/") if p]
            # /library  |  /library/<hash>  |  /library/<hash>/{media|poster}
            if len(parts) == 2:
                self._serve_library_page(parts[1])
            elif len(parts) == 3 and parts[2] == "media":
                self._serve_library_media(parts[1])
            elif len(parts) == 3 and parts[2] == "poster":
                self._serve_library_poster(parts[1])
            else:
                self._send(404, {"ok": False, "error": "not_found"})
            return
        if path == "/library":
            self._serve_library_gallery()
            return
        self._send(404, {"ok": False, "error": "not_found"})

    def _serve_library_gallery(self):
        self._send_html(200, render_gallery_page(library_rows(), job_statuses()))

    def _serve_library_poster(self, asset_hash: str):
        record = library_record(asset_hash)
        path = poster_path_for(record) if record else None
        if not path:
            self._send(404, {"ok": False, "error": "no_poster"})
            return
        with open(path, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_library_page(self, asset_hash: str):
        record = library_record(asset_hash)
        if not record:
            self._send_html(404, b"<h1>Not in the Reaction Library</h1>")
            return
        job = acquisition_job(str(record.get("acquisition_job_id") or ""))
        self._send_html(200, render_library_page(record, job))

    def _serve_library_media(self, asset_hash: str):
        """Range-capable, read-only media serving so the page can seek/play."""
        record = library_record(asset_hash)
        if not record:
            self._send(404, {"ok": False, "error": "not_in_library"})
            return
        path = _safe_media_path(record)
        if not path:
            self._send(404, {"ok": False, "error": "asset_file_missing"})
            return
        size = os.path.getsize(path)
        start, end = 0, size - 1
        status = 200
        rng = self.headers.get("Range") or ""
        if rng.startswith("bytes="):
            spec = rng[len("bytes="):].split(",", 1)[0].strip()
            first, _, last = spec.partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:
                    start = max(0, size - int(last))
            except ValueError:
                start, end = 0, size - 1
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", record.get("content_type") or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _send_html(self, code, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
