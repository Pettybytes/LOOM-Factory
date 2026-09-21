"""Recurring Pass-1 Content Intelligence pipeline (production-capable, bounded).

The bounded production path:

    qualified discovery candidate
      -> Topic Intelligence (market)        [no model cost]
      -> market shortlist threshold          [skip weak market]
      -> transcript-only evidence retrieval  [yt-dlp --skip-download, 0 media bytes]
      -> Content Intelligence (real provider) [model call — shortlisted only]
      -> Combined Source Selection
      -> durable Supabase persistence         [idempotent]

Guardrails: explicit candidate/token/cost limits, hard run-level cost ceiling,
captions via yt-dlp skip-download (creator preferred), temp caption files
removed, honest state on unavailable captions, no full video download, no media
probe, no Discord, no Acquisition, no OpenShorts, no publishing, proof profiles
excluded, replay idempotent, failures never advance a candidate.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .candidate_fusion import fuse_candidates
from .contract import ContentAssessment, TRANSCRIPT_AUTO, TRANSCRIPT_CREATOR, TRANSCRIPT_NONE
from .provider import Evidence, assess_content
from .providers import ProviderError, build_provider
from .selection import compute_selection, SELECTION_POLICY_VERSION

# Outcome groups supported by the Source Review queue (selection.py outcomes).
OUTCOME_STRONG = "fast_track"
OUTCOME_STANDARD = "standard_review"
OUTCOME_EMERGING = "emerging"
OUTCOME_LOW_CONFIDENCE = "human_review_low_confidence"
OUTCOME_HOLD = "hold_hype"
OUTCOME_REJECT = "reject"
OUTCOME_DIFFERENTIATION = "differentiation_required"

QUEUE_OUTCOMES = frozenset({
    OUTCOME_STRONG, OUTCOME_STANDARD, OUTCOME_EMERGING,
    OUTCOME_LOW_CONFIDENCE, OUTCOME_HOLD, OUTCOME_REJECT, OUTCOME_DIFFERENTIATION,
})


@dataclass
class Pass1Config:
    """Bounds for a single Pass-1 run."""
    max_candidates: int = 5
    market_shortlist_threshold: int = 40      # skip market < threshold (no model cost)
    max_transcript_chars: int = 12000
    max_total_tokens: int = 40000
    cost_ceiling_usd: float = 0.05
    provider_name: str = "gemini"
    transcript_languages: str = "en.*"
    extract_multimodal_signals: bool = False


@dataclass
class Pass1CandidateResult:
    duplicate_fingerprint: str
    platform_content_id: str
    title: str
    market_score: int
    status: str = ""               # scored | skipped_market | captions_unavailable | error
    content_worthiness: int | None = None
    selection_score: int | None = None
    selection_outcome: str | None = None
    transcript_type: str = TRANSCRIPT_NONE
    cost_usd: float = 0.0
    error: str = ""
    # Multimodal signal provenance: "" (not attempted) | "disabled" |
    # "extracted:<n>" | "failed:<ExceptionType>". Recorded so a non-fatal signal
    # failure is visible in the run result instead of silently swallowed.
    signal_status: str = ""


@dataclass
class Pass1RunResult:
    candidates: list[Pass1CandidateResult] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    @property
    def scored(self) -> int:
        return sum(1 for c in self.candidates if c.status == "scored")

    def summary(self) -> dict[str, Any]:
        return {
            "processed": len(self.candidates),
            "scored": self.scored,
            "skipped_market": sum(1 for c in self.candidates if c.status == "skipped_market"),
            "captions_unavailable": sum(1 for c in self.candidates if c.status == "captions_unavailable"),
            "errors": sum(1 for c in self.candidates if c.status == "error"),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
        }


def fetch_transcript(url: str, languages: str = "en.*") -> tuple[list[dict[str, Any]], str]:
    """Retrieve English captions (creator first, else auto) via yt-dlp
    skip-download. Returns (segments, transcript_type). Removes temp files."""
    tmp = tempfile.mkdtemp(prefix="loom-pass1-caps-")
    try:
        for write_flag, ttype in (("--write-subs", TRANSCRIPT_CREATOR),
                                  ("--write-auto-subs", TRANSCRIPT_AUTO)):
            subprocess.run(
                ["yt-dlp", "--skip-download", "--no-playlist", "--no-update",
                 write_flag, "--sub-langs", languages, "--sub-format", "vtt",
                 "-o", f"{tmp}/%(id)s", url],
                capture_output=True, text=True, timeout=120,
            )
            vtts = list(Path(tmp).glob("*.vtt"))
            if vtts:
                text = vtts[0].read_text(encoding="utf-8", errors="replace")
                return _parse_vtt(text), ttype
        return [], TRANSCRIPT_NONE
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


class MediaNotFetched(RuntimeError):
    """Bounded media retrieval declined or failed, and the REASON is preserved.

    `--match-filter "duration <= 600"` refusing a longer video is CORRECT
    bounding, but returning a bare empty list made that indistinguishable from
    "analysed successfully and found no salient peaks" — the caller recorded
    `extracted:0` for both. Raising this keeps the cause visible while the call
    site still treats signal extraction as non-fatal.
    """


def fetch_media_and_signals(url: str, max_duration: int = 600) -> list:
    """Download worst-quality media to a temp file, extract deterministic
    audio/visual candidate signals, and delete the temp media. Bounded three
    ways: lowest available quality, a duration cap enforced by yt-dlp AND again
    at decode time, and a hard subprocess timeout. $0 model cost. Non-fatal at
    the call site — the caller records the failure rather than aborting."""
    import shutil
    from .multimodal_signals import analyze_media_signals

    _SIDECAR = {".json", ".vtt", ".part", ".ytdl", ".temp"}
    tmp = tempfile.mkdtemp(prefix="loom-pass1-media-")
    try:
        proc = subprocess.run(
            ["yt-dlp", "-f", "worstvideo+worstaudio/worst", "--no-playlist", "--no-update",
             "--match-filter", f"duration <= {max_duration}",
             "-o", f"{tmp}/%(id)s.%(ext)s", url],
            capture_output=True, text=True, timeout=300,
        )
        media = [p for p in Path(tmp).glob("*")
                 if p.is_file() and p.suffix.lower() not in _SIDECAR]
        if not media:
            lines = [l for l in (proc.stderr or proc.stdout or "").strip().splitlines() if l.strip()]
            reason = lines[-1][:160] if lines else f"yt-dlp exit {proc.returncode}"
            raise MediaNotFetched(reason)
        res = analyze_media_signals(str(media[0]), max_seconds=max_duration)
        return res.get("signal_windows", [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _parse_vtt(text: str) -> list[dict[str, Any]]:
    import re
    ts_re = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})")

    def to_s(ts):
        m = ts_re.search(ts)
        if not m:
            return 0.0
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0

    segs, cur = [], None
    for line in text.splitlines():
        if "-->" in line:
            if cur:
                segs.append(cur)
            a, b = line.split("-->")
            cur = {"start_s": to_s(a.strip()), "end_s": to_s(b.strip()), "text": []}
        elif cur is not None and line.strip():
            cur["text"].append(line.strip())
    if cur:
        segs.append(cur)
    for s in segs:
        s["text"] = " ".join(s["text"]).strip()
    return [s for s in segs if s["text"]]


def eligible_selection(s: dict[str, Any] | None) -> bool:
    """A candidate is eligible for the Source Review queue only when it has a
    COMPLETED combined Source Selection (score + confidence + outcome)."""
    return bool(s and s.get("outcome") in QUEUE_OUTCOMES
                and isinstance(s.get("source_selection_score"), int)
                and isinstance(s.get("source_selection_confidence"), (int, float)))


def selection_queue(selections: list[dict[str, Any]],
                    outcome_filter: set[str] | None = None) -> list[dict[str, Any]]:
    """Return only completed selections eligible for the Source Review queue."""
    out = [s for s in selections if eligible_selection(s)]
    if outcome_filter is not None:
        out = [s for s in out if s.get("outcome") in outcome_filter]
    return out


class Pass1Pipeline:
    """Bounded recurring Pass-1 pipeline."""

    def __init__(self, config: Pass1Config, brief: Any, topic_intelligence: Any,
                 provider_name: str | None = None, provider: Any = None) -> None:
        self.config = config
        self.brief = brief
        self.topic = topic_intelligence
        self.provider_name = provider_name or config.provider_name
        self.provider = provider

    def run(self, records: list[Any], store: Any) -> Pass1RunResult:
        result = Pass1RunResult()
        for rec in records[: self.config.max_candidates]:
            c = self._process(rec, store, result)
            result.candidates.append(c)
            if result.total_cost_usd >= self.config.cost_ceiling_usd:
                break  # hard run-level ceiling
        return result

    def _process(self, rec: Any, store: Any, run: Pass1RunResult) -> Pass1CandidateResult:
        c = Pass1CandidateResult(
            duplicate_fingerprint=rec.duplicate_fingerprint,
            platform_content_id=rec.platform_content_id,
            title=rec.title, market_score=0,
        )

        # 1. Topic Intelligence (market) — no model cost.
        topic = self.topic.assess(rec, self.brief, competition_count=0)
        c.market_score = topic.opportunity_score

        # 2. Market shortlist threshold.
        if c.market_score < self.config.market_shortlist_threshold:
            c.status = "skipped_market"
            return c

        # 3. Transcript-only retrieval.
        try:
            segments, ttype = fetch_transcript(rec.canonical_url, self.config.transcript_languages)
        except Exception as e:
            c.status = "error"
            c.error = f"transcript fetch: {type(e).__name__}"
            return c
        if not segments:
            c.status = "captions_unavailable"
            c.transcript_type = TRANSCRIPT_NONE
            return c
        c.transcript_type = ttype
        transcript_text = " ".join(s["text"] for s in segments)

        # 3.5 Multimodal signals (deterministic, local, opt-in, $0 model cost).
        # Signals reach the model ONLY through the Evidence bundle built below.
        # Fusion collapses overlapping signal peaks onto the one candidate-window
        # contract; at this stage no transcript window exists yet (the model has not
        # run), so fusion here is the dedup step, not a transcript merge.
        signal_windows: list = []
        if not self.config.extract_multimodal_signals:
            c.signal_status = "disabled"
        else:
            try:
                raw_windows = fetch_media_and_signals(rec.canonical_url)
                signal_windows = fuse_candidates([], raw_windows)
                c.signal_status = f"extracted:{len(signal_windows)}"
            except MediaNotFetched as e:
                # Bounded out or unavailable — the reason is recorded, so this is
                # never confused with "extracted and found nothing salient".
                signal_windows = []
                c.signal_status = f"not_fetched:{str(e)[:80]}"
            except Exception as e:
                # Non-fatal, and deliberately not silent: a transcript-only
                # assessment still proceeds and the failure is recorded.
                signal_windows = []
                c.signal_status = f"failed:{type(e).__name__}"

        # 4. Content Intelligence (model call — shortlisted only).
        try:
            provider = self.provider or build_provider(self.provider_name, _load_provider_key(self.provider_name))
            ev = Evidence(platform=rec.platform, content_type="video", title=rec.title,
                          transcript_text=transcript_text, transcript_availability=ttype,
                          signal_windows=signal_windows)
            raw = provider.assess(ev, self.brief)
            assessment = assess_content(
                record=rec, brief=self.brief, evidence_bundle=ev, provider=provider,
                competition_count=0, raw=raw,
            )
            # Persist model identity + usage (from the real provider's extra fields).
            ident = raw.get("model_identity") or {}
            usage = raw.get("usage") or {}
            assessment.model_provider = ident.get("provider", assessment.model_provider)
            assessment.model_version = ident.get("resolved_model") or ident.get("resolved_version") or assessment.model_version
            assessment.raw_model_reference = __import__("json").dumps(ident, sort_keys=True)
        except ProviderError as e:
            c.status = "error"
            c.error = f"provider: {e.code}"
            return c
        except Exception as e:
            c.status = "error"
            c.error = f"assess: {type(e).__name__}"
            return c

        # Track tokens + cost.
        usage = raw.get("usage") or {}
        run.total_input_tokens += int(usage.get("input_tokens", 0))
        run.total_output_tokens += int(usage.get("output_tokens", 0))
        c.cost_usd = float(usage.get("cost_usd", 0.0))
        run.total_cost_usd += c.cost_usd

        # 5. Combined Source Selection.
        sel = compute_selection(
            duplicate_fingerprint=rec.duplicate_fingerprint, tenant_id=rec.tenant_id,
            profile_id=rec.profile_id, platform=rec.platform,
            market_opportunity_score=topic.opportunity_score, market_confidence=topic.confidence,
            content_worthiness_score=assessment.content_worthiness_score,
            content_confidence=assessment.content_confidence,
            tenant_format_fit_score=assessment.scores.get("tenant_fit", 0),
            provenance_usability_score=80,
            recommended_format=assessment.recommended_content_format,
            recommended_angle=assessment.recommended_angle,
            suggested_timestamps=assessment.candidate_timestamps,
        )

        # 6. Persist (idempotent) + failures never advance.
        store.upsert_assessment(assessment)
        store.upsert_selection(sel)

        c.status = "scored"
        c.content_worthiness = assessment.content_worthiness_score
        c.selection_score = sel.source_selection_score
        c.selection_outcome = sel.outcome
        return c


def _load_provider_key(provider_name: str) -> str:
    from .providers import load_key
    if provider_name == "gemini":
        return load_key("LOOM_GEMINI_KEY")
    if provider_name == "qwen":
        return load_key("LOOM_OPENROUTER_API_KEY")
    return ""
