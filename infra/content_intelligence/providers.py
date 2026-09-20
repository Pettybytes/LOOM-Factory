"""Real (production) Content Intelligence model providers.

Promoted out of the Pass-1 proof script. Contains the reusable Gemini and
OpenRouter Qwen transcript-triage providers plus classified errors, model
identity + usage accounting.

Production requirements met here:
  * credentials loaded by name from the LOOM env files (never printed/logged);
  * strict structured response (JSON parsed + schema-validated upstream);
  * evidence gating (upstream, in ``provider.assess_content``);
  * low temperature (0.2), bounded output, timeout;
  * classified failures (``ProviderError``);
  * cost + token accounting (``ProviderUsage``);
  * provider/model/version persistence (``ModelIdentity``, pinned models);
  * NO silent model fallback — each provider is explicit.

Pinned models (from the A/B at commit context): Gemini ``gemini-3.5-flash-lite``
and Qwen ``qwen/qwen3.5-9b`` (OpenRouter). Default transcript provider is
Gemini Flash-Lite (fast + concise; see ab_gemini_qwen.py).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# Pinned model identifiers (do NOT use "-latest" aliases for recurring work).
GEMINI_PINNED_MODEL = "gemini-3.5-flash-lite"
QWEN_PINNED_MODEL = "qwen/qwen3.5-9b"

DEFAULT_TRANSCRIPT_PROVIDER = "gemini"

# Approximate per-token pricing ($/M tokens) — documented estimates for cost
# accounting (not billing). Gemini Flash-Lite and a 9B-class OpenRouter model.
_PRICING: dict[str, tuple[float, float]] = {
    "gemini": (0.075, 0.30),
    "qwen": (0.02, 0.06),
}

# Env files LOOM sources secrets from (existing mechanism, names only).
_ENV_FILES = ("~/.openclaw/.env", "infra/tunnel-proxy/.env")


class ProviderError(RuntimeError):
    """Classified provider failure (machine-readable code, no secret values)."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    requested_model: str
    resolved_model: str = ""
    resolved_version: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "requested_model": self.requested_model,
            "resolved_model": self.resolved_model,
            "resolved_version": self.resolved_version,
        }


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int
    output_tokens: int
    latency_s: float
    cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_s": self.latency_s,
            "cost_usd": self.cost_usd,
        }


