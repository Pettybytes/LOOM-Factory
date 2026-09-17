"""URL validation for the local acquisition helper.

Security: the helper downloads exactly one MP4 from an approved Instagram/Meta
CDN URL. Every URL (the initial candidate and EACH redirect hop) is validated:
https only, approved hostname suffix (rejects suffix-confusion lookalikes such
as ``cdninstagram.com.evil.com``), no credentials, no non-standard port.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from media_filter import is_approved_media_host


class ValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def _split(url: str):
    try:
        return urlsplit(url)
    except Exception:
        raise ValidationError("BAD_URL", "unparseable url")


def is_credential_url(url: str) -> bool:
    p = _split(url)
    return bool(p.username or p.password)


def has_non_standard_port(url: str) -> bool:
    p = _split(url)
    try:
        port = p.port
    except ValueError:
        raise ValidationError("BAD_PORT", "non-numeric port")
    return port is not None and port not in (80, 443)


def validate_media_url(url: str) -> str:
    """Validate a media URL: https + approved host + no creds + no bad port."""
    p = _split(url)
    if p.scheme != "https":
        raise ValidationError("BAD_SCHEME", "media URL must be https")
    if p.username or p.password:
        raise ValidationError("CREDENTIALS", "credential-bearing URL rejected")
    host = (p.hostname or "").lower()
    if not host:
        raise ValidationError("NO_HOST", "no host in URL")
    if not is_approved_media_host(host):
        raise ValidationError("HOST_NOT_ALLOWED", "host not in approved Meta/IG CDN allowlist")
    try:
        port = p.port
    except ValueError:
        raise ValidationError("BAD_PORT", "non-numeric port")
    if port is not None and port not in (80, 443):
        raise ValidationError("BAD_PORT", "non-standard port not allowed")
    return url


def validate_redirect(target_url: str) -> str:
    """Validate a redirect target — must stay within the approved allowlist."""
    return validate_media_url(target_url)


def validate_candidate(canonical_url: str, candidate_url: str) -> None:
    """Validate the captured candidate + canonical source pair."""
    if not (canonical_url or "").strip().startswith("https://"):
        raise ValidationError("BAD_SOURCE", "canonical source URL must be https")
    validate_media_url(candidate_url)
