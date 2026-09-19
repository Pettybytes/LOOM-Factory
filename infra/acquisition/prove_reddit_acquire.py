#!/usr/bin/env python3
"""Bounded Reddit native-video acquisition proof (PUBLIC path — no cookies).

    python3 infra/acquisition/prove_reddit_acquire.py [share-or-post-url]

Sequence: resolve (bounded hops, Reddit hosts only) → identity → yt-dlp metadata
probe (PUBLIC, no authentication) → classify → select one H.264 video + one AAC
audio stream → bounded download of both → local mux → shared normalization
(MP4/H.264/AAC/yuv420p/fast-start) → hash → atomic finalize → Reaction Library row.

Authentication is NOT used here: the post is public. The browser session is only
ever consulted when the classified evidence is an age/auth gate.

Run with env sourced:  set -a; source ~/.openclaw/.env; set +a
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

REPO = Path(__file__).resolve().parents[2]
for _d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(REPO / "infra" / _d))

import acquire as acq  # noqa: E402
import assemble as asm  # noqa: E402
import helper  # noqa: E402
import library_root  # noqa: E402
import media_normalize  # noqa: E402
import reaction_library as rl  # noqa: E402
import reddit_context as rctx  # noqa: E402
import reddit_media as rm  # noqa: E402
import reddit_session as rsess  # noqa: E402
import reddit_url as ru  # noqa: E402

DEFAULT_URL = "https://www.reddit.com/r/TheCapitalLink/s/rPHdJGq2A2"
UA = "LOOM-factory/1.0 (Reddit exact-URL acquisition proof; contact: bryan)"
MAX_BYTES = 100 * 1024 * 1024
MAX_DURATION = 300.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def resolver(url: str):
    """One hop. No auto-follow; the caller enforces hops and hosts."""
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with _OPENER.open(req, timeout=25) as r:
            return r.status, None, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get("Location") if e.headers else None), url
    except Exception:  # noqa: BLE001
        return None, None, url


def ytdlp_metadata(url: str) -> dict:
    """PUBLIC metadata probe. No cookie arguments are passed."""
    cmd = ["yt-dlp", "--skip-download", "--no-warnings", "--socket-timeout", "25",
           "--retries", "0", "--dump-json", url]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"_error": (proc.stderr or "").strip().splitlines()[-1:],
                "_stderr": proc.stderr[-400:]}
    return json.loads(proc.stdout)


def main() -> None:
    url = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL).strip()
    out: dict = {"1_resolve": {}, "2_public_probe": {}, "3_classify": {},
                 "4_streams": {}, "5_acquire": {}, "6_session": {}, "7_hygiene": {}}

    # --- 1. identity -------------------------------------------------------
    ident = ru.parse(url, resolver=resolver)
    out["1_resolve"] = {"input": url, "resolved_from": ident.get("resolved_from", ""),
                        "canonical_url": ident["canonical_url"],
                        "post_id": ident["post_id"], "subreddit": ident["subreddit"],
                        "hops": ident.get("hops", "0"),
                        "fingerprint": ru.fingerprint_for(ident["canonical_url"])}

    # --- 2. PUBLIC metadata probe (no auth) --------------------------------
    meta = ytdlp_metadata(ident["canonical_url"])
    if meta.get("_error"):
        out["2_public_probe"] = {"ok": False, "error": str(meta["_error"])[:200],
                                 "authentication_used": False}
        code = rctx.classify_failure(" ".join(meta.get("_error") or []))
        out["2_public_probe"]["classified"] = code
        if rsess.needs_authenticated_session(code):
            out["2_public_probe"]["next"] = rsess.blocker_instruction()
        print(json.dumps(out, indent=2, default=str))
        return
    out["2_public_probe"] = {"ok": True, "authentication_used": False,
                             "extractor": meta.get("extractor_key"),
                             "media_id": meta.get("id"), "t3": meta.get("display_id"),
                             "title": meta.get("title"), "uploader": meta.get("uploader"),
                             "duration": meta.get("duration"),
                             "width": meta.get("width"), "height": meta.get("height"),
                             "format_count": len(meta.get("formats") or [])}

    # --- 3. classification -------------------------------------------------
    cls = rctx.classify_metadata(meta)
    out["3_classify"] = {"classification": cls["classification"],
                         "reason": cls.get("reason"), "evidence": cls.get("evidence")}
    if cls["classification"] == rctx.EXTERNAL_HOST:
        out["3_classify"]["delegation"] = rm.delegate_external(cls.get("platform_hint", ""))
        print(json.dumps(out, indent=2, default=str))
        return
    if cls["classification"] != rctx.NATIVE_VIDEO:
        out["3_classify"]["refused"] = True
        print(json.dumps(out, indent=2, default=str))
        return

    # --- 4. stream selection ----------------------------------------------
    sel = rctx.select_streams(meta)
    video, audio = sel["video"], sel["audio"]
    out["4_streams"] = {
        "video_format": (video or {}).get("format_id"),
        "video_protocol": (video or {}).get("protocol"),
        "video_host": (video or {}).get("url", "").split("/")[2] if video else "",
        "audio_format": (audio or {}).get("format_id"),
        "audio_protocol": (audio or {}).get("protocol"),
        "audio_host": (audio or {}).get("url", "").split("/")[2] if audio else "",
        "video_candidates": sel["video_candidates"],
        "audio_candidates": sel["audio_candidates"],
        "audio_stream_available": sel["audio_stream_available"]}

    # --- 5. bounded acquire (real mux + normalization + library row) -------
    library_root.ensure_dirs()
    io = rm.RedditMediaIO(
        download_one_video=functools.partial(acq.download_one_video,
                                            host_check=ru.is_reddit_media_host),
        classify_downloaded_file=acq.classify_downloaded_file,
        run_ffprobe=acq.run_ffprobe,
        parse_ffprobe_result=acq.parse_ffprobe_result,
        checksum_sha256=acq.checksum_sha256,
        finalize=acq.finalize,
        cleanup_temp=acq.cleanup_temp,
        run_ffmpeg=media_normalize.run_ffmpeg_command,
        run_ffprobe_json=media_normalize.run_ffprobe_json,
        mux_builder=asm.build_mux_args,
        tmp_dir=str(library_root.tmp_dir()),
        library_dir=str(library_root.library_dir()))

    supabase_url = os.environ.get("LOOM_SUPABASE_URL", "")
    supabase_key = os.environ.get("LOOM_SUPABASE_SERVICE_ROLE_KEY", "")
    target = {"post_id": ident["post_id"], "canonical_url": ident["canonical_url"],
              "subreddit": ident["subreddit"], "title": meta.get("title") or "",
              "creator_name": meta.get("uploader") or "",
              "creator_handle": meta.get("uploader_id") or "",
              "video_url": (video or {}).get("url", ""),
              "audio_url": (audio or {}).get("url", ""),
              "duration_seconds": meta.get("duration"),
              "width": meta.get("width"), "height": meta.get("height")}

    def persist(rec):
        if not (supabase_url and supabase_key):
            return rec
        return helper.persist_library_entry(supabase_url, supabase_key, rec) or rec

    try:
        result = rm.acquire_reddit_native(
            target=target, io=io, library_rows=helper.library_rows(),
            persist_library=persist, max_bytes=MAX_BYTES, max_duration=MAX_DURATION)
    except rm.RedditMediaError as e:
        out["5_acquire"] = {"ok": False, "error": e.code, "message": e.message,
                            "detail": rsess.redact(json.dumps(e.detail))[:400]}
        print(json.dumps(out, indent=2, default=str))
        return
    out["5_acquire"] = {k: result.get(k) for k in
                        ("ok", "idempotent", "already_acquired", "asset_filename",
                         "asset_hash", "display_title", "size_bytes", "duration_seconds",
                         "width", "height", "frame_rate", "video_codec", "audio_codec",
                         "has_audio", "audio_state", "muxed", "normalized", "remuxed",
                         "gaps", "http_status", "downloaded_content_type", "local_path")}
    out["5_acquire"]["library_id"] = (result.get("library_row") or {}).get("id", "")

    # --- 6. session posture (value-free) ----------------------------------
    out["6_session"] = {**rsess.describe(host="reddit.com"),
                        "authentication_used_for_this_acquire": False,
                        "cookie_args_passed_to_ytdlp": []}

    # --- 7. hygiene --------------------------------------------------------
    safe = json.dumps(out, default=str)
    out["7_hygiene"] = {
        "signed_media_url_in_receipt": ("v.redd.it" in safe),
        "cookie_material_in_receipt": any(m in safe.lower() for m in
                                          ("reddit_session", "set-cookie", "netscape http cookie file")),
        "cookies_sent_anywhere": False,
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
