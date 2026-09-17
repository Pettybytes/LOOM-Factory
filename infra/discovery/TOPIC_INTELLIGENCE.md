# LOOM — Topic Intelligence V1

Deterministic, auditable opportunity scoring for discovered YouTube and Reddit
sources. This is the layer immediately after Source Discovery V1: it decides
*which* discovered candidates deserve attention, using only observable evidence.

**Truth boundary — what Topic Intelligence does NOT judge:**

- emotional content inside a video
- spoken hook quality / visual hook quality
- surprise, conflict, payoff, narrative completeness
- whether an exact moment will make a strong clip

Those require later transcript/audio/visual inspection (Moment Mining / Clip
Ranking). Topic Intelligence only identifies *external, observable* evidence
that a source or topic is gaining traction — and labels every signal precisely.

---

## Status matrix

| Area | Status |
|---|---|
| Normalization (platform-aware) | **BUILT + TESTED** |
| Velocity (observation history) | **BUILT + TESTED** |
| Deterministic scoring + reason codes | **BUILT + TESTED** |
| Opportunity class + confidence + flags | **BUILT + TESTED** |
| Assessment store (SQLite) | **BUILT + TESTED** |
| Assessment store (Supabase) | **BUILT** (not yet runtime-proven — migration 022 not applied) |
| Real-data scoring proof (local, $0) | **RUNTIME-PROVEN** (28/28) |
| Supabase-backed acceptance proof | **DEFERRED** (needs migrations 022/023 applied by Bryan) |
| vidIQ evidence adapter | **DEFERRED** (optional, not a dependency) |
| Discord source review inbox | **DEFERRED** (next segment) |
| Moment Mining / Clip Ranking | **DEFERRED** |
| Streamer Moment Scout (Twitch/Kick) | **DEFERRED** (documented below) |
| TikTok discovery | **HELD** (awaiting explicit compliant access decision) |

---

## Modules

| Module | Responsibility |
|---|---|
| `normalization.py` | Platform-aware signal normalization → `NormalizedSignals` |
| `velocity.py` | Velocity computed ONLY from valid time-separated observations |
| `topic_intelligence.py` | Deterministic scoring, reason codes, opportunity class |

### Normalized signals (`normalization.py`)

`normalize_signals(record)` maps a platform-specific `observed_metrics` into a
shared contract without pretending YouTube and Reddit metrics are identical:

| Field | YouTube (RSS) | Reddit (RSS) |
|---|---|---|
| `volume` | `views` (from `media:statistics`) | **absent** (score/ups need JSON/OAuth — unused) |
| `likes` | **absent** (Data API only) | absent |
| `comments` | **absent** (Data API only) | absent |
| `engagement_rate` | absent (needs likes+comments) | absent |
| `source_url_references` | absent | present (external links in body) |

Absent metrics are recorded in `missing_metrics` (never zeroed, never estimated).

### Velocity (`velocity.py`)

`compute_velocity(history, metric_key)`:

- Requires **≥2 time-separated observations** of a numeric metric.
- `value_per_hour = (latest − earliest) / hours`.
- Duplicate timestamps collapse (keep the later value).
- Returns `available=False` with a reason when velocity cannot be computed
  (single observation, no numeric metric, or non-time-separated).

Connectors already append one observation per discovery pass, so as LOOM
re-observes a source over time, velocity becomes available automatically —
no fabrication, no extrapolation.

---

## Scoring model

### Opportunity score (0–100)

Weighted sum over seven signals (weights sum to 1.0):

| Signal | Weight | How scored |
|---|---|---|
| freshness | 0.20 | linear decay of age vs. freshness window |
| engagement | 0.25 | log-scale volume (`log10(views+1)/6` → 100 @ 1M) + engagement-rate bonus when likes+comments present |
| velocity | 0.15 | `value_per_hour / 1000` (1000/hr → 100); negative → 0 |
| relevance | 0.20 | matched brief terms / 3 (3+ matches → 100) |
| provenance | 0.08 | `evidence_confidence × 100` (or 50 if present w/o numeric confidence) |
| cross_platform | 0.07 | 34 per external source reference (Reddit links) |
| saturation | 0.05 | `100 − 20 × competition_count` (inverse) |

A **missing** signal contributes **0** to the score *and* lowers confidence —
its weight is **not** redistributed to present signals. This is what makes
missing data *reduce* confidence rather than cause invented values.

### Confidence & data completeness

- `data_completeness` = sum of weights of **present** signals (0–1).
- `confidence` = `data_completeness × evidence_confidence` (evidence_confidence defaults to 1.0 when absent).

