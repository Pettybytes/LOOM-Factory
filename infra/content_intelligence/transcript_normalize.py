"""Normalize raw caption segments before ANY downstream reasoning.

Root cause this addresses: the transcript fetcher returns each caption line TWICE -
one copy carrying inline word-timing markup (``Welcome<00:00:16.720><c> back</c>``)
and one clean snapshot - interleaved with ``[Music]``/``[Laughter]`` filler. Feeding
that into sentence-boundary detection, anchor discovery, the completeness gate and
evidence scoring corrupts all four: a line ending in ``</c>`` is not a sentence end,
and a first-3s window full of ``[Music]`` yields no hook evidence at all.
"""

from __future__ import annotations

import html
import re
from typing import Any, Iterable

_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"\s+")
_FILLER = re.compile(r"^\[(music|laughter|applause|silence|inaudible|sound|noise)[^\]]*\]$",
                     re.IGNORECASE)
_LEAD = re.compile(r"^\s*>>\s*")


def clean_text(text: str) -> str:
    """Decode entities, strip word-timing markup, collapse whitespace."""
    s = html.unescape(str(text or ""))      # &gt;&gt; -> >>, &amp; -> &
    s = _TAG.sub("", s)
    s = _LEAD.sub("", s)
    return _WS.sub(" ", s).strip()


def is_filler(text: str) -> bool:
    return not text or bool(_FILLER.match(text.strip()))


def normalize_transcript_segments(segments: Iterable[dict[str, Any]] | None
                                  ) -> list[dict[str, Any]]:
    """Return deduplicated, markup-free, monotonic segments.

    The doubled-caption shape is: a markup segment spanning real time, then a short
    clean snapshot of the same words. Those are merged (keeping the covered span and
    the longer text) rather than both being kept as if they were separate speech.
    """
    out: list[dict[str, Any]] = []
    for raw in (segments or []):
        if not isinstance(raw, dict):
            continue
        try:
            st = float(raw.get("start_s", 0.0) or 0.0)
            en = float(raw.get("end_s", st) or st)
        except (TypeError, ValueError):
            continue
        text = clean_text(raw.get("text"))
        if is_filler(text):
            continue
        if en < st:
            st, en = en, st
        if out:
            prev = out[-1]
            ptext = prev["text"]
            overlap = (text in ptext) or (ptext in text)
            if overlap or (st <= prev["end_s"] + 0.05 and text == ptext):
                # same words repeated: keep one span covering both
                if len(text) > len(ptext):
                    prev["text"] = text
                prev["end_s"] = max(prev["end_s"], en)
                continue
            if st < prev["start_s"]:
                st = prev["start_s"]
        out.append({"start_s": round(st, 3), "end_s": round(max(en, st), 3), "text": text})
    return out


def transcript_text(segments: Iterable[dict[str, Any]] | None) -> str:
    return " ".join(s["text"] for s in normalize_transcript_segments(segments))
