"""Authorized-session support for Reddit acquisition — value-safe by construction.

Bryan is already signed in to Reddit in Chrome. This module lets the local
acquisition path borrow that session for content that genuinely requires it,
without ever handling a credential.

Hard rules, enforced here rather than by convention:

* **Public first.** Authentication is used only when access genuinely requires it —
  i.e. when the classified evidence is an age/auth gate. Never pre-emptively.
* **Host keys only.** Profile discovery reads cookie HOST KEYS and row COUNTS. It
  never reads a cookie name, value, or encrypted blob.
* **No passwords, ever.** No password is requested, stored, logged, or typed.
* **No cookie material leaves the machine.** Cookies are handed to `yt-dlp`
  in-process via `--cookies-from-browser`; LOOM never writes a cookie file, never
  prints cookie values, and never puts cookies in a dataset, Discord card, or
  Apify payload.
* **Redaction.** Anything this module returns for logging passes through `redact()`.
* **One instruction if blocked.** If cookie access needs Bryan's approval, the
  module states exactly one command to run — it does not improvise.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

BROWSER = "chrome"
CHROME_ROOT = Path.home() / "Library/Application Support/Google/Chrome"
DEFAULT_HOST = "reddit.com"

#: yt-dlp flag this module relies on. Never a `--cookies FILE`.
COOKIE_FLAG = "--cookies-from-browser"

#: Classifications that legitimately require an authenticated session.
AUTH_REQUIRED_CODES = frozenset({"AGE_GATE_AUTH_REQUIRED"})

REDACTED = "[redacted]"

#: Patterns for session-bearing strings. Redaction is aggressive on purpose: a
#: false positive costs a little log clarity, a false negative leaks a session.
_SECRET_PATTERNS = (
    re.compile(r"(?i)(reddit_session|session|token_v2|csrf_token|auth|bearer)"
               r"\s*[=:]\s*\S+"),
    re.compile(r"(?i)\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
    re.compile(r"(?i)location:\s*\S+"),
    re.compile(r"(?i)set-cookie:\s*\S+"),
)


def redact(text: str) -> str:
    """Strip session-bearing material from anything destined for output."""
    out = str(text or "")
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def chrome_root() -> Path:
    return Path(os.environ.get("LOOM_CHROME_ROOT") or CHROME_ROOT)


def list_profiles() -> list[str]:
    """Profile DIRECTORY names only (Default, Profile 1, ...). No cookie data."""
    root = chrome_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and (d / "Cookies").is_file())


def profile_cookie_hosts(profile: str, *, pattern: str = "%reddit%") -> dict:
    """Host keys + counts for one profile. VALUE-FREE by construction.

    Opens the cookie database read-only and immutable; the query selects
    ``host_key`` and ``COUNT(*)`` only, so no name/value/encrypted blob is ever
    materialized. Works while Chrome is running because nothing is written.
    """
    db = chrome_root() / profile / "Cookies"
    if not db.is_file():
        return {"profile": profile, "error": "no_cookie_db", "hosts": [], "rows": 0}
    try:
        con = sqlite3.connect(f"file:{db}?immutable=1", uri=True, timeout=5)
        try:
            rows = con.execute(
                "SELECT host_key, COUNT(*) FROM cookies WHERE host_key LIKE ? "
                "GROUP BY host_key ORDER BY host_key", (pattern,)).fetchall()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return {"profile": profile, "error": type(e).__name__, "hosts": [], "rows": 0}
    return {"profile": profile,
            "hosts": [r[0] for r in rows],
            "rows": sum(r[1] for r in rows)}


def find_profile(*, host: str = DEFAULT_HOST) -> str | None:
    """The FIRST profile holding a cookie for ``host``, or None.

    A host key is a domain name, not a session identifier, so this stays
    value-free while still answering "which profile is signed in?".
    """
    needle = f"%{host}%"
    for profile in list_profiles():
        info = profile_cookie_hosts(profile, pattern=needle)
        if info.get("rows"):
            return profile
    return None


def describe(*, host: str = DEFAULT_HOST) -> dict:
    """Value-free description for logs/receipts (safe to print)."""
    profile = find_profile(host=host)
    info = profile_cookie_hosts(profile, pattern=f"%{host}%") if profile else {}
    return {"browser": BROWSER, "host": host, "profile": profile,
            "profile_candidates": list_profiles(),
            "cookie_host_keys": info.get("hosts", []),
            "cookie_rows": info.get("rows", 0),
            "cookie_names_or_values_read": False,
            "password_handling": "never requested, stored, or logged"}


def needs_authenticated_session(evidence_code: str) -> bool:
    """Public first: only an explicit age/auth gate justifies the session."""
    return str(evidence_code or "") in AUTH_REQUIRED_CODES


def browser_cookie_args(*, profile: str | None = None,
                        authenticated: bool = False,
                        browser: str = BROWSER) -> list[str]:
    """yt-dlp arguments, or [] when the public path should be used.

    Cookies are consumed by yt-dlp in-process. LOOM never writes a cookie file
    and never passes a cookie value on any command line.
    """
    if not authenticated:
        return []
    spec = f"{browser}:{profile}" if profile else browser
    return [COOKIE_FLAG, spec]


def cookie_extraction_available() -> bool:
    """Whether the installed yt-dlp supports browser-cookie extraction at all."""
    import shutil
    import subprocess
    exe = shutil.which("yt-dlp")
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "--help"], capture_output=True, text=True,
                             timeout=30).stdout
    except Exception:  # noqa: BLE001
        return False
    return COOKIE_FLAG in out


def blocker_instruction(profile: str | None = None) -> str:
    """Exactly ONE instruction when a Keychain approval is required."""
    spec = f"chrome:{profile}" if profile else "chrome"
    return (f"Approve the macOS Keychain prompt for \"Chrome Safe Storage\" so yt-dlp "
            f"can read the signed-in Chrome session in-process, e.g.: "
            f"`yt-dlp --cookies-from-browser {spec} --skip-download --dump-json <reddit url>`")


def assert_no_cookie_material(payload: dict) -> dict:
    """Guard for anything leaving the machine (Discord, dataset, Apify).

    Raises if a value looks like cookie material, so a leak fails loudly instead
    of shipping.
    """
    import json as _json
    blob = _json.dumps(payload, default=str).lower()
    for marker in ("cookies-from-browser", "cookiejar", "set-cookie", "reddit_session",
                   "netscape http cookie file"):
        if marker in blob:
            raise ValueError(f"[COOKIE_MATERIAL_REFUSED] payload contains {marker!r}")
    return payload
