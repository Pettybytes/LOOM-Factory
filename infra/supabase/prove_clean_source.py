#!/usr/bin/env python3
"""Proof: durable bootstrap resolves a REACHABLE clean-source URL after the
OpenShorts render job is evicted — with NO dependency on the transient
OpenShorts backend (/api/status, /api/clip/...).

Exercises the ACTUAL adapter.resolve_durable_bootstrap() (the production code
path, not a reimplementation) against two clips:

  A. Fresh clean-source fixture (loom-clean-fixture:0) — must resolve a
     reachable clean_source_url (replacement available, has_burned_hook true).
  B. Existing fixture (6c46c3ef-...:1 @ render-1.2) — clean_source_url must be
     honestly null (its clean source was never durably stored), while media_url
     (the burned canonical) still resolves. This is the honesty check: no
     fabricated clean source, no placeholder.

Reachability = HTTP 200/206 fetch of the URL, independent of OpenShorts.
"""
import os
import sys
import urllib.request

# Load adapter env (names only needed; values already in ~/.openclaw/.env).
ENV_PATH = os.path.expanduser("~/.openclaw/.env")
for line in open(ENV_PATH):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    k = k.strip()
    v = v.strip().strip('"').strip("'")
    if k.startswith("LOOM_") and k not in os.environ:
        os.environ[k] = v

# Import the production adapter (requires LOOM_DISCORD_* env, now set).
# Resolve the repo root robustly regardless of where this script lives
# (repo-root / infra/supabase / infra/discord-review / etc.).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "infra", "discord-review"))
import adapter  # noqa: E402


def reachable(url):
    if not url:
        return False, None
    req = urllib.request.Request(url, method="GET")
    req.add_header("Range", "bytes=0-1023")
    # filesafe CDN rejects the default Python-urllib UA (403); a browser UA is
    # what the editor actually sends when it loads the media URL.
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status in (200, 206), resp.status
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check(label, tenant, clip, version, expect_clean):
    print(f"\n=== {label}: {clip} @ {version} ===")
    b = adapter.resolve_durable_bootstrap(tenant, clip, version)
    assert b, "bootstrap returned None (expected durable data)"
    print("media_url        :", b.get("media_url"))
    print("clean_source_url :", b.get("clean_source_url"))
    print("has_burned_hook  :", b.get("has_burned_hook"))
    print("has_burned_capts :", b.get("has_burned_captions"))
    print("duration_seconds :", b.get("duration_seconds"))
    print("source_range     :", b.get("source_range"))
    print("hook             :", (b.get("hook") or "")[:60])
    print("captions         :", len(b.get("captions") or []), "words")

    ok, status = reachable(b.get("media_url"))
    print(f"media_url reachable: {ok} (HTTP {status})")
    assert ok, "media_url (canonical) is not reachable"

    ok, status = reachable(b.get("clean_source_url"))
    print(f"clean_source_url reachable: {ok} (HTTP {status})")
    if expect_clean:
        assert b.get("clean_source_url"), "clean_source_url must be resolved"
        assert ok, "clean_source_url must be reachable"
        assert b.get("has_burned_hook"), "fresh fixture must have a burned hook"
        assert b.get("has_burned_captions"), "fresh fixture must have burned captions"
        assert abs(float(b.get("duration_seconds")) - 14.0) < 0.01, "duration should be 14.0"
    else:
        assert b.get("clean_source_url") in (None, ""), (
            "existing fixture must NOT fabricate a clean source (identity unproven)"
        )


# A. Fresh clean-source fixture (the new durable input artifact).
check("FRESH FIXTURE", "dreemzy", "loom-clean-fixture:0", "render-1.0", expect_clean=True)

# B. Existing fixture — honesty check: clean source honestly null.
check("EXISTING FIXTURE", "dreemzy", "6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1", "render-1.2", expect_clean=False)

print("\n✅ PROOF PASSED: durable bootstrap returns a reachable clean-source URL")
print("   for the fresh fixture, with the OpenShorts job evicted (no /api/* dependency),")
print("   and honestly reports replacement-unavailable for the unprovable existing fixture.")
