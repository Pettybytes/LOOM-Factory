"""Relative reranking of a BOUNDED finalist set — ADDITIONAL evidence, never a replacement.

Where this sits (locked architecture):

    source -> candidate generation -> evidence/fusion
           -> independent qualification/scoring -> bounded finalists
           -> RELATIVE RERANK (this module) -> prioritized finalists
           -> Boundary Refiner / review flow

Discovery stays upstream; 5B stays responsible for transcript/audio/visual
evidence; Content Intelligence stays responsible for content-worthiness;
5C.2 stays responsible for boundary placement. This module never places
frame-level boundaries and never invents candidates.

What it is NOT: a virality oracle. The only meaning of its output is
"within this source's finalist set, these candidates appear stronger than those,
based on the available evidence".

Contract guarantees:

* The ORIGINAL independent score/rank is never overwritten — relative ranking is
  recorded alongside it.
* Candidates are never deleted; if the set is larger than the configured maximum,
  only the top-N by independent rank are reranked and the remainder are carried
  through unchanged.
* Any failure (no credential, provider error, timeout, malformed JSON, schema
  violation, set too small) returns the INDEPENDENT ordering with an explicit
  ``fallback`` reason — reranking is never a hard failure point.
* Strict schema validation: every finalist exactly once, unique contiguous ranks,
  no unknown or missing ids, bounded numeric fields.
* Deterministic handling: temperature 0, stable ordering, deterministic
  tie-breaking by (relative_score desc, independent_score desc, id asc).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

try:  # package context (content_intelligence.*)
    from .providers import (
        ModelIdentity,
        ProviderError,
        ProviderUsage,
        QWEN_PINNED_MODEL,
        _estimate_cost,
        _parse_json,
    )
except ImportError:  # top-level context (tests run from this directory)
    from providers import (  # type: ignore
        ModelIdentity,
        ProviderError,
        ProviderUsage,
        QWEN_PINNED_MODEL,
        _estimate_cost,
        _parse_json,
    )

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
QWEN_KEY_ENV = "LOOM_OPENROUTER_API_KEY"

# A transport takes the prompt and returns (text, usage_dict, model_str).
Transport = Callable[[str], "tuple[str, dict[str, Any], str]"]


class RerankValidationError(ValueError):
    """Model output that cannot be trusted as a rerank result."""


# ---------------------------------------------------------------------------
# configuration / data
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RerankConfig:
    max_finalists: int = 6          # maximum finalists sent to Qwen in one call
    min_finalists: int = 2          # below this, relative comparison is meaningless
    max_excerpt_chars: int = 700    # transcript excerpt per finalist
    max_evidence_chars: int = 6000  # total evidence budget for the whole prompt
    temperature: float = 0.0
    # The pinned Qwen model is a REASONING model. Measured through the existing
    # provider path on this machine: one assessment consumed 5464 output tokens
    # (reasoning + answer). The original 1500 budget returned an EMPTY completion
    # (finish_reason="length", content="") and every call failed as invalid_json;
    # even 6000 failed intermittently because reasoning length varies per call.
    # A generous cap costs nothing extra: only ACTUAL usage is billed.
    max_output_tokens: int = 16000
    # One assessment measured 60.9 s end to end, so a 120 s timeout left almost no
    # headroom for a slower reasoning pass.
    # Measured: showing each candidate's independent score in the comparison
    # prompt ANCHORS the model onto it — the relative ordering reproduced the
    # independent ordering exactly (rho 1.0, identical scores) on all 3 sources.
    # With the score line removed the same question produced rho 0.2 and -0.105
    # on 2 of 3 sources, i.e. genuine relative judgement. The score is still
    # PRESERVED in the result; it is simply not revealed to the model.
    include_independent_score_in_prompt: bool = False
    timeout: float = 300.0
    model: str = QWEN_PINNED_MODEL


@dataclass
class Finalist:
    """One already-qualified candidate. Evidence is bounded, never a whole VOD."""

    candidate_id: str
    independent_score: float
    independent_rank: int
    transcript_excerpt: str = ""
    duration_s: float | None = None
    dimensions: dict[str, Any] = field(default_factory=dict)
    hook: str = ""
    signal_bases: list[str] = field(default_factory=list)
    source_id: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class RerankedFinalist:
    candidate_id: str
    independent_score: float
    independent_rank: int
    relative_rank: int
    relative_score: float | None = None
    rationale: str = ""
    strongest_evidence: str = ""
    redundant_with: str | None = None
    confidence: float | None = None
    reranked: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "independent_score": self.independent_score,
            "independent_rank": self.independent_rank,
            "relative_rank": self.relative_rank,
            "relative_score": self.relative_score,
            "rationale": self.rationale,
            "strongest_evidence": self.strongest_evidence,
            "redundant_with": self.redundant_with,
            "confidence": self.confidence,
            "reranked": self.reranked,
            "provenance": self.provenance,
        }


@dataclass
class RerankResult:
    used: bool
    fallback: str
    finalists: list[RerankedFinalist]
    model_identity: ModelIdentity | None = None
    usage: ProviderUsage | None = None
    prompt_chars: int = 0
    reranked_count: int = 0
    carried_count: int = 0
    raw_excerpt: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def ordering_changed(self) -> bool:
        reranked = [f for f in self.finalists if f.reranked]
        return any(f.relative_rank != f.independent_rank for f in reranked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "used": self.used,
            "fallback": self.fallback,
            "reranked_count": self.reranked_count,
            "carried_count": self.carried_count,
            "ordering_changed": self.ordering_changed,
            "prompt_chars": self.prompt_chars,
            "model_identity": (self.model_identity.__dict__ if self.model_identity else None),
            "usage": (self.usage.__dict__ if self.usage else None),
            "finalists": [f.to_dict() for f in self.finalists],
            "provenance": self.provenance,
        }


# ---------------------------------------------------------------------------
# prompt (bounded)
# ---------------------------------------------------------------------------
def build_rerank_prompt(finalists: list[Finalist], source_context: str = "",
                        config: RerankConfig | None = None) -> str:
    """Bounded comparison prompt. Excerpts are truncated and the total evidence
    budget is enforced, so a long source can never blow up the context."""
    cfg = config or RerankConfig()
    lines: list[str] = []
    used = 0
    for f in finalists:
        excerpt = (f.transcript_excerpt or "")[:cfg.max_excerpt_chars]
        dims = ", ".join(f"{k}={v}" for k, v in sorted((f.dimensions or {}).items()))[:240]
        block = [f"candidate_id: {f.candidate_id}"]
        if cfg.include_independent_score_in_prompt:
            block.append(f"independent_score: {round(float(f.independent_score), 2)}")
        block.append(
            f"duration_s: {round(float(f.duration_s), 2) if f.duration_s is not None else 'unknown'}")
        if f.hook:
            block.append(f"hook: {f.hook[:160]}")
        if dims:
            block.append(f"dimensions: {dims}")
        if f.signal_bases:
            block.append(f"signal_evidence: {', '.join(f.signal_bases)}")
        if excerpt:
            block.append(f"transcript_excerpt: {excerpt}")
        text = "\n".join(block)
        if used + len(text) > cfg.max_evidence_chars:
            break
        used += len(text)
        lines.append(text)

    return (
        "You are comparing ALREADY-QUALIFIED candidate moments from ONE source.\n"
        "Decide ONLY their RELATIVE order: which are stronger than which, on the\n"
        "evidence given. You are NOT predicting views, revenue or virality.\n\n"
        f"Source context: {source_context[:400] or 'not provided'}\n\n"
        "Judge on: immediate hook, emotional/reaction strength, understandable\n"
        "context, payoff, standalone comprehensibility, novelty/non-redundancy\n"
        "against the OTHER finalists, likely content-worthiness, and the strength\n"
        "of the supporting evidence. If two candidates capture substantially the\n"
        "same moment or payoff, name the stronger one as the representative and\n"
        "mark the other via redundant_with.\n\n"
        "FINALISTS:\n" + "\n\n".join(lines) + "\n\n"
        "Return ONLY valid JSON (no markdown fences):\n"
        '{"ranking": [{"candidate_id": string, "relative_rank": integer, "relative_score": number, '
        '"rationale": string, "strongest_evidence": string, "redundant_with": string|null, '
        '"confidence": number}]}\n'
        "Rules: include every candidate_id exactly once; relative_rank starts at 1 and is unique; "
        "confidence is 0..1; rationale is one sentence."
    )


# ---------------------------------------------------------------------------
# strict validation
# ---------------------------------------------------------------------------
def validate_rerank_payload(payload: Any, finalists: list[Finalist]) -> list[dict[str, Any]]:
    """Strictly validate the model payload against the finalist set."""
    if not isinstance(payload, dict):
        raise RerankValidationError("payload is not an object")
    ranking = payload.get("ranking")
    if not isinstance(ranking, list) or not ranking:
        raise RerankValidationError("missing or empty 'ranking' list")

    expected = {f.candidate_id for f in finalists}
    seen: dict[str, dict[str, Any]] = {}
    ranks: list[int] = []
    for row in ranking:
        if not isinstance(row, dict):
            raise RerankValidationError("ranking entry is not an object")
        cid = row.get("candidate_id")
        if not isinstance(cid, str) or not cid:
            raise RerankValidationError("ranking entry missing candidate_id")
        if cid not in expected:
            raise RerankValidationError(f"unknown candidate_id {cid!r}")
        if cid in seen:
            raise RerankValidationError(f"duplicate candidate_id {cid!r}")
        rank = row.get("relative_rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise RerankValidationError(f"invalid relative_rank for {cid!r}")
        ranks.append(rank)
        rw = row.get("redundant_with")
        if rw is not None and (not isinstance(rw, str) or rw not in expected or rw == cid):
            raise RerankValidationError(f"invalid redundant_with for {cid!r}")
        conf = row.get("confidence")
        if conf is not None and (not isinstance(conf, (int, float)) or isinstance(conf, bool)
                                 or not (0.0 <= float(conf) <= 1.0)):
            raise RerankValidationError(f"invalid confidence for {cid!r}")
        seen[cid] = row

    missing = expected - set(seen)
    if missing:
        raise RerankValidationError(f"missing candidate_id(s): {sorted(missing)}")
    if sorted(ranks) != list(range(1, len(finalists) + 1)):
        raise RerankValidationError(f"relative_rank must be unique 1..{len(finalists)}, got {sorted(ranks)}")
    return [seen[f.candidate_id] for f in finalists]


# ---------------------------------------------------------------------------
# transport (the existing Qwen route: OpenRouter + pinned model + same key)
# ---------------------------------------------------------------------------
def qwen_transport(prompt: str, *, api_key: str | None = None,
                   config: RerankConfig | None = None) -> tuple[str, dict[str, Any], str]:
    cfg = config or RerankConfig()
    key = api_key if api_key is not None else os.environ.get(QWEN_KEY_ENV, "")
    if not key:
        raise ProviderError("AUTH", f"{QWEN_KEY_ENV} absent", retryable=False)
    body = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_output_tokens,
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            raw = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise ProviderError("HTTP", f"Qwen HTTP {e.code}: {e.read().decode()[:200]}",
                            retryable=e.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError("NETWORK", f"Qwen transport: {type(e).__name__}", retryable=True) from None

    choices = raw.get("choices") or []
    if not choices:
        raise ProviderError("EMPTY", "no choices returned", retryable=True)
    text = (choices[0].get("message") or {}).get("content") or ""
    return text, (raw.get("usage") or {}), str(raw.get("model") or cfg.model)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def _independent_order(finalists: list[Finalist]) -> list[RerankedFinalist]:
    return [RerankedFinalist(
        candidate_id=f.candidate_id,
        independent_score=f.independent_score,
        independent_rank=f.independent_rank,
        relative_rank=f.independent_rank,
        provenance=dict(f.provenance),
    ) for f in finalists]


def relative_rerank(
    finalists: list[Finalist],
    *,
    source_context: str = "",
    transport: Transport | None = None,
    config: RerankConfig | None = None,
    provenance: dict[str, Any] | None = None,
) -> RerankResult:
    """Rerank a bounded finalist set relatively. NEVER raises on model failure."""
    cfg = config or RerankConfig()
    prov = dict(provenance or {})
    ordered = sorted(finalists, key=lambda f: (f.independent_rank, f.candidate_id))

    if len(ordered) < cfg.min_finalists:
        return RerankResult(used=False, fallback="finalist_set_too_small",
                            finalists=_independent_order(ordered), provenance=prov)

    to_rerank = ordered[:cfg.max_finalists]
    carried = ordered[cfg.max_finalists:]

    prompt = build_rerank_prompt(to_rerank, source_context, cfg)
    tx = transport or qwen_transport
    t0 = time.time()
    try:
        text, usage, model_str = tx(prompt)
    except ProviderError as e:
        return RerankResult(used=False, fallback=f"provider_error:{e.code}",
                            finalists=_independent_order(ordered), prompt_chars=len(prompt),
                            provenance=prov)
    except Exception as e:                       # never a hard failure point
        return RerankResult(used=False, fallback=f"transport_error:{type(e).__name__}",
                            finalists=_independent_order(ordered), prompt_chars=len(prompt),
                            provenance=prov)

    try:
        payload = _parse_json(text)
    except Exception:
        return RerankResult(used=False, fallback="invalid_json",
                            finalists=_independent_order(ordered), prompt_chars=len(prompt),
                            raw_excerpt=text[:300], provenance=prov)

    try:
        rows_by_rank = validate_rerank_payload(payload, to_rerank)
    except RerankValidationError:
        return RerankResult(used=False, fallback="schema_invalid",
                            finalists=_independent_order(ordered), prompt_chars=len(prompt),
                            raw_excerpt=text[:300], provenance=prov)

    row_by_id = {r["candidate_id"]: r for r in rows_by_rank}
    out: list[RerankedFinalist] = []
    for f in to_rerank:
        row = row_by_id[f.candidate_id]
        score = row.get("relative_score")
        out.append(RerankedFinalist(
            candidate_id=f.candidate_id,
            independent_score=f.independent_score,
            independent_rank=f.independent_rank,
            relative_rank=int(row["relative_rank"]),
            relative_score=(round(float(score), 4) if isinstance(score, (int, float))
                            and not isinstance(score, bool) else None),
            rationale=str(row.get("rationale") or "")[:400],
            strongest_evidence=str(row.get("strongest_evidence") or "")[:300],
            redundant_with=row.get("redundant_with"),
            confidence=(round(float(row["confidence"]), 4) if row.get("confidence") is not None else None),
            reranked=True,
            provenance=dict(f.provenance),
        ))
    # Deterministic tie-break: relative_score desc, independent_score desc, id asc.
    out.sort(key=lambda r: (-(r.relative_score if r.relative_score is not None else -1.0),
                            -r.independent_score, r.candidate_id))
    for i, r in enumerate(out, start=1):
        r.relative_rank = i

    carried_out = [RerankedFinalist(
        candidate_id=f.candidate_id, independent_score=f.independent_score,
        independent_rank=f.independent_rank, relative_rank=len(out) + i,
        reranked=False, provenance=dict(f.provenance))
        for i, f in enumerate(carried, start=1)]

    in_tok = int(usage.get("prompt_tokens", 0) or 0)
    out_tok = int(usage.get("completion_tokens", 0) or 0)
    usage_obj = ProviderUsage(
        input_tokens=in_tok, output_tokens=out_tok,
        latency_s=round(time.time() - t0, 2),
        cost_usd=_estimate_cost("qwen", in_tok, out_tok),
    )
    return RerankResult(
        used=True, fallback="", finalists=out + carried_out,
        model_identity=ModelIdentity(provider="qwen", requested_model=cfg.model,
                                     resolved_model=model_str, resolved_version=model_str),
        usage=usage_obj, prompt_chars=len(prompt),
        reranked_count=len(out), carried_count=len(carried_out),
        raw_excerpt=text[:300], provenance=prov,
    )
