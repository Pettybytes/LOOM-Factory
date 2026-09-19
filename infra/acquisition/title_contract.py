"""Human-readable title + filename contract (shared by every platform).

Titles are for humans; IDs and hashes preserve machine identity. The two never
trade places: a stable content id is never replaced by a title, and a title is
never invented to fill a gap.

Distinct fields, preserved separately
-------------------------------------
``source_title``    the title/caption the platform supplied (verbatim, cleaned)
``display_title``   the cleaned human-readable title shown in Discord and in the
                    Reaction Library
``creator_name``    the human name, when known
``creator_handle``  the handle/username, when known
``content_id``      the stable platform identity (never derived from a title)
``asset_filename``  the derived, human-readable filename

Title priority
--------------
1. platform-provided title
2. platform-provided caption or clip name
3. existing stored source metadata
4. creator plus a concise content-type label
5. ``untitled-clip`` only when no meaningful metadata exists

A *placeholder* title (a generic label that only restates the content id, e.g.
``TikTok video #123`` or ``Video by someone``) is NOT meaningful metadata: it is
rejected by priority 1-3 and falls through to priority 4, which is both truthful
and more useful. No AI model or paid provider is ever called to invent a title.

Filename contract
-----------------
    platform__creator__short-title__content-id__short-hash.ext
    platform__creator__short-title__content-id__short-hash__poster.jpg
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

MAX_TITLE_SLUG = 60
HASH_MIN = 8
HASH_MAX = 12
FALLBACK_TITLE = "untitled-clip"
MAX_FILENAME_BYTES = 250          # conservative cross-platform path limit

#: Portable filesystem reserved device names (case-insensitive).
RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

#: Concise content-type labels used by priority 4 (creator + label).
PLATFORM_LABELS: dict[str, str] = {
    "tiktok": "TikTok video",
    "instagram": "Instagram Reel",
    "youtube": "YouTube video",
    "twitch": "Twitch clip",
    "kick": "Kick clip",
}

CONTENT_KIND_LABELS: dict[str, str] = {
    "clip": "clip",
    "vod": "VOD",
    "video": "video",
    "reel": "Reel",
    "slideshow": "slideshow",
}

_SUBSEP_RE = re.compile(r"[\\/]+")          # path separators
_UNSAFE_RE = re.compile(r"[^a-z0-9]+")
_DOTS_RE = re.compile(r"^[.\s_-]+|[.\s_-]+$")

#: Generic labels that merely restate identity — not meaningful metadata.
_PLACEHOLDER_PATTERNS = (
    re.compile(r"^video by \S+$", re.I),
    re.compile(r"^tiktok video #?\d+$", re.I),
    re.compile(r"^tiktok$", re.I),
    re.compile(r"^instagram (reel|video)$", re.I),
    re.compile(r"^untitled( clip| video)?$", re.I),
    re.compile(r"^clip$", re.I),
)


class TitleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def clean_title(raw: Any) -> str:
    """Normalize a title for DISPLAY: no control chars, collapsed whitespace.

    Unicode formatting/control characters are dropped (they can reorder or hide
    text in a UI), newlines and tabs become single spaces, and leading/trailing
    dots, spaces and underscores are removed.
    """
    if raw is None:
        return ""
    text = str(raw)
    text = "".join(
        " " if ch in "\r\n\t" else ch
        for ch in text
        if unicodedata.category(ch)[0] != "C" or ch in "\r\n\t")
    text = _SUBSEP_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _DOTS_RE.sub("", text).strip()


def is_placeholder_title(title: str, content_id: str = "") -> bool:
    """True when a title carries no information beyond identity."""
    text = clean_title(title)
    if not text:
        return True
    if content_id and text.strip() == str(content_id).strip():
        return True
    if content_id and str(content_id) in text and len(text.split()) <= 4:
        # e.g. "TikTok video #123456789" — identity with a generic wrapper.
        stripped = text.replace(str(content_id), "").replace("#", "")
        if not _UNSAFE_RE.sub("", stripped.lower()).strip("-"):
            return True
    return any(p.match(text) for p in _PLACEHOLDER_PATTERNS)


def clean_creator(raw: Any) -> str:
    """Normalize a creator name/handle WITHOUT stripping its punctuation.

    A handle's trailing underscore (``coco_franklin_``) is part of the identity,
    so creator values keep it — only control characters, path separators and
    whitespace runs are normalized.
    """
    if raw is None:
        return ""
    text = str(raw)
    text = "".join(
        " " if ch in "\r\n\t" else ch
        for ch in text
        if unicodedata.category(ch)[0] != "C" or ch in "\r\n\t")
    text = _SUBSEP_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_label(platform: str, kind: str = "") -> str:
    """Concise label for priority 4; empty when the platform is unknown.

    An empty label lets the caller fall through to ``untitled-clip`` instead of
    inventing a content type for a platform we know nothing about.
    """
    base = PLATFORM_LABELS.get((platform or "").lower(), "")
    if not base:
        return ""
    if kind and kind in CONTENT_KIND_LABELS:
        return f"{base} ({CONTENT_KIND_LABELS[kind]})"
    return base


def resolve_display_title(*, platform: str, content_id: str = "",
                          source_title: str = "", caption: str = "",
                          stored_title: str = "", clip_name: str = "",
                          creator_name: str = "", creator_handle: str = "",
                          kind: str = "") -> str:
    """Build the display title using the documented priority order."""
    for candidate in (source_title, caption, clip_name, stored_title):
        text = clean_title(candidate)
        if text and not is_placeholder_title(text, content_id):
            return text[:200]
    # The human NAME is preferred over the handle: it reads better, and a handle
    # can be an opaque token. Both are still stored distinctly.
    creator = clean_creator(creator_name) or clean_creator(creator_handle)
    if creator:
        label = content_label(platform, kind)
        return clean_title(f"{creator} · {label}" if label else creator)[:200]
    label = content_label(platform, kind)
    return clean_title(label) or FALLBACK_TITLE


def slug(raw: Any, *, limit: int = MAX_TITLE_SLUG, fallback: str = "") -> str:
    """Lowercase, filesystem-safe slug: [a-z0-9-] only, no separators or dots."""
    text = clean_title(raw).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Apostrophes are elided, not turned into separators: "It's" -> "its", never
    # "it-s". Quotes and similar marks are dropped for the same reason.
    text = re.sub(r"['\u2019\u2018\u02bc\"\u201c\u201d`]", "", text)
    text = _SUBSEP_RE.sub("-", text)
    text = _UNSAFE_RE.sub("-", text).strip("-")
    if len(text) > limit:
        cut = text[:limit]
        if "-" in cut:
            head = cut.rsplit("-", 1)[0]
            cut = head if len(head) >= limit // 2 else cut
        text = cut.strip("-")
    text = _DOTS_RE.sub("", text).strip("-")
    if text.lower() in RESERVED_NAMES:
        text = f"{text}-asset"
    return text or fallback


def short_hash(digest: str, *, length: int = HASH_MAX) -> str:
    """Deterministic hash suffix, honouring the 8-12 character contract."""
    value = re.sub(r"[^0-9a-f]", "", str(digest or "").lower())
    if len(value) < HASH_MIN:
        raise TitleError("BAD_DIGEST", "a sha256 digest is required for the hash suffix")
    return value[:max(HASH_MIN, min(HASH_MAX, length))]


def build_filename(*, platform: str, creator: str, title: str, content_id: str,
                   digest: str, ext: str = "mp4") -> str:
    """``platform__creator__short-title__content-id__short-hash.ext``.

    The complete content id is preserved; only the title portion is shortened.
    """
    ext = re.sub(r"[^a-z0-9]", "", str(ext or "mp4").lower()) or "mp4"
    platform_slug = slug(platform, limit=24, fallback="item")
    creator_slug = slug(creator, limit=40)
    title_slug = slug(title, limit=MAX_TITLE_SLUG, fallback=FALLBACK_TITLE)
    id_slug = slug(content_id, limit=64, fallback="noid")
    # The creator segment is present only when a creator is actually known: an
    # "unknown" placeholder token would be noise, not information.
    head = [platform_slug] + ([creator_slug] if creator_slug else [])
    name = "__".join(head + [title_slug, id_slug, short_hash(digest)])
    if len(name.encode()) > MAX_FILENAME_BYTES - 12:
        # Shorten the title first, then the creator — never the id or the hash.
        over = len(name.encode()) - (MAX_FILENAME_BYTES - 12)
        title_slug = title_slug[:max(8, len(title_slug) - over)].strip("-") or FALLBACK_TITLE
        name = "__".join(head + [title_slug, id_slug, short_hash(digest)])
    return f"{name}.{ext}"


def poster_filename(video_filename: str, ext: str = "jpg") -> str:
    """Poster keeps the video's base name, plus ``__poster`` — never a new stem."""
    stem = str(video_filename).rsplit(".", 1)[0] if "." in str(video_filename) \
        else str(video_filename)
    return f"{stem}__poster.{ext}"