### Opportunity class (precedence order)

1. `duplicate` — identity evidence (exact/repost/crosspost/near)
2. `stale` — publication outside freshness window
3. `incomplete` — confidence < 0.50 or completeness < 0.35
4. `strong` — score ≥ 70
5. `moderate` — score ≥ 40
6. `weak` — otherwise

### Reason codes (stable vocabulary)

Every score is explainable via a list of `{code, signal, message}`. Representative codes:

- Freshness: `FRESH_WINDOW`, `STALE_OUT_OF_WINDOW`, `FRESH_MISSING_TS`
- Engagement: `ENGAGE_VOLUME_PRESENT`, `ENGAGE_RATE_PRESENT`, `ENGAGE_RATE_MISSING`, `ENGAGE_NO_METRICS`
- Velocity: `VELOCITY_AVAILABLE`, `VELOCITY_NEGATIVE`, `VELOCITY_UNAVAILABLE`
- Relevance: `RELEVANCE_MATCH`, `RELEVANCE_NONE`, `RELEVANCE_NO_TERMS`
- Provenance: `PROVENANCE_PRESENT`, `PROVENANCE_MISSING`
- Cross-platform: `CROSS_PLATFORM_LINKS`, `CROSS_PLATFORM_NONE`
- Saturation: `SATURATION_NONE`, `SATURATION_HIGH`, `SATURATION_UNKNOWN`
- Flags: `MISSING_<METRIC>`, `MISSING_PUBLICATION_TS`, `VELOCITY_UNAVAILABLE`, `LOW_CONFIDENCE`, `DUPLICATE_<KIND>`

### Determinism

Scoring is pure (no randomness, no LLM, no paid service, no scraping). The only
time-varying input is `now` (freshness horizon), which is an explicit injectable
parameter — the same observation with the same `now` always yields the same
score and reason codes.

---

## Storage

- Raw observations stay in `loom_discovered_sources` (`observed_metrics` +
  `metric_history`).
- Derived scores go in `loom_topic_assessments` (migration 022), keyed by
  `(duplicate_fingerprint, score_version)` — re-scoring with the same version
  upserts in place (never appends a duplicate, never corrupts score history).
- `profile_id` is stored on each assessment so `production_assessments()`
  excludes acceptance/test/proof profiles (same as discovery rows).

---

## content_format routing field

`content_format` (migration 023) is the content routing field. It defaults to
`hold_unassigned` — nothing is auto-routed. Reserved values:

- `reaction_single`
- `reaction_compilation`
- `commentary_story`
- `ranking_top5`
- `stream_highlight`
- `hold_unassigned`

---

## Deferred: Streamer Moment Scout (Twitch/Kick)

A future non-ranking content lane for reaction-worthy moments from selected
Twitch and Kick streamers. **Not authorized during Topic Intelligence V1.**

The future design will discover a streamer/VOD/clip, detect promising
timestamps via lightweight signals (native clip views, chat-message velocity,
emote bursts, viewer-count changes, clip clusters, repeated phrases, stream
title/category relevance, cross-platform reposts), inspect only a bounded
window around each timestamp, then require human approval before any
acquisition/editing/publication.

Fields the contracts must later support (kept deferred — no dead-end):

`platform`, `creator_id`, `broadcast_id`, `vod_id`, `clip_id`,
`source_start_seconds`, `source_end_seconds`, `signal_types`,
`content_format`, `reaction_fit_score`.

`platform` and `content_format` already exist. The streamer-specific fields
will live on a future streamer-moment table (or source-type extension) — they
are **not** crammed into `loom_discovered_sources` / `loom_topic_assessments`
now, so no breaking change is required later.

Guardrails (future): no complete-stream download by default; no continuous
recording; no automatic publication; require meaningful original reaction/
commentary; preserve attribution and provenance; use official APIs only.

---

## Tests

```sh
python3 -m unittest discover -s infra/discovery/tests -v
```

Covers: strong, weak, stale, duplicate, incomplete, and replayed candidates;
determinism; platform normalization honesty; velocity honesty; confidence
reduction on missing data; idempotent replay; production-queue exclusion;
content_format carry-through.

## Bounded real-data proof (local, $0)

```sh
python3 infra/discovery/prove_topic_intelligence.py
```

Real YouTube data (first-party RSS, no credential) → normalized → scored →
replay idempotent → duplicate rejected → manual-ingest boundary only → proof
profile excluded → no forbidden modules. Uses local SQLite (no production
Supabase write, no DDL required).
