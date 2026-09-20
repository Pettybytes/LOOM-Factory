# LOOM — Content Intelligence Preflight V1 + Combined Source Selection V1

The missing content-understanding layer. Topic Intelligence answers "is the
internet showing demand for this source?" (market evidence). Content
Intelligence answers "does the source contain a compelling experience?" —
strictly gated by evidence actually inspected.

## Truth boundary

LOOM never infers visual or audio qualities from a title alone. It never
reports facial emotion, physical action, vocal intensity, or visual payoff
unless the corresponding media evidence was actually inspected. Every content
dimension has a minimum evidence level; the assessment schema rejects any score
that exceeds its evidence level.

## Evidence levels

| Level | Name | Can score | Confidence ceiling |
|---|---|---|---|
| 0 | metadata_only | structural/deterministic dims only (tenant_fit, format_fit, saturation, originality) | 0.35 |
| 1 | text_content | + semantic text dims (hook, emotion, conflict, surprise, payoff, story, …) | 0.60 |
| 2 | static_visuals | + visual_interest | 0.70 |
| 3 | bounded_media | + vocal_intensity + confirms visual/audio payoff | 0.95 |
| 4 | full_content | **NOT authorized** | — |

`visual_interest` requires level ≥ 2; `vocal_intensity` requires level ≥ 3.
Content confidence is capped by the highest level actually inspected.

## Modules

| Module | Responsibility |
|---|---|
| `evidence.py` | Evidence-level contract, dimension gating, confidence ceilings |
| `contract.py` | Strict Content Assessment schema + validation (reject malformed / out-of-bounds / evidence-exceeding) |
| `selection.py` | Combined Source Selection (35/45/15/5) + separate confidence + decision rules |
| `provider.py` | Evidence bundling, model-provider interface, deterministic MockProvider, orchestration |
| `probe.py` | Bounded media probe contract (temp lifecycle, hard limits, no full download) |
| `store.py` | SQLite persistence (idempotent; new evidence version → new row) |

Migrations: `infra/supabase/025_loom_content_assessments.sql`,
`026_loom_source_selections.sql` (created; NOT yet applied).

## Combined Source Selection (V1 policy)

| Signal | Weight |
|---|---|
| Market opportunity (Topic Intelligence) | 35% |
| Content worthiness (Content Intelligence) | 45% |
| Tenant + format fit | 15% |
| Provenance + usability | 5% |

Score and confidence are computed **independently** and kept separate. A high
score from metadata-only evidence carries low confidence through.

Decision outcomes: `fast_track`, `hold_hype`, `emerging`, `reject`,
`human_review_low_confidence`, `differentiation_required`, `standard_review`.

## Two-pass preflight

- **Pass 1 (cheap text triage):** transcript/body/title/description → semantic
  text signals + format fit + weak-candidate rejection. No media download.
- **Pass 2 (bounded moment probe):** short, precisely targeted media windows
  around transcript-derived timestamps. Hard limits (candidates / windows /
  seconds / bytes / cost). Transient media deleted after inspection. Only
  metadata + scores + reasons + provenance are preserved.

## Status matrix

| Area | Status |
|---|---|
| Evidence-level contract + gating | **BUILT + TESTED** |
| Content assessment schema + validation | **BUILT + TESTED** |
| Combined selection + decision rules | **BUILT + TESTED** |
| Bounded-media probe contract | **BUILT + TESTED** (mock fetch; real fetch deferred) |
| SQLite persistence | **BUILT + TESTED** |
| Supabase persistence (migrations 025/026) | **APPLIED + RUNTIME-PROVEN** (Pass-1 rows written idempotently) |
| Semantic scoring (hook/emotion/…) via real model | **RUNTIME-PROVEN** (Pass-1, Gemini Flash-Lite) |
| Real transcript inspection | **RUNTIME-PROVEN** (Pass-1) |
| Real provider layer (Gemini + Qwen) | **BUILT + TESTED** (pinned models, classified failures, cost/token accounting) |
| Recurring Pass-1 pipeline | **BUILT + TESTED + RUNTIME-PROVEN** (9/9 e2e) |
| Selection queue contract | **BUILT + TESTED** |
| A/B: Gemini vs Qwen | **DONE** — default = Gemini Flash-Lite (57× faster, 10× fewer output tokens) |
| Real thumbnail / bounded-media inspection | **DEFERRED** (Pass 2 — media gate) |
| Source Review Inbox | **PAUSED** (resumes after this segment) |

