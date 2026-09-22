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
    enable_fire_gate: bool = True


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
    evidence_depth: str = ""
    market_gate_passed: bool = False
    market_evidence: dict | None = None
    tenant_fit_verdict: str = ""
    tenant_fit_reasons: tuple[str, ...] = ()
    fire_passed: bool = False
    fire_reasons: tuple[str, ...] = ()
    content_score_recalibrated: int | None = None
    evidence_confidence: int | None = None
    moment_evidence: dict | None = None
    moment_candidate: dict | None = None
    inspection_attempts: tuple = ()
    source_mode: str = ""
    moment_zones: tuple = ()
    selected_zone: dict | None = None
    clip_start_plan: dict | None = None
    rejection_reason: str = ""


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


def _record_duration(rec: Any) -> float | None:
    """Source duration when the platform exposes it (drives source_mode)."""
    for attr in ("duration_seconds", "duration", "source_duration"):
        v = getattr(rec, attr, None)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    m = getattr(rec, "observed_metrics", None) or {}
    for k in ("duration", "duration_seconds", "length_seconds"):
        v = m.get(k) if isinstance(m, dict) else None
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


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


def production_review_queue(selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Source Review receives FIRE-PASSING candidates ONLY.

    Non-FIRE candidates stay internally available for analytics, query yield and
    learning; Bryan's producer inbox is not the junk filter.
    """
    return [s for s in selection_queue(selections) if isinstance(s, dict) and s.get("fire_passed")]


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

    def _apply_fire(self, c: Any, rec: Any, *, segments: Any = None,
                    model_timestamps: Any = None) -> None:
        """Moment location -> clip start -> bounded inspection -> confidence -> FIRE.

        Long-form sources are SCANNED for candidate moments and inspected around
        the located moment; discrete short-form sources skip the locator entirely.

        Reuses the EXISTING contracts end to end: the tenant-fit + market results
        already computed, the canonical MomentCandidate, the deterministic
        assessors, and the single FIRE gate. No new model stage.
        """
        from .fire import evidence_confidence, fire_gate, recalibrate_content_score, selection_score
        from .moment_candidate import from_market_and_market_evidence
        from .moment_inspection import (assess_activity, assess_escalation, assess_payoff,
                                        assess_reaction_angle, assess_setup, assess_stop,
                                        inspect_bounded, inspect_with_escalation,
                                        populate_from_inspection)
        from .platform_metrics import normalize_market_evidence
        from .tenant_fit import tenant_fit as _tenant_fit

        market_ev = normalize_market_evidence(rec)
        fit = _tenant_fit(title=getattr(rec, "title", "") or "",
                          creator=getattr(rec, "creator_channel_identity", "") or "")
        mc = from_market_and_market_evidence(rec, market_ev, tenant_fit_result=fit)

        # ---- WHOLE-SOURCE MOMENT LOCATION --------------------------------
        # Long-form: scan the WHOLE source cheaply, rank zones, promote the best,
        # optimise where the clip should BEGIN. Short-form: the source IS the
        # moment, so the locator is deliberately bypassed.
        from .clip_start import optimize_clip_start
        from .moment_locator import locate_moments, promote_zones, source_mode

        duration = _record_duration(rec)
        mode = source_mode(duration)
        zones = locate_moments(segments or [], source_id=str(getattr(rec, "platform_content_id", "")),
                               source_url=str(getattr(rec, "canonical_url", "")),
                               source_duration=duration, model_timestamps=model_timestamps)
        promoted = promote_zones(zones, keep=1)
        zone = promoted[0] if promoted else None
        plan = None
        inspect_start = 0.0
        if zone is not None:
            plan = optimize_clip_start(zone.event_timestamp, segments or [],
                                       duration_hint=duration)
            inspect_start = plan.recommended_start
        c.source_mode = mode
        c.moment_zones = tuple(z.as_dict() for z in zones)
        c.selected_zone = zone.as_dict() if zone is not None else None
        c.clip_start_plan = plan.as_dict() if plan is not None else None

        # TRUE bounded inspection: 3s, extended to 8s then 12s only while the
        # decision is ambiguous, FROM THE CHOSEN CLIP START. Never the full source.
        from .bounded_preview import fetch_bounded_preview
        from .multimodal_signals import analyze_media_signals

        def _decide(e: Any) -> str:
            decisive = (assess_stop(e).passed and assess_activity(e).passed
                        and assess_payoff(e).passed)
            return "decisive" if decisive else "ambiguous"

        ev, attempts = inspect_with_escalation(
            rec,
            fetch_preview=getattr(self, "_inspection_preview", fetch_bounded_preview),
            analyze_signals=getattr(self, "_inspection_analyze", analyze_media_signals),
            decide=_decide,
            fetch_captions=getattr(self, "_inspection_captions", fetch_transcript),
            start=inspect_start,
        )
        c.inspection_attempts = attempts
        a = {"stop": assess_stop(ev), "activity": assess_activity(ev), "setup": assess_setup(ev),
             "escalation": assess_escalation(ev), "payoff": assess_payoff(ev),
             "reaction_angle": assess_reaction_angle(ev)}
        populate_from_inspection(mc, ev, a)

        reaction_ok = bool(a["reaction_angle"].passed)
        payoff_ok = bool(a["payoff"].passed)
        hook_ok = bool(a["stop"].passed)
        conf_ev = ev.as_confidence_evidence(immediate_hook=hook_ok, payoff=payoff_ok,
                                            reaction_angle=mc.moment_evidence.get("reaction_angle") or "")
        # The market evidence this candidate already carries IS confidence
        # evidence; it must not be omitted, or a fully-inspected candidate would
        # be scored as if its metrics were missing.
        conf_ev["market_metrics"] = market_ev.views is not None
        conf_ev["age"] = market_ev.age_hours is not None
        conf_ev["engagement"] = market_ev.engagement is not None
        conf = evidence_confidence(platform=rec.platform, evidence=conf_ev)
        content = recalibrate_content_score(c.content_worthiness, conf_ev)
        sel = selection_score(market=int(c.market_score or 0), content=content,
                              tenant_fit_verdict=c.tenant_fit_verdict,
                              reaction_opportunity=reaction_ok,
                              evidence_completeness=conf.score,
                              duplicate_risk=False)
        decision = fire_gate(
            market_score=int(c.market_score or 0), content_score=content, selection=sel,
            confidence=conf.score,
            market_gate_pass=bool(c.market_gate_passed),
            tenant_fit_pass=(c.tenant_fit_verdict == "PASS"),
            immediate_hook=hook_ok,
            action_activity=bool(a["activity"].passed),
            payoff_present=payoff_ok,
            duplicate_risk=False,
            reaction_opportunity=reaction_ok,
        )
        c.fire_passed = decision.passed
        c.fire_reasons = decision.reasons
        c.content_score_recalibrated = content
        c.evidence_confidence = conf.score
        c.moment_evidence = dict(mc.moment_evidence)
        c.moment_candidate = mc.to_dict()
        c.evidence_depth = mc.evidence_depth

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

        # 2.5 DETERMINISTIC MARKET GATE — runs BEFORE any expensive work.
        # A market failure must not reach transcript retrieval, visual
        # inspection, model scoring, or Source Review. The reason is always
        # persisted so a rejection is explainable, never silent.
        from .platform_metrics import normalize_market_evidence, platform_market_gate
        market_ev = normalize_market_evidence(rec)
        decision = platform_market_gate(market_ev)
        c.market_gate_passed = decision.passed
        c.market_evidence = decision.evidence
        if not decision.passed:
            c.status = "filtered_market"
            c.rejection_reason = ",".join(decision.reasons)
            return c

        # 2.6 EARLY TENANT FIT — cheap metadata only, still BEFORE expensive work.
        # Ambiguous candidates PROCEED: only a clear mismatch stops the pipeline,
        # because inventing a rejection is worse than spending inspection budget.
        from .tenant_fit import tenant_fit
        fit = tenant_fit(title=getattr(rec, "title", "") or "",
                         description=getattr(rec, "body_excerpt", "") or "",
                         creator=getattr(rec, "creator_channel_identity", "") or "")
        c.tenant_fit_verdict = fit.verdict
        c.tenant_fit_reasons = tuple(fit.reasons)
        if fit.failed:
            c.status = "filtered_tenant"
            c.rejection_reason = ",".join(fit.reasons)
            return c

        # 3. Transcript-only retrieval.
        try:
            segments, ttype = fetch_transcript(rec.canonical_url, self.config.transcript_languages)
        except Exception as e:
            c.status = "error"
            c.error = f"transcript fetch: {type(e).__name__}"
            return c
        if not segments:
            # Platform Evidence Resolver: no legitimate text track exists for this
            # platform/source. Assess from METADATA ONLY instead of discarding a
            # qualified candidate. Evidence depth is recorded honestly and the
            # model is told there is no transcript; the response contract is
            # identical, so downstream intelligence stays common.
            c.transcript_type = TRANSCRIPT_NONE
            transcript_text = ""
            c.evidence_depth = "metadata_only"
        else:
            c.transcript_type = ttype
            transcript_text = " ".join(s["text"] for s in segments)
            c.evidence_depth = "transcript"

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
            ev = Evidence(
                platform=rec.platform, content_type="video", title=rec.title,
                description=getattr(rec, "body_excerpt", "") or "",
                body_excerpt=getattr(rec, "body_excerpt", "") or "",
                transcript_text=transcript_text, transcript_availability=ttype,
                signal_windows=signal_windows,
                market_metrics=getattr(rec, "observed_metrics", {}) or {},
                thumbnail_available=bool(getattr(rec, "thumbnail_reference", "")
                                         or getattr(rec, "thumbnail_available", False)),
                thumbnail_reference=getattr(rec, "thumbnail_reference", "") or "")
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
        # 5. BOUNDED MOMENT INSPECTION -> grounded assessments -> EVIDENCE
        # CONFIDENCE -> FIRE. Only candidates that already passed Market and
        # Tenant Fit reach this point. A FIRE failure records explicit reasons
        # and the candidate is never carded.
        if self.config.enable_fire_gate:
            self._apply_fire(c, rec, segments=segments,
                            model_timestamps=getattr(assessment, "candidate_timestamps", None))
        return c


def _load_provider_key(provider_name: str) -> str:
    from .providers import load_key
    if provider_name == "gemini":
        return load_key("LOOM_GEMINI_KEY")
    if provider_name == "qwen":
        return load_key("LOOM_OPENROUTER_API_KEY")
    return ""
