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
| Supabase persistence (migrations 025/026) | **SCHEMA CREATED** (not applied; no real content assessment yet) |
| Semantic scoring (hook/emotion/…) via real model | **DEFERRED** (paid/model gate) |
| Real transcript / thumbnail / bounded-media inspection | **DEFERRED** (paid/media gate) |
| Source Review Inbox | **PAUSED** (resumes after this segment) |

## Tests

```sh
python3 -m unittest discover -s infra/content_intelligence/tests -v
```

42 deterministic tests: evidence gating, validation (reject malformed /
out-of-bounds / evidence-exceeding), idempotent replay, versioned assessments,
decision rules, format fit, bounded-probe cleanup + failure, proof-profile
exclusion, boundary guards (no OpenShorts/Discord/GHL imports).