## Pass-1 real proof (transcript-only, Gemini Flash-Lite, < $0.05)

```sh
python3 infra/content_intelligence/prove_pass1.py
```

Real YouTube candidate (Linus Tech Tips) → English auto-captions via yt-dlp
(skip-download, 0 video/audio bytes, no playlist, no cookies) → Gemini
Flash-Lite scores the transcript (evidence level 1) → strict Content Assessment
→ Combined Source Selection → idempotent Supabase persistence. 13/13 acceptance
criteria; cost ≈ $0.002 (well under the $0.05 ceiling).

## Default provider + pinned model

A/B (same transcript, same schema/settings) at `ab_gemini_qwen.py`:

| | Gemini Flash-Lite | OpenRouter Qwen `qwen3.5-9b` |
|---|---|---|
| resolved | `gemini-3.5-flash-lite` | `qwen/qwen3.5-9b` (SiliconFlow) |
| latency | **2.6s** | 146s |
| output tokens | **560** | 5798 |
| worthiness | 68 | 82 |
| schema-valid / evidence-grounded | ✅ | ✅ |

**Default transcript-triage provider: Gemini Flash-Lite** (pinned
`gemini-3.5-flash-lite` — not the `-latest` alias). It is ~57× faster and ~10×
more concise at effectively equivalent quality and cost. Qwen remains an
explicit secondary provider (no silent fallback).

## Recurring Pass-1 pipeline

```sh
python3 infra/content_intelligence/prove_pipeline.py
```

`pipeline.Pass1Pipeline` is the bounded production path: discovery → Topic
Intelligence → market shortlist threshold → transcript-only retrieval (yt-dlp
skip-download) → real provider → Content Intelligence → Combined Selection →
idempotent Supabase persistence. Hard limits (candidates / transcript chars /
tokens / cost ceiling). `selection_queue()` returns only completed selections
eligible for the future Source Review queue.

## Runtime requirements

- **Python 3 — standard library only.** No third-party packages. The multimodal
  signal layer deliberately carries no numpy dependency so the deterministic
  path needs no install step.
- **`ffmpeg` on PATH** — required only by the multimodal signal layer
  (`multimodal_signals.py`) to decode audio/video. `ffprobe` is not used;
  duration is derived from decoded sample counts. Every ffmpeg call is bounded
  by a hard 300s timeout and by `max_seconds`, and a killed decode raises
  rather than silently reporting empty media.
- **$0, no model call.** Signal extraction is local and deterministic; the
  semantic provider is still the only thing that judges content.

## Tests

```sh
python3 -m unittest discover -s infra/content_intelligence/tests -v
```

Also runnable from inside this directory as `python3 -m unittest discover -s tests`.

97 deterministic tests: evidence gating, validation (reject malformed /
out-of-bounds / evidence-exceeding), idempotent replay, versioned assessments,
decision rules, format fit, bounded-probe cleanup + failure, proof-profile
exclusion, boundary guards (no OpenShorts/Discord/GHL imports), the
candidate-window contract (normalize / salience gate / peaks / windows /
fusion-with-provenance), real-media extraction, and acceptance items A-F on real
bytes.

The suite REQUIRES `fixtures/test.mp4`; if it is missing those tests FAIL rather
than skip, so a green run always means the media path actually executed.
