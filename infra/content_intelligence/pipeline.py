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
    processing_eligibility: str = ""
    connector_name: str = ""
    # Identity carried for the authoritative persisted record.
    tenant_id: str = ""
    profile_id: str = ""
    platform: str = ""
    canonical_url: str = ""
    publication_timestamp: Any = None
    creator_channel_identity: str = ""
    idempotency_key: str = ""
    scoring_policy_version: str = ""
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
    source_relative_rank: int | None = None
    source_candidate_count: int = 0
    duplicate_state: str = ""
    rank_evidence: dict | None = None
    source_mode: str = ""
    moment_zones: tuple = ()
    moment_type: dict | None = None
    boundary_plan: dict | None = None
    completeness: dict | None = None
    repair_attempts: tuple = ()
    selected_zone: dict | None = None
    clip_start_plan: dict | None = None
    rejection_reason: str = ""
    media_assessment_status: str = ""
    media_assessment: dict | None = None
    market_score_final: int | None = None


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
    from .bounded_preview import _auth_args      # cookies/UA args shared with the preview path
    from .multimodal_signals import analyze_media_signals

    _SIDECAR = {".json", ".vtt", ".part", ".ytdl", ".temp"}
    tmp = tempfile.mkdtemp(prefix="loom-pass1-media-")
    try:
        proc = subprocess.run(
            ["yt-dlp", "-f", "worstvideo+worstaudio/worst", "--no-playlist", "--no-update",
             "--match-filter", f"duration <= {max_duration}",
             *_auth_args(),
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


def apply_run_level_ranking(candidates: list[Any], *, overlap_threshold: float = 0.6,
                             gap_s: float = 6.0, strict: bool = False,
                             _scoped: bool = False) -> list[Any]:
    """RUN-LEVEL pass: within-source relative ranking + dedup/diversity.

    Reuses the existing moment_quality ranking. Absolute FIRE stays mandatory and is
    untouched: this only ORDERS surviving candidates and suppresses near-identical
    versions of the SAME event, preserving lineage rather than deleting rows.
    """
    from .moment_quality import RankInput, dedup, relative_rank

    if strict and not _scoped:
        groups = {}
        for c in candidates:
            if c.fire_passed and (c.completeness or {}).get("completeness_verdict") == "PASS":
                groups.setdefault((c.tenant_id, c.profile_id), []).append(c)
        return [r for group in groups.values() for r in apply_run_level_ranking(
            group, overlap_threshold=overlap_threshold, gap_s=gap_s, strict=True, _scoped=True)]

    inputs: list[Any] = []
    by_fp: dict[str, Any] = {}
    for c in candidates or []:
        plan = getattr(c, "boundary_plan", None) or {}
        me = getattr(c, "moment_evidence", None) or {}
        comp = getattr(c, "completeness", None) or {}
        mt = (getattr(c, "moment_type", None) or {}).get("moment_type", "unknown")
        start = plan.get("recommended_clip_start")
        end = plan.get("recommended_clip_end")
        fp = f"{getattr(c, 'platform_content_id', '?')}|{start}|{end}"
        if strict:
            fp = (f"{c.tenant_id}|{c.profile_id}|{c.platform}|{fp}|"
                  f"{plan.get('event_timestamp')}|{getattr(c, 'duplicate_fingerprint', '')}")
        inputs.append(RankInput(
            fingerprint=fp,
            event_timestamp=float(plan.get("event_timestamp") or 0.0),
            moment_type=mt,
            market=int(getattr(c, "market_score", None) or 0),
            content=int(getattr(c, "content_score_recalibrated", None) or 0),
            selection=int(getattr(c, "selection_score", None) or 0),
            confidence=int(getattr(c, "evidence_confidence", None) or 0),
            hook=bool(me.get("hook_0_3s")),
            activity=me.get("action_activity") is not None,
            payoff=bool(me.get("payoff")),
            completeness=(comp.get("completeness_verdict") == "PASS"),
            reaction_angle=bool(me.get("reaction_angle")),
            overlap_start=(float(start) if isinstance(start, (int, float)) else None),
            overlap_end=(float(end) if isinstance(end, (int, float)) else None),
        ))
        by_fp[fp] = c

    if strict:
        # Absolute source timestamps are comparable only WITHIN one source.
        # A 0–8s moment on source A cannot duplicate 0–8s on unrelated source B.
        rankings = relative_rank(inputs)
        by_source = {}
        for item in inputs:
            candidate = by_fp[item.fingerprint]
            by_source.setdefault((candidate.platform, candidate.platform_content_id), []).append(item)
        states = {r.fingerprint: r for group in by_source.values()
                  for r in dedup(group, overlap_threshold=overlap_threshold, gap_s=gap_s)}
        for r in rankings:
            local = states[r.fingerprint]
            r.duplicate_state = local.duplicate_state
            r.rank_evidence.update({k: v for k, v in local.rank_evidence.items()
                                   if k in {"suppressed_by", "overlap_ratio"}})
    else:
        rankings = dedup(inputs, overlap_threshold=overlap_threshold, gap_s=gap_s)
    for r in rankings:
        c = by_fp.get(r.fingerprint)
        if c is None:
            continue
        c.source_relative_rank = r.source_relative_rank
        c.source_candidate_count = r.source_candidate_count
        c.duplicate_state = r.duplicate_state
        c.rank_evidence = r.as_dict().get("rank_evidence") or {}
    return rankings


def production_review_queue(selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Source Review receives FIRE-PASSING candidates ONLY.

    Non-FIRE candidates stay internally available for analytics, query yield and
    learning; Bryan's producer inbox is not the junk filter.
    """
    return [s for s in selection_queue(selections)
            if isinstance(s, dict) and s.get("fire_passed")
            and s.get("duplicate_state") != "DUPLICATE_SUPPRESSED"]


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
        self._media_cost_usd = 0.0

    def _media_assess(self, ev: Any, rec: Any) -> dict[str, Any] | None:
        """Run the EXISTING provider over the real bounded media (inline_data).

        Returns None when the provider cannot take media (e.g. a test double),
        so the call site records an honest status instead of pretending.
        """
        provider = self.provider or build_provider(self.provider_name,
                                                   _load_provider_key(self.provider_name))
        fn = getattr(provider, "assess_media", None)
        if fn is None:
            return None
        from .providers import _build_prompt
        caption = (ev.caption_text or ev.spoken_hook or "").strip()
        windows = [dict(t) for t in (ev.timestamps or ()) if isinstance(t, dict)]
        note = ("A bounded video/audio excerpt of the INSPECTED OPENING is attached. "
                "Assess what the media actually shows and sounds like; never invent "
                "dialogue or events the media does not contain.")
        if not caption:
            label = "NO CAPTIONS FOR THIS CLIP (REAL MEDIA ATTACHED)"
            prompt = _build_prompt("no captions available",
                                   getattr(provider, "max_transcript_chars", 12000),
                                   windows, label=label, note=note)
        else:
            label = "CAPTIONS SCOPED TO THE INSPECTED OPENING (REAL MEDIA ATTACHED)"
            prompt = _build_prompt(caption, getattr(provider, "max_transcript_chars", 12000),
                                   windows, label=label, note=note)
        requirements = getattr(self.brief, "mining_requirements", {}) or {}
        if requirements:
            from .mining_contract import inspection_prompt
            prompt += inspection_prompt(requirements, source_start=getattr(ev, "source_start", 0.0),
                                        inspected_duration=ev.window_seconds)
            # Preserve the timing evidence instead of flattening captions into
            # prose: a payoff described later cannot validate an earlier end.
            import json
            import re
            timed, previous = [], []
            for seg in getattr(ev, "caption_segments", ()):
                if float(seg.get("end_s", 0)) - float(seg.get("start_s", 0)) < .04:
                    continue  # auto-caption display refresh, not new speech
                words = re.sub(r"<[^>]+>", "", seg.get("text", "")).split()
                overlap = next((n for n in range(min(len(previous), len(words)), 0, -1)
                                if previous[-n:] == words[:n]), 0)
                spoken = words[overlap:]
                previous = words
                if spoken:
                    timed.append({"start": seg["start_s"], "end": seg["end_s"],
                                  "text": " ".join(spoken)})
            if timed:
                prompt += "\nTIMECODED CAPTION EVIDENCE (absolute source seconds): " + json.dumps(timed)[:16000]
        return fn(ev.last_artifact_path, prompt)

    @staticmethod
    def _merge_media_hints(ev: Any, parsed: dict[str, Any]) -> None:
        """Ground hook/payoff/escalation/reaction hints in what the media model
        actually observed. Thresholds are conservative: a hint only lands when
        the model is reasonably sure the opening shows it."""
        scores = parsed.get("scores") or {}

        def _s(k: str) -> int:
            v = scores.get(k)
            return int(v) if isinstance(v, (int, float)) else 0

        if _s("hook") >= 60:
            ev.provider_hints["hook"] = True
        if _s("payoff") >= 60:
            ev.provider_hints["payoff"] = True
        if _s("conflict_tension") >= 60 or _s("surprise") >= 70:
            ev.provider_hints["escalation"] = True
        if parsed.get("recommended_angle"):
            ev.provider_hints["reaction_angle"] = str(parsed["recommended_angle"])
        if isinstance(parsed.get("content_worthiness_score"), (int, float)):
            ev.provider_hints["media_model_worthiness"] = int(parsed["content_worthiness_score"])

    def run(self, records: list[Any], store: Any) -> Pass1RunResult:
        result = Pass1RunResult()
        for rec in records[: self.config.max_candidates]:
            c = self._process(rec, store, result)
            result.candidates.append(c)
            # A source can contain several independently inspected complete
            # moments. Expand before ranking/persistence, not merely in a JSON
            # evidence list which the producer inbox never ranks or displays.
            result.candidates.extend(c.__dict__.pop("_complete_moment_candidates", ()))
            if result.total_cost_usd >= self.config.cost_ceiling_usd:
                break  # hard run-level ceiling
        # RUN-LEVEL pass: relative ranking within the run + dedup/diversity. Absolute
        # FIRE is unchanged; this only orders survivors and suppresses near-identical
        # versions of the same event.
        apply_run_level_ranking(result.candidates,
                               strict=bool(getattr(self.brief, "mining_requirements", {})))
        policy = (getattr(self.brief, "mining_requirements", {}) or {}).get("review_policy") or {}
        if policy.get("mode") == "human_curated":
            options = [c for c in result.candidates if c.status == "scored"
                       and c.boundary_plan and (c.completeness or {}).get("completeness_verdict") == "PASS"
                       and c.duplicate_state != "DUPLICATE_SUPPRESSED"
                       and (c.content_score_recalibrated or 0) >= policy.get("minimum_content", 50)]
            options.sort(key=lambda c: (c.content_score_recalibrated or 0, c.selection_score or 0), reverse=True)
            seen = set()
            options = [c for c in options if not (c.duplicate_fingerprint in seen or seen.add(c.duplicate_fingerprint))]
            for rank, c in enumerate(options, 1):
                c.moment_evidence.setdefault("review_context", {}).update(
                    review_mode="human_curated", review_rank=rank, review_count=len(options),
                    review_reason="Complete inspected moment; tenant requested advisory scores and human judgment.")
        # Persist the AUTHORITATIVE joined candidate AFTER FIRE + ranking so the
        # Source Review queue reads the final state (not pre-FIRE selection rows).
        for c in result.candidates:
            if getattr(c, "duplicate_fingerprint", "") and (
                c.status == "scored" or getattr(c, "processing_eligibility", "") == "manual_intake"
            ):
                try:
                    if hasattr(store, "upsert_candidate"):
                        store.upsert_candidate(c)
                    if hasattr(store, "upsert_selection_fire"):
                        store.upsert_selection_fire(c)
                except Exception as exc:  # noqa: BLE001
                    # Persistence is not allowed to silently advance a candidate:
                    # a store failure marks the run honestly instead.
                    c.status = "error"
                    c.error = f"persist_candidate: {type(exc).__name__}"
        return result

    def _materialize_inspected_moments(self, c: Any, rec: Any, moments: list[dict],
                                      ev: Any, mc: Any, *, segments: Any,
                                      signal_windows: Any) -> None:
        """One independently gated, stable persisted candidate per real window.

        Reuses one inspected artifact/model response; each window receives only
        its own deterministic signals, hook/payoff, content score and lineage.
        Source metadata and paid-call costs are not manufactured or duplicated.
        """
        import copy
        import hashlib
        import json
        import math
        from .fire import evidence_confidence, fire_gate, selection_score
        from .moment_inspection import (assess_activity, assess_escalation, assess_payoff,
                                        assess_reaction_angle, assess_setup, assess_stop,
                                        normalize_signal_kinds, populate_from_inspection)
        from .moment_quality import BoundaryPlan
        from .platform_metrics import normalize_market_evidence
        from .selection import _idempotency_key

        template = copy.deepcopy(c)
        source_fp = rec.duplicate_fingerprint
        source_idem = template.idempotency_key
        market = normalize_market_evidence(rec)
        inspected_start = float(getattr(ev, "source_start", 0.0))
        windows = []
        # Analyzer timestamps are relative to its bounded artifact; pipeline
        # scan windows are already absolute source seconds.
        for item in ev.timestamps or ():
            if isinstance(item, dict):
                windows.append({**item,
                    "start_s": float(item.get("start_s") or 0.0) + inspected_start,
                    "end_s": float(item.get("end_s") or 0.0) + inspected_start})
        windows.extend(w for w in (signal_windows or []) if isinstance(w, dict))

        variants = []
        seen = set()
        for moment in moments:
            start, end = moment["recommended_clip_start"], moment["recommended_clip_end"]
            identity = ["inspected_moment_v1", rec.tenant_id, rec.profile_id, rec.platform,
                        rec.platform_content_id, source_fp, float(start), float(end),
                        float(moment["event_timestamp"])]
            fingerprint = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            target = c if not variants else copy.deepcopy(template)
            target.__dict__.pop("_complete_moment_candidates", None)
            target.duplicate_fingerprint = fingerprint
            target.idempotency_key = _idempotency_key(
                fingerprint, target.scoring_policy_version or SELECTION_POLICY_VERSION)
            target.tenant_id, target.profile_id = rec.tenant_id, rec.profile_id
            target.platform = rec.platform
            target.source_relative_rank, target.source_candidate_count = None, 0
            target.duplicate_state, target.rank_evidence = "", None
            if variants:
                target.cost_usd = 0.0  # shared inspection is charged exactly once

            scoped = copy.deepcopy(ev)
            scoped.source_start = start
            scoped.window_seconds = end - start
            scoped.signal_window = (start, end)
            scoped.timestamps = tuple({**w, "start_s": max(start, float(w["start_s"])),
                                       "end_s": min(end, float(w["end_s"]))}
                                      for w in windows
                                      if isinstance(w.get("start_s"), (int, float))
                                      and isinstance(w.get("end_s"), (int, float))
                                      and w["start_s"] < end and w["end_s"] > start)
            scoped.signals = normalize_signal_kinds(scoped.timestamps)
            scoped.caption_text = " ".join(str(s.get("text") or "") for s in (segments or [])
                if isinstance(s, dict) and float(s.get("start_s") or 0.0) < end
                and float(s.get("end_s") or 0.0) > start)
            scoped.spoken_hook = scoped.caption_text[:200]
            scoped.provider_hints = {"hook": bool(moment["hook_0_3s"]),
                                     "payoff": bool(moment["payoff"]),
                                     "reaction_angle": moment["reaction_angle"]}
            assessments = {"stop": assess_stop(scoped), "activity": assess_activity(scoped),
                "setup": assess_setup(scoped), "escalation": assess_escalation(scoped),
                "payoff": assess_payoff(scoped), "reaction_angle": assess_reaction_angle(scoped)}
            candidate = copy.deepcopy(mc)
            populate_from_inspection(candidate, scoped, assessments)
            hook = assessments["stop"].passed
            payoff = assessments["payoff"].passed
            reaction = assessments["reaction_angle"].passed
            conf_ev = scoped.as_confidence_evidence(immediate_hook=hook, payoff=payoff,
                                                    reaction_angle=moment["reaction_angle"])
            conf_ev.update(market_metrics=market.views is not None, age=market.age_hours is not None,
                           engagement=market.engagement is not None)
            conf_ev["category_metadata"] = bool(market.native.get("twitch_category"))
            conf_ev["preview"] = bool(scoped.preview_reference or market.native.get("twitch_thumbnail"))
            confidence = evidence_confidence(platform=rec.platform, evidence=conf_ev)
            raw_content = moment.get("content_score")
            content = (max(0, min(100, int(raw_content)))
                       if isinstance(raw_content, (int, float)) and not isinstance(raw_content, bool)
                       and math.isfinite(raw_content) else 0)
            selection = selection_score(market=target.market_score, content=content,
                tenant_fit_verdict="PASS", reaction_opportunity=reaction,
                evidence_completeness=confidence.score, duplicate_risk=False)
            decision = fire_gate(market_score=target.market_score, content_score=content,
                selection=selection, confidence=confidence.score,
                market_gate_pass=target.market_gate_passed, tenant_fit_pass=True,
                immediate_hook=hook, action_activity=assessments["activity"].passed,
                payoff_present=payoff, duplicate_risk=False, reaction_opportunity=reaction)

            plan = BoundaryPlan(event_timestamp=moment["event_timestamp"],
                recommended_clip_start=start, recommended_clip_end=end,
                start_reason=moment["start_reason"], end_reason=moment["end_reason"],
                moment_type=moment.get("moment_type", "unknown"),
                provenance=tuple(moment["provenance"]))
            target.boundary_plan = plan.as_dict()
            target.clip_start_plan = plan.as_dict()
            target.moment_type = {"moment_type": plan.moment_type}
            target.completeness = {"completeness_verdict": "PASS", "reasons": [], "evidence": moment}
            target.tenant_fit_verdict = "PASS"
            target.content_score_recalibrated = content
            target.selection_score = selection
            target.evidence_confidence = confidence.score
            target.market_score_final = target.market_score
            target.fire_passed, target.fire_reasons = decision.passed, decision.reasons
            target.rejection_reason = ",".join(decision.reasons)
            target.moment_evidence = {**candidate.moment_evidence,
                "inspected_moments": [copy.deepcopy(moment)],
                "source_complete_moment_count": len(moments),
                "qualification": (target.media_assessment or {}).get("qualification", {}),
                "market_evidence": target.market_evidence,
                "intent": (getattr(self.brief, "mining_requirements", {}) or {}).get("intent", ""),
                "what_happens": moment["what_happens"], "hook_0_3s": moment["hook_0_3s"],
                "payoff": moment["payoff"], "reaction_angle": moment["reaction_angle"],
                "why_selected": moment["why_reaction_worthy"],
                "lineage": {"source_duplicate_fingerprint": source_fp,
                    "source_selection_idempotency_key": source_idem,
                    "moment_fingerprint": fingerprint, "identity_version": "inspected_moment_v1",
                    "tenant_id": rec.tenant_id, "profile_id": rec.profile_id,
                    "platform": rec.platform, "platform_content_id": rec.platform_content_id,
                    "inspection_start": inspected_start,
                    "inspection_end": inspected_start + ev.window_seconds}}
            candidate.moment_start, candidate.moment_end = start, end
            candidate.moment_duration = end - start
            candidate.source_duration = _record_duration(rec)
            candidate.market_score = target.market_score
            candidate.content_score, candidate.selection_score = content, selection
            candidate.evidence_confidence = confidence.score
            candidate.fire_result = "PASS" if decision.passed else "FAIL"
            candidate.moment_evidence = copy.deepcopy(target.moment_evidence)
            target.moment_candidate = candidate.to_dict()
            target.evidence_depth = candidate.evidence_depth
            variants.append(target)
        if variants:
            c._complete_moment_candidates = tuple(variants[1:])

    def _apply_fire(self, c: Any, rec: Any, *, segments: Any = None,
                    model_timestamps: Any = None, signal_windows: Any = None,
                    _zone_override: Any = None, _inspect_other_zones: bool = True) -> None:
        import copy
        source_template = copy.deepcopy(c)
        strict_mining = bool(getattr(self.brief, "mining_requirements", {}) or {})
        from .transcript_normalize import normalize_transcript_segments
        # Raw captions arrive doubled and markup-bearing; clean ONCE here so the
        # locator, anchors, completeness gate and evidence all see real speech.
        segments = normalize_transcript_segments(segments)
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
                          description=getattr(rec, "body_excerpt", "") or "",
                          creator=getattr(rec, "creator_channel_identity", "") or "", brief=self.brief)
        mc = from_market_and_market_evidence(rec, market_ev, tenant_fit_result=fit)

        # ---- WHOLE-SOURCE MOMENT LOCATION --------------------------------
        # Long-form: scan the WHOLE source cheaply, rank zones, promote the best,
        # optimise where the clip should BEGIN. Short-form: the source IS the
        # moment, so the locator is deliberately bypassed.
        from .clip_start import optimize_clip_start  # noqa: F401 (kept for API compat)
        from .moment_locator import (DEFAULT_PROMOTE, SHORT_FORM_MAX_SECONDS,
                                     locate_moments, promote_zones, source_mode,
                                     source_mode_for_record)
        from .moment_quality import (build_boundary_plan, classify_moment_type,
                                     completeness_gate, repair_boundaries)

        duration = _record_duration(rec)
        # Source CHARACTERISTICS decide the mode (asset kind / URL / duration),
        # never the platform name.
        mode = source_mode_for_record(rec)
        zones = locate_moments(segments or [], source_id=str(getattr(rec, "platform_content_id", "")),
                               source_url=str(getattr(rec, "canonical_url", "")),
                               source_duration=duration,
                               model_timestamps=(model_timestamps if segments or not strict_mining else None),
                               signal_peaks=signal_windows,
                               **({"asset_kind": "long_form"} if strict_mining else {}))
        promoted = promote_zones(zones, keep=DEFAULT_PROMOTE if strict_mining else 1)
        zone = _zone_override if _zone_override is not None else (promoted[0] if promoted else None)
        native_full_inspection = (strict_mining and mode == "source_as_moment"
                                  and duration is not None and duration <= SHORT_FORM_MAX_SECONDS)
        plan = None
        inspect_start = 0.0
        type_result = None
        gate = None
        repairs: tuple = ()
        if mode == "source_as_moment" and duration is not None and (not strict_mining or native_full_inspection or zone is None):
            # A native Short IS the moment. The complete asset is the clip:
            # boundaries are [0, duration] BY CONSTRUCTION, so no locator
            # rebuild and no blind boundary repair can truncate it. A 120s
            # short must never become a 15s guess.
            from .moment_quality import BoundaryPlan, CompletenessResult
            type_result = classify_moment_type(
                transcript_text=" ".join(str(seg.get("text") or "") for seg in (segments or []) if isinstance(seg, dict)),
                signal_bases=[str(w.get("basis")) for w in (signal_windows or [])
                              if isinstance(w, dict) and w.get("basis")])
            plan = BoundaryPlan(
                event_timestamp=0.0, recommended_clip_start=0.0,
                recommended_clip_end=float(duration),
                start_reason="native_short_starts_at_zero",
                end_reason="native_short_complete_asset",
                moment_type=type_result.moment_type,
                provenance=("source_as_moment", "complete_asset"))
            gate = completeness_gate(plan, segments or [], signals=signal_windows or ())
            inspect_start = 0.0
        elif zone is not None:
            # MOMENT TYPE first: different types imply different boundaries. The
            # type comes from ALREADY-AVAILABLE evidence (no new model stage).
            # Pad the window: a type cue often sits just outside the provisional
            # bounds, and classifying from a too-narrow window yields `unknown`.
            pad = 8.0
            zone_text = " ".join(
                str(seg.get("text") or "") for seg in (segments or [])
                if isinstance(seg, dict)
                and zone.provisional_start - pad <= float(seg.get("start_s", 0.0) or 0.0)
                <= zone.provisional_end + pad)
            if len(zone_text.split()) < 4:
                zone_text = " ".join(str(seg.get("text") or "")
                                     for seg in (segments or []) if isinstance(seg, dict))
            type_result = classify_moment_type(
                transcript_text=zone_text or zone.transcript_excerpt,
                signal_bases=[str(w.get("basis")) for w in (signal_windows or [])
                              if isinstance(w, dict) and w.get("basis")])
            plan = build_boundary_plan(type_result.moment_type, zone.event_timestamp,
                                       segments or [], signals=signal_windows or (),
                                       duration_hint=duration)
            inspect_start = plan.recommended_clip_start
            gate = completeness_gate(plan, segments or [], signals=signal_windows or ())
            if gate.failed:
                plan, gate, repairs = repair_boundaries(
                    plan, gate, segments or [], signals=signal_windows or (),
                    duration_hint=duration)
        c.moment_type = type_result.as_dict() if type_result is not None else None
        c.boundary_plan = plan.as_dict() if plan is not None else None
        c.completeness = gate.as_dict() if gate is not None else None
        c.repair_attempts = repairs
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

        requirements = getattr(self.brief, "mining_requirements", {}) or {}

        def _inspect_window(length=None):
            from . import bounded_preview
            # Known native durations can be rounded upward even below 12s.
            # Always use verified media probing for the terminal native request;
            # never let the legacy transfer helper claim requested == inspected.
            use_contiguous = native_full_inspection or (length is not None and length > 12.0)
            fetcher = getattr(self, "_inspection_contiguous_preview" if use_contiguous
                              else "_inspection_preview", None)
            if fetcher is None:
                fetcher = (getattr(bounded_preview, "fetch_contiguous_inspection_preview")
                           if use_contiguous else fetch_bounded_preview)
            if native_full_inspection:
                from functools import partial
                # Only the terminal native chunk may end short. The helper
                # reports real inspected_end, which inspection and the resolver
                # propagate as the hard upper bound; no padding is requested.
                fetcher = partial(fetcher, allow_terminal_partial=True)
            evidence, logs = inspect_with_escalation(
                rec, fetch_preview=fetcher,
                analyze_signals=getattr(self, "_inspection_analyze", analyze_media_signals),
                decide=_decide,
                fetch_captions=getattr(self, "_inspection_captions", fetch_transcript),
                start=inspect_start, retain_last=True,
                **({"windows": (length,), "max_window": length} if length is not None else {}))
            evidence.source_start = inspect_start
            from .moment_inspection import normalize_signal_kinds
            extra_signals = normalize_signal_kinds(signal_windows or [])
            evidence.signals.update(extra_signals)
            if any(k in extra_signals for k in ("motion", "scene_change", "cut_density")):
                evidence.visual_opening = True
            parsed = None
            if evidence.last_artifact_path:
                from .bounded_preview import cleanup
                try:
                    parsed = self._media_assess(evidence, rec)
                    c.media_assessment_status = "ok" if parsed else "unsupported_provider"
                except ProviderError as exc:
                    c.media_assessment_status = f"unavailable:{exc.code}"
                except Exception as exc:
                    c.media_assessment_status = f"failed:{type(exc).__name__}"
                finally:
                    cleanup(evidence.last_artifact_path)
                    evidence.last_artifact_path = ""
            else:
                c.media_assessment_status = "no_artifact"
            if parsed:
                c.media_assessment = dict(parsed)
                self._merge_media_hints(evidence, parsed)
                extra_cost = float((parsed.get("usage") or {}).get("cost_usd", 0.0) or 0.0)
                c.cost_usd = round((c.cost_usd or 0.0) + extra_cost, 6)
                self._media_cost_usd += extra_cost
            return evidence, logs, parsed

        initial_length = min(12.0, max(0.0, duration - inspect_start)) if duration else 12.0
        if native_full_inspection:
            # A bounded SHORT source is inspected completely once, assembled
            # from cached <=12s transfers. This does NOT select the whole asset:
            # only independently resolved event windows can become candidates.
            initial_length = duration
        ev, attempts, media_assessment = _inspect_window(initial_length if requirements else None)
        c.inspection_attempts = attempts

        resolved_moments = []
        inspection_rejections = []
        if requirements:
            from .mining_contract import resolve_inspected_moments
            resolved_moments, inspection_rejections = resolve_inspected_moments(
                media_assessment or {}, requirements, inspected_start=inspect_start,
                inspected_end=min(inspect_start + ev.window_seconds, duration or float("inf")),
                duration=duration)
            # The 12s transfer chunk is NOT a clip-end cap. An unresolved event
            # advances through contiguous real coverage until its first complete
            # boundary, the actual source end, an honest retrieval failure, or the
            # existing run budget. No coverage gap may masquerade as inspected.
            while (not resolved_moments and inspection_rejections == ["no_complete_inspected_moment"]
                   and media_assessment and duration and not native_full_inspection):
                covered_end = inspect_start + ev.window_seconds
                parsed_events = media_assessment.get("moments") or []
                limit = duration if parsed_events else min(duration, max(
                    zone.provisional_end if zone is not None else covered_end,
                    (zone.event_timestamp + 12.0) if zone is not None else covered_end))
                if covered_end >= limit:
                    break
                if self._media_cost_usd >= self.config.cost_ceiling_usd:
                    inspection_rejections.append("inspection_budget_before_completeness")
                    break
                next_length = min(ev.window_seconds + 12.0, limit - inspect_start)
                next_ev, next_attempts, next_assessment = _inspect_window(next_length)
                c.inspection_attempts = (*c.inspection_attempts, *next_attempts)
                if next_ev.window_seconds <= ev.window_seconds or not next_assessment:
                    inspection_rejections.append("inspection_extension_unavailable")
                    break
                ev, media_assessment = next_ev, next_assessment
                resolved_moments, inspection_rejections = resolve_inspected_moments(
                    media_assessment, requirements, inspected_start=inspect_start,
                    inspected_end=min(inspect_start + ev.window_seconds, duration), duration=duration)
            if resolved_moments:
                # Relative ordering is applied to REAL inspected complete moments,
                # never to titles. Every alternative stays in the evidence record.
                resolved_moments.sort(key=lambda m: m["event_timestamp"])
                chosen = resolved_moments[0]
                from .moment_quality import BoundaryPlan
                plan = BoundaryPlan(event_timestamp=chosen["event_timestamp"],
                    recommended_clip_start=chosen["recommended_clip_start"],
                    recommended_clip_end=chosen["recommended_clip_end"],
                    start_reason=chosen["start_reason"], end_reason=chosen["end_reason"],
                    moment_type=chosen.get("moment_type", "unknown"),
                    provenance=tuple(chosen["provenance"]))
                c.boundary_plan = plan.as_dict()
                c.moment_type = {"moment_type": plan.moment_type}
                ev.provider_hints.update(hook=True, payoff=True, reaction_angle=chosen["reaction_angle"])
                c.tenant_fit_verdict = "PASS"
            else:
                c.tenant_fit_verdict = "FAIL"
                c.rejection_reason = ",".join(inspection_rejections)

        a = {"stop": assess_stop(ev), "activity": assess_activity(ev), "setup": assess_setup(ev),
             "escalation": assess_escalation(ev), "payoff": assess_payoff(ev),
             "reaction_angle": assess_reaction_angle(ev)}
        populate_from_inspection(mc, ev, a)

        # Re-evaluate completeness with the evidence the inspection + media
        # model actually produced: signal windows from the inspected clip and
        # the model's own payoff/context understanding. The caption-only gate
        # contradicted real evidence (a fully understood 8 s Short failed
        # MISSING_PAYOFF/INSUFFICIENT_CONTEXT).
        if gate is not None and plan is not None:
            _gate_signals = [w for w in (signal_windows or []) if isinstance(w, dict)]
            _gate_signals += [dict(t) for t in (ev.timestamps or ()) if isinstance(t, dict)]
            _media_ev: dict[str, Any] = {}
            if media_assessment:
                _scores = media_assessment.get("scores") or {}

                def _ms(k: str) -> int:
                    v = _scores.get(k)
                    return int(v) if isinstance(v, (int, float)) else 0

                _media_ev = {"payoff_understanding": _ms("payoff") >= 60,
                             "context_understanding": _ms("hook") >= 60}
            gate = completeness_gate(plan, segments or [], signals=_gate_signals,
                                     media_evidence=_media_ev)
            c.completeness = gate.as_dict()
            c.boundary_plan = plan.as_dict()
        if requirements:
            c.completeness = {"completeness_verdict": "PASS" if resolved_moments else "FAIL",
                              "reasons": inspection_rejections,
                              "evidence": resolved_moments[0] if resolved_moments else None}
            if not resolved_moments:
                c.boundary_plan = None

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
        conf_ev["category_metadata"] = bool(market_ev.native.get("twitch_category"))
        conf_ev["preview"] = bool(ev.preview_reference or market_ev.native.get("twitch_thumbnail"))
        conf = evidence_confidence(platform=rec.platform, evidence=conf_ev)
        # A media-aware model pass grounds the content score in what the clip
        # actually showed; without one the text-only score stands.
        _base_content = c.content_worthiness
        if media_assessment and isinstance(media_assessment.get("content_worthiness_score"), int):
            _base_content = int(media_assessment["content_worthiness_score"])
        content = recalibrate_content_score(_base_content, conf_ev)
        if requirements:
            content = int(resolved_moments[0].get("content_score", _base_content or 0)) if resolved_moments else 0
            content = max(0, min(100, content))

        # EVIDENCE-INFORMED MARKET: qualification is computed AFTER real content
        # evidence exists, so the market dimension reflects the grounded content
        # assessment instead of contradicting it. The pre-evidence shortlist
        # score stays on c.market_score; FIRE uses the evidence-informed one.
        _market_final = int(c.market_score or 0)
        try:
            import copy as _copy
            _enriched = _copy.copy(rec)
            _enriched.moment_evidence = dict(mc.moment_evidence)
            _enriched.moment_evidence["content_worthiness_score"] = content
            _reassess = self.topic.assess(_enriched, self.brief, competition_count=0)
            if not requirements:
                _market_final = int(_reassess.opportunity_score)
        except Exception:  # noqa: BLE001 — keep the pre-evidence score
            pass
        c.market_score_final = _market_final
        sel = selection_score(market=int(c.market_score or 0), content=content,
                              tenant_fit_verdict=c.tenant_fit_verdict,
                              reaction_opportunity=reaction_ok,
                              evidence_completeness=conf.score,
                              duplicate_risk=False)
        decision = fire_gate(
            market_score=_market_final, content_score=content, selection=sel,
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
        # A FIRE-passing candidate whose clip is NOT complete still gets no card:
        # boundary evidence is a separate, hard requirement.
        if gate is not None and not gate.passed:
            c.fire_passed = False
            c.fire_reasons = tuple(dict.fromkeys((*decision.reasons, "INCOMPLETE_CLIP")))
        if requirements:
            c.fire_passed = decision.passed and bool(resolved_moments)
            c.fire_reasons = tuple(dict.fromkeys((*decision.reasons, *inspection_rejections)))
        # The evidence-informed selection replaces the pre-evidence combined
        # selection on the authoritative record (the pre-fire one reflects the
        # text-only content score and would contradict a FIRE pass).
        c.selection_score = sel
        c.content_score_recalibrated = content
        c.evidence_confidence = conf.score
        c.moment_evidence = dict(mc.moment_evidence)
        if requirements:
            c.moment_evidence.update({"inspected_moments": resolved_moments,
                                     "qualification": (media_assessment or {}).get("qualification", {}),
                                     "market_evidence": c.market_evidence,
                                     "intent": requirements.get("intent", "")})
            if resolved_moments:
                chosen = resolved_moments[0]
                c.moment_evidence.update({"what_happens": chosen["what_happens"],
                    "hook_0_3s": chosen["hook_0_3s"], "payoff": chosen["payoff"],
                    "reaction_angle": chosen["reaction_angle"],
                    "why_selected": chosen["why_reaction_worthy"]})
        c.moment_candidate = mc.to_dict()
        c.evidence_depth = mc.evidence_depth
        if requirements and resolved_moments:
            self._materialize_inspected_moments(c, rec, resolved_moments, ev, mc,
                                               segments=segments, signal_windows=signal_windows)
        if requirements and _inspect_other_zones and not native_full_inspection:
            variants = list(c.__dict__.pop("_complete_moment_candidates", ()))
            for alternative in promoted:
                if alternative is zone:
                    continue
                if self._media_cost_usd >= self.config.cost_ceiling_usd:
                    break
                sibling = copy.deepcopy(source_template)
                sibling.cost_usd = 0.0
                try:
                    self._apply_fire(sibling, rec, segments=segments,
                        model_timestamps=model_timestamps, signal_windows=signal_windows,
                        _zone_override=alternative, _inspect_other_zones=False)
                except Exception as exc:
                    sibling.status = "error"
                    sibling.error = f"zone_inspection:{type(exc).__name__}"
                variants.append(sibling)
                variants.extend(sibling.__dict__.pop("_complete_moment_candidates", ()))
            c._complete_moment_candidates = tuple(variants)

    def _process(self, rec: Any, store: Any, run: Pass1RunResult) -> Pass1CandidateResult:
        c = Pass1CandidateResult(
            duplicate_fingerprint=rec.duplicate_fingerprint,
            platform_content_id=rec.platform_content_id,
            title=rec.title, market_score=0,
        )
        if hasattr(rec, "processing_eligibility"):
            c.processing_eligibility = rec.processing_eligibility
        if hasattr(rec, "connector_name"):
            c.connector_name = rec.connector_name
        if (rec.tenant_id != self.brief.tenant_id or
                getattr(rec, "profile_id", "") != self.brief.profile_id):
            c.status = "filtered_tenant"
            c.rejection_reason = "tenant_or_profile_mismatch"
            return c
        requirements = getattr(self.brief, "mining_requirements", {}) or {}
        if requirements:
            from .mining_contract import media_qualification
            from .tenant_fit import tenant_fit
            media_ok, media_reason = media_qualification(rec)
            if not media_ok:
                c.status, c.rejection_reason = "filtered_media", media_reason
                return c
            fit = tenant_fit(title=rec.title, description=getattr(rec, "body_excerpt", ""),
                             creator=rec.creator_channel_identity, brief=self.brief)
            c.tenant_fit_verdict, c.tenant_fit_reasons = fit.verdict, fit.reasons
            if fit.failed:
                c.status, c.rejection_reason = "filtered_tenant", ",".join(fit.reasons)
                return c

        # 1. Topic Intelligence (market) — no model cost.
        topic = self.topic.assess(rec, self.brief, competition_count=0)
        c.market_score = topic.opportunity_score
        demand = None
        if requirements.get("market_policy") == "observed_demand_v1":
            from .market_demand import assess_market_demand
            demand = assess_market_demand(rec,
                max_age_hours=self.brief.freshness_window_days * 24 if self.brief.freshness_window_days else None)
            c.market_score = demand.score
            c.market_evidence = demand.evidence

        # 2. Market shortlist threshold.
        advisory_review = (requirements.get("review_policy") or {}).get("mode") == "human_curated"
        if not advisory_review and c.market_score < (75 if requirements else self.config.market_shortlist_threshold):
            c.status = "skipped_market"
            return c

        # 2.5 DETERMINISTIC MARKET GATE — runs BEFORE any expensive work.
        # A market failure must not reach transcript retrieval, visual
        # inspection, model scoring, or Source Review. The reason is always
        # persisted so a rejection is explainable, never silent.
        from .platform_metrics import normalize_market_evidence, platform_market_gate
        market_ev = normalize_market_evidence(rec)
        decision = demand or platform_market_gate(market_ev,
            **({"max_age_hours": self.brief.freshness_window_days * 24}
               if requirements and self.brief.freshness_window_days > 0 else {}))
        c.market_gate_passed = decision.passed
        c.market_evidence = decision.evidence
        # Tenant-configured human review makes numerical demand scores advisory,
        # without changing the score, FIRE result, or structural/safety gates.
        structural_market_failure = any(r in {"UNSUPPORTED_MARKET_PLATFORM", "SUBREDDIT_META"}
                                        for r in decision.reasons)
        if not decision.passed and (not advisory_review or structural_market_failure):
            c.status = "filtered_market"
            c.rejection_reason = ",".join(decision.reasons)
            return c

        # 2.6 EARLY TENANT FIT — cheap metadata only, still BEFORE expensive work.
        # Ambiguous candidates PROCEED: only a clear mismatch stops the pipeline,
        # because inventing a rejection is worse than spending inspection budget.
        from .tenant_fit import tenant_fit
        fit = tenant_fit(title=getattr(rec, "title", "") or "",
                         description=getattr(rec, "body_excerpt", "") or "",
                         creator=getattr(rec, "creator_channel_identity", "") or "", brief=self.brief)
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
            if (self.config.enable_fire_gate and advisory_review
                    and (requirements.get("review_policy") or {}).get("media_first") is True):
                # No paid title/metadata opinion before an already-required
                # media inspection. Zero means unassessed, never a content claim.
                raw = {"scores": {}, "content_worthiness_score": 0,
                       "reasons": [{"code": "SEMANTIC_ASSESSMENT_DEFERRED_TO_ACTUAL_MEDIA"}],
                       "model_identity": {"provider": "deterministic", "resolved_model": "deferred_to_media"},
                       "usage": {"cost_usd": 0, "input_tokens": 0, "output_tokens": 0}}
            else:
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
        # Carry the identity the authoritative record needs (store persistence).
        c.tenant_id = rec.tenant_id
        c.profile_id = rec.profile_id
        c.platform = rec.platform
        c.canonical_url = getattr(rec, "canonical_url", "") or ""
        c.publication_timestamp = getattr(rec, "publication_timestamp", None)
        c.creator_channel_identity = getattr(rec, "creator_channel_identity", "") or ""
        c.idempotency_key = getattr(sel, "idempotency_key", "") or ""
        c.scoring_policy_version = getattr(sel, "scoring_policy_version", "") or ""
        # 5. BOUNDED MOMENT INSPECTION -> grounded assessments -> EVIDENCE
        # CONFIDENCE -> FIRE. Only candidates that already passed Market and
        # Tenant Fit reach this point. A FIRE failure records explicit reasons
        # and the candidate is never carded.
        if self.config.enable_fire_gate:
            self._apply_fire(c, rec, segments=segments,
                            model_timestamps=getattr(assessment, "candidate_timestamps", None),
                            signal_windows=signal_windows)
            run.total_cost_usd = round(run.total_cost_usd + self._media_cost_usd, 6)
            self._media_cost_usd = 0.0
        return c


def _load_provider_key(provider_name: str) -> str:
    from .providers import load_key
    if provider_name == "gemini":
        return load_key("LOOM_GEMINI_KEY")
    if provider_name == "qwen":
        return load_key("LOOM_OPENROUTER_API_KEY")
    return ""
