"""A/B comparison: Gemini Flash-Lite vs OpenRouter Qwen (transcript triage).

Runs the SAME transcript excerpt through both models with identical schema +
settings, then compares schema validity, evidence grounding, unsupported
claims, timestamp usefulness, worthiness, confidence, format, angle, latency,
tokens and cost. Loads credentials by name from files (never prints/commits).

Run from repo root:  python3 infra/content_intelligence/ab_gemini_qwen.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

GEMINI_MODEL = "gemini-flash-lite-latest"
QWEN_MODEL = "qwen/qwen3.5-9b"
MAX_TRANSCRIPT_CHARS = 12000
TEMPERATURE = 0.2
MAX_OUTPUT_TOKENS = 1200

_DIMS = ["hook", "emotion", "conflict_tension", "surprise", "humor", "relatability",
         "stakes", "story_progression", "payoff", "narrative_completeness",
         "context_independence", "reaction_potential", "commentary_value", "originality_novelty"]
_FORMATS = ["reaction_single", "reaction_compilation", "commentary_story",
            "ranking_top5", "stream_highlight", "hold_unassigned"]


def _load_env_key(env_path: str, var: str) -> str:
    p = Path(env_path)
    if not p.exists():
        return ""
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{var}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _load_openrouter_key() -> str:
    p = Path.home() / "Desktop" / "Jasmine-Phase1" / "docs" / "TOOLS.md"
    if not p.exists():
        return ""
    in_section = False
    for line in p.read_text(encoding="utf-8").splitlines():
        ls = line.strip()
        if ls.upper().startswith("### OPENROUTER"):
            in_section = True
            continue
        if in_section and ls.startswith("### "):
            break
        if in_section and ls.startswith("- API Key:"):
            if "`" in ls:
                return ls.split("`")[1]
            return ls.split(":", 1)[1].strip()
    return ""


def _fetch_transcript(url: str) -> str:
    tmp = tempfile.mkdtemp(prefix="loom-ab-")
    try:
        subprocess.run(
            ["yt-dlp", "--skip-download", "--no-playlist", "--no-update",
             "--write-auto-subs", "--sub-langs", "en.*", "--sub-format", "vtt",
             "-o", f"{tmp}/%(id)s", url],
            capture_output=True, text=True, timeout=120,
        )
        vtts = list(Path(tmp).glob("*.vtt"))
        if not vtts:
            return ""
        raw = vtts[0].read_text(encoding="utf-8", errors="replace")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    segs, cur = [], None
    for line in raw.splitlines():
        if "-->" in line:
            if cur:
                segs.append(cur)
            cur = {"text": []}
        elif cur is not None and line.strip():
            cur["text"].append(line.strip())
    if cur:
        segs.append(cur)
    return " ".join(" ".join(s["text"]) for s in segs if s.get("text"))


def _build_prompt(excerpt: str) -> str:
    return (
        "You are a content analyst for a short-form video operation. Analyze the "
        "following video TRANSCRIPT (with timestamps) for short-form content potential.\n\n"
        "Score each dimension as an integer 0-100, based ONLY on the transcript text "
        "(do NOT infer visual or audio qualities you cannot observe from text).\n\n"
        f"Dimensions: {', '.join(_DIMS)}.\n\n"
        "Also provide:\n"
        "- content_worthiness_score: overall 0-100 for a short clip.\n"
        "- candidate_timestamps: up to 5 {start_s, end_s, basis} for the most promising clip moments.\n"
        f"- recommended_content_format: one of {_FORMATS}.\n"
        "- recommended_angle: one-sentence angle for the creator's commentary.\n"
        "- reasons: 3-6 {code, signal, message} explaining the strongest signals.\n\n"
        "Return ONLY valid JSON (no markdown fences) with this exact shape:\n"
        '{"scores": {"<dim>": int, ...}, "content_worthiness_score": int, '
        '"candidate_timestamps": [{"start_s": number, "end_s": number, "basis": string}], '
        '"recommended_content_format": string, "recommended_angle": string, '
        '"reasons": [{"code": string, "signal": string, "message": string}]}\n\n'
        f"TRANSCRIPT:\n{excerpt}"
    )


def _parse_json(text: str):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    return json.loads(m.group(0)) if m else None


def call_gemini(key: str, prompt: str):
    t0 = time.time()
    url = "https://generativelanguage.googleapis.com/v1beta/models/" + GEMINI_MODEL + ":generateContent?key=" + key
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": TEMPERATURE, "maxOutputTokens": MAX_OUTPUT_TOKENS}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode())
    cand = (resp.get("candidates") or [{}])[0]
    text = cand.get("content", {}).get("parts", [{}])[0].get("text", "")
    um = resp.get("usageMetadata", {})
    return _parse_json(text), {
        "requested_model": GEMINI_MODEL,
        "resolved_model_version": resp.get("modelVersion", ""),
        "input_tokens": um.get("promptTokenCount", 0),
        "output_tokens": um.get("candidatesTokenCount", 0),
        "latency_s": round(time.time() - t0, 2),
    }


def call_qwen(key: str, prompt: str):
    t0 = time.time()
    body = {"model": QWEN_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": TEMPERATURE, "max_tokens": MAX_OUTPUT_TOKENS}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode())
    choice = (resp.get("choices") or [{}])[0]
    text = choice.get("message", {}).get("content", "")
    usage = resp.get("usage", {})
    return _parse_json(text), {
        "requested_model": QWEN_MODEL,
        "resolved_model": resp.get("model", ""),
        "provider": resp.get("provider", ""),
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "latency_s": round(time.time() - t0, 2),
    }


def _validate(out: dict | None, name: str) -> dict:
    if out is None:
        return {"schema_valid": False, "error": "no JSON"}
    scores = out.get("scores") or {}
    issues = []
    for d, v in scores.items():
        if not isinstance(v, int) or v < 0 or v > 100:
            issues.append(f"{d}={v}")
    # Evidence grounding: no visual/audio claims at text level.
    unsupported = [k for k in scores if k in ("visual_interest", "vocal_intensity")]
    return {
        "schema_valid": isinstance(scores, dict) and isinstance(out.get("content_worthiness_score"), int),
        "n_scores": len(scores),
        "score_issues": issues,
        "unsupported_claims": unsupported,
        "n_timestamps": len(out.get("candidate_timestamps") or []),
        "worthiness": out.get("content_worthiness_score"),
        "format": out.get("recommended_content_format"),
        "angle": (out.get("recommended_angle") or "")[:120],
    }


def main() -> int:
    gk = _load_env_key("infra/tunnel-proxy/.env", "LOOM_GEMINI_KEY")
    ok = _load_openrouter_key()
    print("Gemini key present:", bool(gk), "| OpenRouter key present:", bool(ok))
    if not (gk and ok):
        print("ABORT: missing credential (name-only lookup)")
        return 2

    transcript = _fetch_transcript("https://www.youtube.com/watch?v=qgLaCZyKv_8")
    if not transcript:
        print("ABORT: no transcript")
        return 2
    excerpt = transcript[:MAX_TRANSCRIPT_CHARS]
    prompt = _build_prompt(excerpt)
    print(f"transcript: {len(transcript)} chars; excerpt {len(excerpt)} chars")

    results = {}
    for name, fn in (("gemini", call_gemini), ("qwen", call_qwen)):
        try:
            out, meta = fn(gk if name == "gemini" else ok, prompt)
            results[name] = {"meta": meta, "validate": _validate(out, name), "raw": out}
        except Exception as e:
            results[name] = {"error": f"{type(e).__name__}: {str(e)[:400]}"}

    print("\n=== RESULTS ===")
    for name, r in results.items():
        print(f"\n--- {name.upper()} ---")
        if "error" in r:
            print("  ERROR:", r["error"])
            continue
        print("  meta:", json.dumps(r["meta"]))
        print("  validate:", json.dumps(r["validate"], indent=2))

    # Cost estimate (documented per-token pricing, rough).
    print("\n=== COST ESTIMATE ===")
    pricing = {
        "gemini": (0.075, 0.30),   # $/M input, $/M output (flash-lite approx)
        "qwen": (0.02, 0.06),       # $/M input, $/M output (9B-class approx)
    }
    for name, r in results.items():
        if "error" in r or "meta" not in r:
            continue
        m = r["meta"]
        ip, op = pricing[name]
        cost = (m["input_tokens"] * ip + m["output_tokens"] * op) / 1e6
        print(f"  {name}: {cost:.5f} USD (in={m['input_tokens']} out={m['output_tokens']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
