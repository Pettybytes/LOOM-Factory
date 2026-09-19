#!/usr/bin/env python3
"""Migrate Reaction Library assets to human-readable filenames (recoverable).

    python3 infra/acquisition/migrate_library_titles.py            # dry run
    python3 infra/acquisition/migrate_library_titles.py --apply    # execute

Per-asset sequence (never a blind bulk rename):

  1. read the durable row and its current path
  2. derive the new filename from that row's metadata only
  3. create the new path WITHOUT overwriting an existing file
  4. verify byte size and SHA-256 against the original
  5. update the durable row under a compare-and-swap on the OLD filename
  6. verify the renamed asset + poster resolve
  7. remove the obsolete path only after the row and the asset are verified
  8. on any failure: remove the copy, keep the original, report the failure

Title provenance is recorded honestly: ``title_source=platform`` only when the
platform genuinely supplied a distinct title; a placeholder title (a generic label
that merely restates the identity) is rejected and the row is reported as
``title_limited`` instead. No AI model and no paid provider is ever consulted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _d in ("acquisition", "local-helper", "discord-review"):
    sys.path.insert(0, str(REPO / "infra" / _d))

import library_root  # noqa: E402
import reaction_library as rl  # noqa: E402
import title_contract as tc  # noqa: E402

LIB = library_root.library_dir()
TABLE = "loom_reaction_library"

#: Canonical-URL host -> platform. AUTHORITATIVE: three library rows carry a wrong
#: ``platform`` column ("youtube") while their filenames and canonical URLs are
#: Twitch/Kick. ``loom_acquisition_jobs.platform`` agrees with the URL.
HOST_PLATFORM = (
    ("tiktok.com", "tiktok"), ("instagram.com", "instagram"),
    ("clips.twitch.tv", "twitch"), ("twitch.tv", "twitch"),
    ("kick.com", "kick"), ("youtube.com", "youtube"), ("youtu.be", "youtube"),
)

#: Platform-read titles (free local metadata, cached). Keyed by content id.
#: ``title`` is the platform's own words; a placeholder is marked as such and the
#: display title falls through to creator + content-type label instead.
PLATFORM_META: dict[str, dict[str, str]] = {}


def _human_identity(raw: str) -> str:
    """A creator identity that is meaningful to a human.

    yt-dlp's ``uploader_id`` is frequently a NUMERIC user id (Instagram) or an
    internal channel number (Twitch/Kick/TikTok). A pure digit string is not a
    name and must never become the creator segment of a filename.
    """
    value = str(raw or "").strip().lstrip("@").strip()
    if not value:
        return ""
    if value.isdigit() or (len(value) >= 6 and value.replace("_", "").isdigit()):
        return ""
    return value


def load_platform_meta(path: str = "/tmp/loom-meta/sources.json") -> None:
    try:
        raw = json.loads(Path(path).read_text())
    except Exception:
        return
    for cid, rec in raw.items():
        if not rec.get("ok"):
            continue
        # Prefer the human name; a numeric identity is discarded outright.
        name = _human_identity(rec.get("uploader")) or _human_identity(rec.get("channel"))
        handle = _human_identity(rec.get("uploader_id"))
        if handle and handle.lower() == name.lower():
            handle = ""
        PLATFORM_META[cid] = {
            "title": str(rec.get("title") or ""),
            "creator_name": name,
            "creator_handle": handle,
        }


def platform_from_url(url: str, fallback: str = "") -> str:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    for suffix, name in HOST_PLATFORM:
        if host == suffix or host.endswith("." + suffix):
            return name
    return (fallback or "").lower()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Rest:
    def __init__(self, url: str, key: str) -> None:
        self.url, self.key = url.rstrip("/"), key

    def _headers(self, write: bool = False) -> dict[str, str]:
        h = {"api" + "key": self.key, "Authorization": "Bearer " + self.key,
             "User-Agent": "LOOM-TitleMigration/1.0"}
        if write:
            h["Content-Type"] = "application/json"
            h["Prefer"] = "return=representation"
        return h

    def _do(self, method: str, path: str, body: dict | None = None) -> list:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}/rest/v1/{path}", data=data,
                                     headers=self._headers(body is not None), method=method)
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode() or "[]"
        return json.loads(raw) if raw.strip().startswith("[") else []

    def rows(self) -> list[dict]:
        return self._do("GET", f"{TABLE}?select=*&order=created_at.asc")

    def cas_filename(self, row_id: str, old_name: str, patch: dict) -> list[dict]:
        """Compare-and-swap on the OLD filename: a concurrent rename matches 0 rows."""
        q = ("id=eq." + urllib.parse.quote(str(row_id))
             + "&asset_filename=eq." + urllib.parse.quote(old_name))
        return self._do("PATCH", f"{TABLE}?{q}", patch)


def plan(row: dict, taken: set[str]) -> dict:
    """Derive the new filename/meta for one row. Pure — no filesystem writes."""
    cid = str(row.get("platform_content_id") or "")
    url = str(row.get("canonical_url") or "")
    platform = platform_from_url(url, str(row.get("platform") or ""))
    meta = PLATFORM_META.get(cid, {})
    platform_title = meta.get("title", "")
    placeholder = tc.is_placeholder_title(platform_title, cid)
    stored = rl.asset_titles(row)
    fields = tc.title_fields(
        platform=platform, content_id=cid, digest=str(row.get("asset_hash") or ""),
        creator_name=meta.get("creator_name", "") or stored.get("creator_name", ""),
        creator_handle=meta.get("creator_handle", "") or stored.get("creator_handle", ""),
        source_title="" if placeholder else platform_title,
        stored_title=stored.get("display_title", ""),
        taken=taken)
    old = str(row.get("asset_filename") or "")
    return {
        "id": str(row.get("id") or ""),
        "content_id": cid,
        "old_filename": old,
        "new_filename": fields["asset_filename"],
        "display_title": fields["display_title"],
        "source_title": "" if placeholder else tc.clean_title(platform_title),
        "creator_name": fields["creator_name"],
        "creator_handle": fields["creator_handle"],
        "platform_before": str(row.get("platform") or ""),
        "platform_after": platform,
        "title_source": "platform" if (platform_title and not placeholder) else "derived",
        "title_limited": fields["title_limited"],
        "platform_corrected": str(row.get("platform") or "") != platform,
        "unchanged": old == fields["asset_filename"],
        "old_poster": tc.poster_filename(old) if old else "",
        "new_poster": fields["poster_filename"],
    }


def _rename_pair(old_name: str, new_name: str, report: dict) -> dict:
    """Copy → verify → (caller CASes) → remove. Returns integrity evidence."""
    src, dst = LIB / old_name, LIB / new_name
    out: dict = {"old": old_name, "new": new_name}
    if not src.is_file():
        out["error"] = "MISSING_SOURCE"
        return out
    if dst.exists():
        out["error"] = "DEST_EXISTS"
        return out
    before_size, before_hash = src.stat().st_size, sha256_file(src)
    shutil.copy2(src, dst)
    after_size, after_hash = dst.stat().st_size, sha256_file(dst)
    out.update({"bytes": after_size, "sha256": after_hash,
                "size_matches": before_size == after_size,
                "hash_matches": before_hash == after_hash})
    if not (out["size_matches"] and out["hash_matches"]):
        dst.unlink(missing_ok=True)
        out["error"] = "INTEGRITY_MISMATCH"
    return out


def migrate_one(rest: Rest, row: dict, p: dict, *, apply: bool) -> dict:
    result = dict(p)
    if p["unchanged"] and not p["platform_corrected"]:
        result["action"] = "skipped_already_readable"
        return result

    if not apply:
        result["action"] = "would_rename"
        return result

    # 3./4. create the new path, verify integrity BEFORE touching the durable row.
    video = _rename_pair(p["old_filename"], p["new_filename"], result)
    if video.get("error"):
        result["action"] = "failed"
        result["failure"] = video
        return result

    poster = None
    if p["old_poster"] and (LIB / p["old_poster"]).is_file():
        poster = _rename_pair(p["old_poster"], p["new_poster"], result)
        if poster.get("error"):
            (LIB / p["new_filename"]).unlink(missing_ok=True)
            result["action"] = "failed"
            result["failure"] = {"stage": "poster", **poster}
            return result

    # 5. durable row under compare-and-swap on the OLD filename.
    patch = {
        "asset_filename": p["new_filename"],
        "platform": p["platform_after"],
        "provenance": {
            **(row.get("provenance") if isinstance(row.get("provenance"), dict) else {}),
            "display_title": p["display_title"],
            "source_title": p["source_title"],
            "creator_name": p["creator_name"],
            "creator_handle": p["creator_handle"],
            "title_source": p["title_source"],
            "filename_migrated_from": p["old_filename"],
        },
    }
    try:
        updated = rest.cas_filename(p["id"], p["old_filename"], patch)
    except urllib.error.HTTPError as e:
        updated = []
        result["cas_http_error"] = f"HTTP {e.code}"
    if not updated:
        # 8. failure -> restore the original state and keep the old path.
        (LIB / p["new_filename"]).unlink(missing_ok=True)
        if poster:
            (LIB / p["new_poster"]).unlink(missing_ok=True)
        result["action"] = "failed"
        result["failure"] = {"stage": "row_cas", "reason": "concurrent change or write refused"}
        return result

    # 6. verify the row now resolves to a real file (what the viewer serves).
    served = rest._do("GET", f"{TABLE}?id=eq.{urllib.parse.quote(p['id'])}"
                             "&select=asset_filename,platform,provenance")
    name = str((served[0] if served else {}).get("asset_filename") or "")
    resolved = (LIB / os.path.basename(name)).is_file() if name else False
    # The poster is only required when one was actually renamed: an asset that
    # never had a poster must not fail this check.
    poster_ok = True if poster is None else (LIB / p["new_poster"]).is_file()
    result["served_filename"] = name
    result["served_file_exists"] = resolved
    result["served_poster_exists"] = poster_ok
    if not (resolved and poster_ok):
        # Roll the durable row back so no split state can survive, then drop the
        # copy. The original path was never removed, so the asset stays intact.
        try:
            rest.cas_filename(p["id"], p["new_filename"],
                             {"asset_filename": p["old_filename"],
                              "platform": p["platform_before"]})
        except Exception:  # noqa: BLE001 — reported below, never silent
            result["rollback_failed"] = True
        (LIB / p["new_filename"]).unlink(missing_ok=True)
        if poster:
            (LIB / p["new_poster"]).unlink(missing_ok=True)
        result["action"] = "failed"
        result["rolled_back"] = True
        result["failure"] = {"stage": "serve_verify", "served": name}
        return result

    # 7. only now remove the obsolete paths.
    (LIB / p["old_filename"]).unlink(missing_ok=True)
    if poster:
        (LIB / p["old_poster"]).unlink(missing_ok=True)
    result["action"] = "renamed"
    return result


def main() -> None:
    apply = "--apply" in sys.argv
    load_platform_meta()
    env = {}
    for line in (Path.home() / ".openclaw/.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    rest = Rest(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])

    rows = rest.rows()
    taken = {str(r.get("asset_filename") or "").lower() for r in rows}
    report = {"apply": apply, "library_dir": str(LIB), "rows": len(rows),
              "items": [], "renamed": 0, "skipped": 0, "failed": 0,
              "platform_corrected": 0, "title_limited": []}
    for row in rows:
        p = plan(row, taken - {str(row.get("asset_filename") or "").lower()})
        out = migrate_one(rest, row, p, apply=apply)
        report["items"].append(out)
        if out["action"] in ("renamed", "would_rename"):
            report["renamed"] += 1
        elif out["action"].startswith("skipped"):
            report["skipped"] += 1
        else:
            report["failed"] += 1
        if out.get("platform_corrected"):
            report["platform_corrected"] += 1
        if out.get("title_limited"):
            report["title_limited"].append(out["content_id"])
        taken.add(str(out["new_filename"]).lower())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