def disambiguate(filename: str, taken: "set[str] | frozenset[str]") -> str:
    """Deterministic collision resolution.

    Extends the hash suffix first (8 -> 10 -> 12 characters is a wider sample of
    the same digest), then appends a stable counter. The same inputs always
    produce the same result, so a migration is reproducible.
    """
    taken_l = {str(t).lower() for t in (taken or ())}
    if filename.lower() not in taken_l:
        return filename
    stem, _, ext = filename.rpartition(".")
    parts = stem.split("__")
    if len(parts) >= 5:
        digest_char = parts[-1]
        for width in (10, 12):
            if len(digest_char) < width:
                candidate = "__".join(parts[:-1] + [digest_char[:width]]) + f".{ext}"
                if candidate.lower() not in taken_l:
                    return candidate
    counter = 2
    while True:
        counter += 1
        candidate = f"{stem}-{counter}.{ext}"
        if candidate.lower() not in taken_l:
            return candidate
        if counter > 9999:
            raise TitleError("COLLISION_UNRESOLVED", f"cannot place {filename!r}")


def title_fields(*, platform: str, content_id: str, digest: str, creator_name: str = "",
                 creator_handle: str = "", source_title: str = "", caption: str = "",
                 stored_title: str = "", clip_name: str = "", kind: str = "",
                 ext: str = "mp4", taken: "set[str] | frozenset[str] | None" = None
                 ) -> dict[str, Any]:
    """The complete durable title record for one asset (including its filename)."""
    display = resolve_display_title(
        platform=platform, content_id=content_id, source_title=source_title,
        caption=caption, stored_title=stored_title, clip_name=clip_name,
        creator_name=creator_name, creator_handle=creator_handle, kind=kind)
    creator = clean_creator(creator_name) or clean_creator(creator_handle)
    filename = build_filename(platform=platform, creator=creator, title=display,
                              content_id=content_id, digest=digest, ext=ext)
    if taken:
        filename = disambiguate(filename, taken)
    return {
        "source_title": clean_title(source_title) or clean_title(caption),
        "display_title": display,
        "creator_name": clean_creator(creator_name),
        "creator_handle": clean_creator(creator_handle),
        "content_id": str(content_id),
        "asset_filename": filename,
        "poster_filename": poster_filename(filename),
        "title_source": ("platform" if clean_title(source_title)
                         and not is_placeholder_title(source_title, content_id)
                         else "derived"),
        # "title limited" == no usable platform title existed, so the display title
        # had to be derived. That is reported, never hidden.
        "title_limited": not (clean_title(source_title)
                              and not is_placeholder_title(source_title, content_id)),
    }