def load_key(env_var: str, *extra_paths: str) -> str:
    """Load a credential by NAME from the LOOM env files. Never prints value."""
    paths = list(extra_paths) + [os.path.expanduser(p) for p in _ENV_FILES]
    seen: set[str] = set()
    for p in paths:
        if not os.path.exists(p) or p in seen:
            continue
        seen.add(p)
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line.startswith(f"{env_var}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _estimate_cost(provider: str, input_tokens: int, output_tokens: int) -> float:
    ip, op = _PRICING.get(provider, (0.0, 0.0))
    return round((input_tokens * ip + output_tokens * op) / 1_000_000.0, 6)


def _parse_json(text: str):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if not m:
        raise ProviderError("INVALID_RESPONSE", "no JSON object in model output")
    return json.loads(m.group(0))


def _build_prompt(transcript_text: str, max_chars: int, signal_windows: list | None = None) -> str:
    dims = ["hook", "emotion", "conflict_tension", "surprise", "humor", "relatability",
            "stakes", "story_progression", "payoff", "narrative_completeness",
            "context_independence", "reaction_potential", "commentary_value", "originality_novelty"]
    formats = ["reaction_single", "reaction_compilation", "commentary_story",
               "ranking_top5", "stream_highlight", "hold_unassigned"]
    excerpt = transcript_text[:max_chars]

    signal_context = ""
    if signal_windows:
        signal_context = (
            "Several moments of unusually high audio or visual intensity "
            "(relative to the rest of this video, deterministic and within-source "
            "only — not a claim of importance or virality) were also detected and "
            "may not be apparent from the transcript alone. Use them to inform "
            "candidate_timestamps and reasons where they align with, or provide a "
            "visual payoff to, the spoken content. These signals are RELATIVE "
            "intensity measurements inside this source only; they are never "
            "evidence that a moment is important, high quality, or viral, and "
            "they do not replace your reading of the transcript.\n"
            "Signal windows:\n"
        )
        for w in signal_windows:
            peak = w.get("peak_s")
            if peak is not None:
                signal_context += (
                    f"- {w['start_s']:.1f}s to {w['end_s']:.1f}s "
                    f"(peak {peak:.1f}s): {w.get('basis', 'signal')}\n"
                )
            else:
                signal_context += (
                    f"- {w['start_s']:.1f}s to {w['end_s']:.1f}s: "
                    f"{w.get('basis', 'signal')}\n"
                )
        signal_context += "\n"

    # The transcript-only framing (no media evidence inspected) stays exactly as
    # it was: with no signal windows the model must not infer audio/visual
    # qualities it cannot observe. Only when deterministic media signals were
    # actually extracted does the instruction admit them, and then only as
    # relative within-source intensity.
    if signal_windows:
        scoring_rule = (
            "Score each dimension as an integer 0-100, based primarily on the "
            "transcript text and informed by the deterministic audio/visual "
            "salience signals listed below where they add evidence (within-source "
            "relative intensity only, never a claim of semantic importance).\n\n"
        )
    else:
        scoring_rule = (
            "Score each dimension as an integer 0-100, based ONLY on the transcript text "
            "(do NOT infer visual or audio qualities you cannot observe from text).\n\n"
        )

    return (
        "You are a content analyst for a short-form video operation. Analyze the "
        "following video TRANSCRIPT (with timestamps) for short-form content potential.\n\n"
        f"{scoring_rule}"
        f"Dimensions: {', '.join(dims)}.\n\n"
        f"{signal_context}"
        "Also provide:\n"
        "- content_worthiness_score: overall 0-100 for a short clip.\n"
        "- candidate_timestamps: up to 5 {start_s, end_s, basis} for the most promising clip moments.\n"
        f"- recommended_content_format: one of {formats}.\n"
        "- recommended_angle: one-sentence angle for the creator's commentary.\n"
        "- reasons: 3-6 {code, signal, message} explaining the strongest signals.\n\n"
        "Return ONLY valid JSON (no markdown fences) with this exact shape:\n"
        '{"scores": {"<dim>": int, ...}, "content_worthiness_score": int, '
        '"candidate_timestamps": [{"start_s": number, "end_s": number, "basis": string}], '
        '"recommended_content_format": string, "recommended_angle": string, '
        '"reasons": [{"code": string, "signal": string, "message": string}]}\n\n'
        f"TRANSCRIPT:\n{excerpt}"
    )


class GeminiProvider:
    """Gemini Flash-Lite transcript-triage provider (pinned model)."""

    name = "gemini"
    version = GEMINI_PINNED_MODEL

    def __init__(self, api_key: str, model: str = GEMINI_PINNED_MODEL,
                 temperature: float = 0.2, max_output_tokens: int = 1200,
                 timeout: float = 60.0, max_transcript_chars: int = 12000) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.max_transcript_chars = max_transcript_chars

    def assess(self, evidence_bundle: Any, brief: Any) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError("AUTH", "LOOM_GEMINI_KEY absent", retryable=False)
        prompt = _build_prompt(evidence_bundle.transcript_text, self.max_transcript_chars,
                              getattr(evidence_bundle, "signal_windows", None))
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               + self.model + ":generateContent?key=" + self.api_key)
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperature,
                                 "maxOutputTokens": self.max_output_tokens},
        }
        t0 = time.time()
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            raise ProviderError("HTTP", f"Gemini HTTP {e.code}: {detail}",
                                retryable=e.code in (429, 500, 502, 503)) from e
        except urllib.error.URLError as e:
            raise ProviderError("NETWORK", f"Gemini network: {e.reason}", retryable=True) from e

        cand = (raw.get("candidates") or [{}])[0]
        text = cand.get("content", {}).get("parts", [{}])[0].get("text", "")
        um = raw.get("usageMetadata", {})
        in_tok = int(um.get("promptTokenCount", 0))
        out_tok = int(um.get("candidatesTokenCount", 0))
        parsed = _parse_json(text)
        parsed["model_identity"] = ModelIdentity(
            provider=self.name, requested_model=self.model,
            resolved_model=self.model,
            resolved_version=raw.get("modelVersion", ""),
        ).to_dict()
        parsed["usage"] = ProviderUsage(
            input_tokens=in_tok, output_tokens=out_tok,
            latency_s=round(time.time() - t0, 2),
            cost_usd=_estimate_cost("gemini", in_tok, out_tok),
        ).to_dict()
        return parsed


