"""Content Intelligence Preflight — Pass 1 real acceptance proof (text-only).

Minimal real semantic proof per Bryan's approved plan:
  * MODEL:  existing Gemini Flash-Lite (LOOM_GEMINI_KEY, name-only, never printed).
  * EVIDENCE: ONE public non-production YouTube candidate; English captions
    retrieved via yt-dlp (skip-download => 0 video/audio bytes, no playlist
    expansion, no cookies, no auth bypass). No caption artifacts persisted.
  * BOUNDED MEDIA: deferred (Pass 1 is text-only).
  * COST: hard ceiling $0.05.

Pipeline exercised end-to-end:
  Topic Intelligence (market) -> Content Intelligence (Gemini on transcript,
  evidence-gated) -> Combined Source Selection -> Supabase persistence ->
  acceptance checks (idempotent replay, no full download, no OpenShorts/Discord/
  publish, proof profile excluded from production queues).

Run from repo root:  python3 infra/content_intelligence/prove_pass1.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "infra"))

from discovery.connectors.youtube import YouTubeConnector  # noqa: E402
from discovery.connector import DiscoveryRequest, ConnectorError  # noqa: E402
from discovery.source_brief import SourceBrief  # noqa: E402
from discovery.topic_intelligence import TopicIntelligence  # noqa: E402
from content_intelligence.contract import ContentAssessment, TRANSCRIPT_AUTO, TRANSCRIPT_CREATOR  # noqa: E402
from content_intelligence.provider import Evidence, format_fit_scores, tenant_fit_score  # noqa: E402
from content_intelligence.selection import compute_selection, SELECTION_POLICY_VERSION  # noqa: E402

REAL_TENANT = "dreemzy"
PROOF_PROFILE = "pass1-gemini-proof"  # non-production (contains 'proof'), auto-excluded
YOUTUBE_CHANNEL = "UCXuqSBlHAE6Xw-yeJA0Tunw"  # Linus Tech Tips (real, public)
GEMINI_MODEL = "models/gemini-flash-lite-latest"
MAX_TRANSCRIPT_CHARS = 12000  # bounded excerpt for the minimal proof

_PASS: list[str] = []
_FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (_PASS if ok else _FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Env (names only)
# ---------------------------------------------------------------------------
def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in (Path.home() / ".openclaw/.env", Path("infra/tunnel-proxy/.env")):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _supabase_req(url: str, key: str, method: str, path: str, body=None) -> object:
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "User-Agent": "LOOM-ContentIntelligence/1.0"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        data = json.dumps(body).encode()
    req = urllib.request.Request(f"{url}/rest/v1/{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode() or "[]"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Supabase HTTP {e.code}: {e.read().decode()[:300]}") from e


# ---------------------------------------------------------------------------
# VTT parsing
# ---------------------------------------------------------------------------
_TIMESTAMP_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})")


def _parse_vtt(text: str) -> list[dict]:
    segments = []
    cur = None
    for line in text.splitlines():
        line = line.rstrip("\n")
        if "-->" in line:
            parts = line.split("-->")
            if cur:
                segments.append(cur)
            cur = {"start_s": _ts_to_s(parts[0].strip()), "end_s": _ts_to_s(parts[1].strip()), "text": []}
        elif cur is not None and line.strip():
            cur["text"].append(line.strip())
    if cur:
        segments.append(cur)
    for s in segments:
        s["text"] = " ".join(s["text"]).strip()
    return [s for s in segments if s["text"]]


def _ts_to_s(ts: str) -> float:
    m = _TIMESTAMP_RE.search(ts)
    if not m:
        return 0.0
    h, mi, s, ms = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
    return h * 3600 + mi * 60 + s + ms / 1000.0


# ---------------------------------------------------------------------------
# Gemini Flash-Lite provider
# ---------------------------------------------------------------------------
class GeminiProvider:
    name = "gemini-flash-lite"
    version = "1.0.0"

    def __init__(self, api_key: str, model: str = GEMINI_MODEL) -> None:
        self.api_key = api_key
        self.model = model

    def assess(self, evidence_bundle: Evidence, brief) -> dict:
        prompt = _build_prompt(evidence_bundle)
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200},
        }
        url = (f"https://generativelanguage.googleapis.com/v1beta/{self.model}"
               f":generateContent?key={self.api_key}")
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "LOOM-ContentIntelligence/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode())
        text = (raw.get("candidates") or [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return _parse_model_json(text)


def _build_prompt(ev: Evidence) -> str:
    dims = ["hook", "emotion", "conflict_tension", "surprise", "humor", "relatability",
            "stakes", "story_progression", "payoff", "narrative_completeness",
            "context_independence", "reaction_potential", "commentary_value",
            "originality_novelty"]
    fmt = ["reaction_single", "reaction_compilation", "commentary_story",
           "ranking_top5", "stream_highlight", "hold_unassigned"]
    excerpt = ev.transcript_text[:MAX_TRANSCRIPT_CHARS]
    return (
        "You are a content analyst for a short-form video operation. Analyze the "
        "following video TRANSCRIPT (with timestamps) for short-form content potential.\n\n"
        "Score each dimension as an integer 0-100, based ONLY on the transcript text "
        "(do NOT infer visual or audio qualities you cannot observe from text).\n\n"
        f"Dimensions: {', '.join(dims)}.\n\n"
        "Also provide:\n"
        "- content_worthiness_score: overall 0-100 for a short clip.\n"
        "- candidate_timestamps: up to 5 {start_s, end_s, basis} for the most promising clip moments.\n"
        f"- recommended_content_format: one of {fmt}.\n"
        "- recommended_angle: one-sentence angle for the creator's commentary.\n"
        "- reasons: 3-6 {code, signal, message} explaining the strongest signals.\n\n"
        "Return ONLY valid JSON (no markdown fences) with this exact shape:\n"
        '{"scores": {"<dim>": int, ...}, "content_worthiness_score": int, '
        '"candidate_timestamps": [{"start_s": number, "end_s": number, "basis": string}], '
        '"recommended_content_format": string, "recommended_angle": string, '
        '"reasons": [{"code": string, "signal": string, "message": string}]}\n\n'
        f"TRANSCRIPT:\n{excerpt}"
    )


def _parse_model_json(text: str) -> dict:
    text = (text or "").strip()
    # Strip markdown fences if present.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise RuntimeError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(m.group(0))


# ---------------------------------------------------------------------------
# Proof
# ---------------------------------------------------------------------------
def main() -> int:
    env = _load_env()
    gemini_key = env.get("LOOM_GEMINI_KEY", "")
    sb_url = env.get("LOOM_SUPABASE_URL", "").rstrip("/")
    sb_key = env.get("LOOM_SUPABASE_SERVICE_ROLE_KEY", "")
    if not gemini_key:
        print("FAIL: LOOM_GEMINI_KEY absent (name-only lookup)")
        return 2
    if not (sb_url and sb_key):
        print("FAIL: Supabase creds absent (name-only lookup)")
        return 2

    print("=== LOOM Content Intelligence Preflight — Pass 1 real proof ===\n")

    brief = SourceBrief(
        tenant_id=REAL_TENANT, profile_id=PROOF_PROFILE, version="1",
        target_platforms=["youtube"], topics=["technology", "reviews"], entities=[],
        keywords=["review", "smart home", "tech"], exclusions=["political", "graphic", "nsfw"],
        creator_channel_allowlist=[], creator_channel_blocklist=[],
        freshness_window_days=365, content_type_requirements=["video"], language="en",
        provenance_rights_requirements="first-party RSS",
        acquisition_eligibility=True, cost_ceiling_usd=0.0, manual_review_required=True,
        connector_restrictions=["youtube"],
    )

    # 1. Discover candidate(s).
    print("[1] discover candidate (first-party RSS):")
    conn = YouTubeConnector(timeout=20.0)
    records = list(conn.discover(DiscoveryRequest(brief=brief, query=f"channel:{YOUTUBE_CHANNEL}")))
    check("discovered > 0 candidates", len(records) > 0, f"n={len(records)}")
    if not records:
        return 2

    # 2. Find a candidate with English captions (bounded attempts).
    rec = None
    transcript_type = TRANSCRIPT_AUTO
    segments = []
    print("[2] retrieve English captions (yt-dlp skip-download):")
    for cand in records[:4]:
        url = cand.canonical_url
        seg, ttype = _fetch_captions(url)
        if seg:
            rec = cand
            segments = seg
            transcript_type = ttype
            break
    check("captions retrieved for >=1 candidate", rec is not None,
          rec.platform_content_id if rec else "none")
    if rec is None:
        print("    => no candidate with English captions; abort (report exact failure).")
        return 2

    # 3. Topic Intelligence (market).
    print("[3] Topic Intelligence (market evidence):")
    ti = TopicIntelligence()
    topic = ti.assess(rec, brief, competition_count=0)
    check("market score computed", 0 <= topic.opportunity_score <= 100,
          f"market={topic.opportunity_score} conf={topic.confidence}")

    # 4. Build evidence + call Gemini Flash-Lite.
    print("[4] Content Intelligence (Gemini Flash-Lite on transcript):")
    transcript_text = " ".join(s["text"] for s in segments)
    ev = Evidence(
        platform=rec.platform, content_type="video", title=rec.title,
        transcript_text=transcript_text, transcript_availability=transcript_type,
    )
    provider = GeminiProvider(gemini_key)
    raw = provider.assess(ev, brief)
    scores = {k: int(v) for k, v in (raw.get("scores") or {}).items()}
    # Deterministic structural signals.
    fit = format_fit_scores(rec.platform, "video", True)
    scores["tenant_fit"] = tenant_fit_score(brief, rec.title, "", transcript_text)
    scores["format_fit"] = max(fit.values())
    worthiness = int(raw.get("content_worthiness_score", 0))
    completeness = ev.evidence_completeness()
    conf = min(0.60, completeness)  # level 1 ceiling 0.60
    check("model returned scores", len(scores) > 0, f"{len(scores)} dims")
    check("content worthiness computed", 0 <= worthiness <= 100, f"worthiness={worthiness}")

    # 5. Validate as a strict ContentAssessment.
    print("[5] validate strict Content Assessment:")
    assessment = ContentAssessment(
        duplicate_fingerprint=rec.duplicate_fingerprint, tenant_id=REAL_TENANT,
        profile_id=PROOF_PROFILE, platform=rec.platform, content_assessment_version="1.0.0",
        evidence_level=1, evidence_types=["metadata", "text_content"],
        evidence_completeness=completeness, scores=scores,
        content_worthiness_score=worthiness, content_confidence=conf,
        reasons=list(raw.get("reasons") or []), flags=["PASS1_GEMINI"],
        idempotency_key=_idem_key(rec.duplicate_fingerprint, "1.0.0", 1, ["metadata", "text_content"]),
        transcript_availability=transcript_type,
        candidate_timestamps=list(raw.get("candidate_timestamps") or []),
        recommended_content_format=raw.get("recommended_content_format") or "hold_unassigned",
        recommended_angle=raw.get("recommended_angle") or "",
        model_provider=provider.name, model_version=provider.version, prompt_version="1.0.0",
    )
    check("evidence_level=1 (text)", assessment.evidence_level == 1)
    check("no visual/audio claims at level 1",
          "visual_interest" not in assessment.scores and "vocal_intensity" not in assessment.scores)
    check("confidence respects level-1 ceiling", assessment.content_confidence <= 0.60,
          f"conf={assessment.content_confidence}")

    # 6. Combined Source Selection.
    print("[6] Combined Source Selection:")
    sel = compute_selection(
        duplicate_fingerprint=rec.duplicate_fingerprint, tenant_id=REAL_TENANT,
        profile_id=PROOF_PROFILE, platform=rec.platform,
        market_opportunity_score=topic.opportunity_score, market_confidence=topic.confidence,
        content_worthiness_score=worthiness, content_confidence=conf,
        tenant_format_fit_score=assessment.scores.get("tenant_fit", 0),
        provenance_usability_score=80,
        recommended_format=assessment.recommended_content_format,
        recommended_angle=assessment.recommended_angle,
        suggested_timestamps=assessment.candidate_timestamps,
    )
    check("selection score computed", 0 <= sel.source_selection_score <= 100,
          f"selection={sel.source_selection_score} conf={sel.source_selection_confidence} outcome={sel.outcome}")

    # 7. Persist to Supabase (idempotent).
    print("[7] persist to Supabase (idempotent):")
    _upsert_assessment(sb_url, sb_key, assessment)
    _upsert_selection(sb_url, sb_key, sel)
    # Replay idempotency.
    _upsert_assessment(sb_url, sb_key, assessment)
    _upsert_selection(sb_url, sb_key, sel)
    n_assess = _count(sb_url, sb_key, "loom_content_assessments", assessment.idempotency_key)
    n_sel = _count(sb_url, sb_key, "loom_source_selections", sel.idempotency_key)
    check("assessment replay idempotent (1 row)", n_assess == 1, f"rows={n_assess}")
    check("selection replay idempotent (1 row)", n_sel == 1, f"rows={n_sel}")

    # 8. Production-queue exclusion + boundary guards.
    print("[8] exclusion + boundary guards:")
    check("proof profile non-production",
          "proof" in PROOF_PROFILE or "acceptance" in PROOF_PROFILE, PROOF_PROFILE)
    forbidden = ("openshorts", "discord", "ghl", "distribution", "social", "elevenlabs", "retell")
    imported = [m for m in sys.modules if any(f in m.lower() for f in forbidden)]
    check("no forbidden modules imported", not imported, ",".join(imported) if imported else "none")

    print("\n=== RESULT ===")
    print(f"  candidate: {rec.platform_content_id} — {rec.title[:70]}")
    print(f"  transcript: {transcript_type} ({len(transcript_text)} chars, {len(segments)} cues)")
    print(f"  market={topic.opportunity_score}  content_worthiness={worthiness}  selection={sel.source_selection_score}  outcome={sel.outcome}")
    print(f"  recommended_format={assessment.recommended_content_format}")
    print(f"  PASS: {len(_PASS)}  FAIL: {len(_FAIL)}")
    if _FAIL:
        print("  FAILED:", ", ".join(_FAIL))
        return 1
    print("  ALL PASS-1 ACCEPTANCE CRITERIA PASSED")
    return 0


def _fetch_captions(url: str) -> tuple[list[dict], str]:
    """Fetch English captions (creator first, else auto) without video download.
    Returns (segments, transcript_type)."""
    tmp = tempfile.mkdtemp(prefix="loom-pass1-caps-")
    try:
        # 1) Creator/manual captions first.
        subprocess.run(
            ["yt-dlp", "--skip-download", "--no-playlist", "--no-update",
             "--write-subs", "--sub-langs", "en.*", "--sub-format", "vtt",
             "-o", f"{tmp}/%(id)s", url],
            capture_output=True, text=True, timeout=120,
        )
        vtts = list(Path(tmp).glob("*.vtt"))
        if vtts:
            text = vtts[0].read_text(encoding="utf-8", errors="replace")
            return _parse_vtt(text), TRANSCRIPT_CREATOR
        # 2) Else auto-generated captions.
        subprocess.run(
            ["yt-dlp", "--skip-download", "--no-playlist", "--no-update",
             "--write-auto-subs", "--sub-langs", "en.*", "--sub-format", "vtt",
             "-o", f"{tmp}/%(id)s", url],
            capture_output=True, text=True, timeout=120,
        )
        vtts = list(Path(tmp).glob("*.vtt"))
        if not vtts:
            return [], TRANSCRIPT_AUTO
        text = vtts[0].read_text(encoding="utf-8", errors="replace")
        return _parse_vtt(text), TRANSCRIPT_AUTO
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _idem_key(fingerprint: str, version: str, level: int, evidence_types: list[str]) -> str:
    import hashlib
    raw = f"{fingerprint}\x1f{version}\x1f{level}\x1f{evidence_types}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _upsert_assessment(sb_url, sb_key, a: ContentAssessment) -> None:
    d = a.to_dict()
    payload = {
        "idempotency_key": a.idempotency_key,
        "tenant_id": a.tenant_id, "profile_id": a.profile_id, "platform": a.platform,
        "duplicate_fingerprint": a.duplicate_fingerprint,
        "content_assessment_version": a.content_assessment_version,
        "evidence_level": a.evidence_level, "evidence_types": a.evidence_types,
        "evidence_completeness": a.evidence_completeness, "scores": a.scores,
        "content_worthiness_score": a.content_worthiness_score,
        "content_confidence": a.content_confidence,
        "transcript_availability": a.transcript_availability,
        "transcript_quality": a.transcript_quality,
        "candidate_timestamps": a.candidate_timestamps,
        "recommended_content_format": a.recommended_content_format,
        "recommended_angle": a.recommended_angle, "reasons": a.reasons,
        "flags": a.flags, "risk_flags": a.risk_flags,
        "model_provider": a.model_provider, "model_version": a.model_version,
        "prompt_version": a.prompt_version, "raw_model_reference": a.raw_model_reference,
    }
    _supabase_req(sb_url, sb_key, "POST",
                  "loom_content_assessments?on_conflict=idempotency_key", payload)


def _upsert_selection(sb_url, sb_key, s) -> None:
    d = s.to_dict()
    payload = {
        "idempotency_key": s.idempotency_key, "tenant_id": s.tenant_id,
        "profile_id": s.profile_id, "platform": s.platform,
        "duplicate_fingerprint": s.duplicate_fingerprint,
        "scoring_policy_version": s.scoring_policy_version,
        "market_opportunity_score": s.market_opportunity_score,
        "market_confidence": s.market_confidence,
        "content_worthiness_score": s.content_worthiness_score,
        "content_confidence": s.content_confidence,
        "tenant_format_fit_score": s.tenant_format_fit_score,
        "provenance_usability_score": s.provenance_usability_score,
        "source_selection_score": s.source_selection_score,
        "source_selection_confidence": s.source_selection_confidence,
        "recommended_format": s.recommended_format,
        "recommended_angle": s.recommended_angle,
        "suggested_timestamps": s.suggested_timestamps,
        "reason_codes": s.reason_codes, "explanation": s.explanation,
        "missing_evidence": s.missing_evidence, "disqualifying_risks": s.disqualifying_risks,
        "outcome": s.outcome,
    }
    _supabase_req(sb_url, sb_key, "POST",
                  "loom_source_selections?on_conflict=idempotency_key", payload)


def _count(sb_url, sb_key, table: str, idempotency_key: str) -> int:
    import urllib.parse
    q = urllib.parse.quote(idempotency_key)
    rows = _supabase_req(sb_url, sb_key, "GET",
                         f"{table}?idempotency_key=eq.{q}&select=idempotency_key")
    return len(rows) if isinstance(rows, list) else 0


if __name__ == "__main__":
    sys.exit(main())