# Structured-output schema for the Qwen assessment response. Mirrors EXACTLY the
# shape `_build_prompt` has always demanded, so the contract is unchanged:
#   {"scores", "content_worthiness_score", "candidate_timestamps",
#    "recommended_content_format", "recommended_angle", "reasons"}
QWEN_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "content_assessment",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "scores": {"type": "object", "additionalProperties": {"type": "integer"}},
            "content_worthiness_score": {"type": "integer"},
            "candidate_timestamps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"start_s": {"type": "number"},
                                   "end_s": {"type": "number"},
                                   "basis": {"type": "string"}},
                    "required": ["start_s", "end_s", "basis"],
                    "additionalProperties": False,
                },
            },
            "recommended_content_format": {"type": "string"},
            "recommended_angle": {"type": "string"},
            "reasons": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"code": {"type": "string"},
                                   "signal": {"type": "string"},
                                   "message": {"type": "string"}},
                    "required": ["code", "signal", "message"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["scores", "content_worthiness_score",
                     "recommended_content_format", "recommended_angle", "reasons"],
        "additionalProperties": False,
    },
}


class QwenProvider:
    """OpenRouter Qwen transcript-triage provider (pinned model)."""

    name = "qwen"
    version = QWEN_PINNED_MODEL

    def __init__(self, api_key: str, model: str = QWEN_PINNED_MODEL,
                 temperature: float = 0.2, max_output_tokens: int = 1200,
                 timeout: float = 180.0, max_transcript_chars: int = 12000,
                 reasoning_enabled: bool = False,
                 structured_output: str = "json_schema") -> None:
        """Pinned-model provider for structured transcript triage.

        `reasoning_enabled=False` is deliberate and scoped to this provider: the
        pinned model is a REASONING model, and with thinking on it consumed the
        entire output budget before emitting any answer (empty completion ->
        INVALID_RESPONSE), failing 100% of observed calls at the default budget
        and remaining unreliable at 6000/16000. Non-thinking was measured at
        337-665 output tokens for this exact prompt, so the bounded
        `max_output_tokens` is sized to the schema rather than to reasoning.
        Callers with genuinely reasoning-shaped work can opt in explicitly.
        """
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.max_transcript_chars = max_transcript_chars
        self.reasoning_enabled = reasoning_enabled
        self.structured_output = structured_output

    def assess(self, evidence_bundle: Any, brief: Any) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError("AUTH", "LOOM_OPENROUTER_API_KEY absent", retryable=False)
        prompt = _build_prompt(evidence_bundle.transcript_text, self.max_transcript_chars,
                              getattr(evidence_bundle, "signal_windows", None))
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            # Explicit, always present: the request can never silently fall back
            # to thinking-on with a reasoning-sized budget.
            "reasoning": {"enabled": bool(self.reasoning_enabled)},
        }
        if self.structured_output == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                      "json_schema": QWEN_RESPONSE_SCHEMA}
        elif self.structured_output == "json_object":
            body["response_format"] = {"type": "json_object"}
        else:
            raise ProviderError("BAD_CONFIG",
                                f"unsupported structured_output {self.structured_output!r}",
                                retryable=False)
        t0 = time.time()
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + self.api_key},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            raise ProviderError("HTTP", f"Qwen HTTP {e.code}: {detail}",
                                retryable=e.code in (429, 500, 502, 503)) from e
        except urllib.error.URLError as e:
            raise ProviderError("NETWORK", f"Qwen network: {e.reason}", retryable=True) from e

        choice = (raw.get("choices") or [{}])[0]
        text = choice.get("message", {}).get("content", "")
        usage = raw.get("usage", {})
        in_tok = int(usage.get("prompt_tokens", 0))
        out_tok = int(usage.get("completion_tokens", 0))
        parsed = _parse_json(text)
        parsed["model_identity"] = ModelIdentity(
            provider=self.name, requested_model=self.model,
            resolved_model=raw.get("model", self.model),
            resolved_version="",
        ).to_dict()
        parsed["usage"] = ProviderUsage(
            input_tokens=in_tok, output_tokens=out_tok,
            latency_s=round(time.time() - t0, 2),
            cost_usd=_estimate_cost("qwen", in_tok, out_tok),
        ).to_dict()
        return parsed


def build_provider(name: str, api_key: str, **kw: Any):
    """Explicit provider factory — NO silent fallback."""
    if name == "gemini":
        return GeminiProvider(api_key, **kw)
    if name == "qwen":
        return QwenProvider(api_key, **kw)
    raise ProviderError("UNKNOWN_PROVIDER", f"no provider {name!r} (gemini|qwen)", retryable=False)
