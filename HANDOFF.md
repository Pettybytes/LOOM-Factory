# LOOM — HANDOFF

> **Primary execution authority:** `CURRENT_STATE.md`. If this file and
> `CURRENT_STATE.md` ever disagree, `CURRENT_STATE.md` wins.
>
> Never put API keys, tokens, passwords, cookies, secrets, complete auth
> headers, or private customer data in this file.

---

## Last Updated
2026-09-17 15:40 EDT (America/Detroit)

## Segment — Source Review Inbox (Discord)

- **Migration 024 `loom_source_reviews` APPLIED** (first-decision-wins).
- **`/source-review`** guild command registered (id `1550229456462028881`).
- `source_review.py` (combined cards: market + content + selection; Approve-for-Acquisition / Hold / Reject) + `register_source_review_command.py` + adapter type-2 slash command.
- **128 deterministic tests** (62 + 55 + 11). **Safe runtime proof 8/8** (card post+delete, idempotent decision, production exclusion).
- **APPROVE FOR ACQUISITION records authorization only — triggers nothing.**

## Segment — Content Intelligence Operationalization V1.1 (recurring pipeline)

- **A/B:** Gemini Flash-Lite vs Qwen — Gemini is default transcript-triage provider (2.6s vs 146s; 560 vs 5798 output tokens; both schema-valid + evidence-grounded).
- **Pinned model:** `gemini-3.5-flash-lite`; Qwen `qwen/qwen3.5-9b` (secondary, no silent fallback).
- **`providers.py`** — real Gemini + Qwen providers (classified failures, cost/token accounting, model identity, no credential logging).
- **`pipeline.py`** — Pass1Pipeline (discovery → topic → shortlist → transcript → content → selection → idempotent Supabase; hard limits + cost ceiling) + `selection_queue()`.
- **`store_supabase.py`** — Supabase persistence for assessments + selections.
- **117 deterministic tests** (62 + 55). **Recurring-pipeline proof 9/9** ($0.00071).

## Segment — Content Intelligence Preflight V1 + Combined Source Selection V1

- **`evidence.py`** — 5 evidence levels + per-dimension gating + confidence ceilings.
- **`contract.py`** — strict Content Assessment schema + validation (rejects malformed / out-of-bounds / evidence-exceeding claims).
- **`selection.py`** — combined 35/45/15/5 score + separate confidence + decision rules.
- **`provider.py`** — evidence bundling, model-provider interface, deterministic MockProvider, orchestration.
- **`probe.py`** — bounded media probe contract (temp lifecycle, hard limits, no full download).
- **`store.py`** — SQLite persistence (idempotent; new evidence version → new row).
- **Migrations 025/026 APPLIED** (Bryan, 2026-09-17).
- **104 deterministic tests** (62 discovery + 42 content-intelligence).
- **Pass-1 real proof 13/13** (Gemini Flash-Lite `gemini-flash-lite-latest`, transcript-only, ≈ $0.002 < $0.05): real YouTube candidate `qgLaCZyKv_8`, English auto-captions via yt-dlp (0 video/audio bytes), market=59 + content=68 → selection=60 / standard_review, evidence-gated, idempotent Supabase rows.

## Segment — Topic Intelligence V1: RUNTIME-PROVEN (local + Supabase)

- **`normalization.py`** — platform-aware signal normalization (YouTube `views`; Reddit engagement honestly absent over RSS). Missing metrics recorded, never fabricated.
- **`velocity.py`** — velocity ONLY from ≥2 valid time-separated observations; unavailable otherwise (honest reason).
- **`topic_intelligence.py`** — 7-signal weighted score → opportunity class (strong/moderate/weak/stale/duplicate/incomplete) + confidence + data-completeness + reason codes + missing-data/low-confidence flags. No LLM, no paid service, deterministic.
- **`content_format`** routing field added (default `hold_unassigned`).
- **Assessment store:** SQLite built+tested; Supabase built + runtime-proven.
- **Migrations 022/023 APPLIED** (Bryan, 2026-09-17).
- **62 deterministic tests pass** (was 38).
- **Real-data proofs: local SQLite 28/28 PASS; Supabase 13/13 PASS** (15 real YouTube candidates, $0).
- Fixed Supabase `upsert_source` UPDATE-path parity (profile_id/body_excerpt/content_format refreshed on re-observation).
- Proof profiles auto-excluded from `production_assessments()`. No candidate advanced beyond `manual_ingest_only`.

Zero cost. Zero downstream side effects. No media download, no OpenShorts job,
no publication, no distribution.

## Segment 3 — Reddit connector milestone: RUNTIME-PROVEN (Supabase 12/12)

- **RedditConnector** (`connectors/reddit.py`) — subreddit + bounded keyword
  discovery via first-party Reddit RSS (no credential, no scraping, no auth
  evasion). Public JSON API is 403 unauthenticated; OAuth API requires app
  registration (neither used).
- Captures: post id (`t3_` stripped), canonical URL, subreddit, author, title,
  body excerpt, publication time, observed metrics (content_type + source URL
  refs), provenance (honest note: RSS omits score/ups/comments).
- Cross-post dedup: author-agnostic `crosspost_key` added to identity + gate.
- **38 deterministic tests pass** (was 34).
- **Supabase proof 12/12 PASS**: 25 real r/technology posts discovered → stored
  (25/25) → replay idempotent → explicit exact-duplicate + cross-post rejection
  → policy rejection (missing provenance) → manual-ingest boundary only → no
  forbidden modules / RSS-GET-only → proof profile excluded from production queue.

## Segment 2 — Automated Source Discovery V1: CLOSED

- **Segment 2 — Foundation: COMPLETE.**
- **Segment 3 — first YouTube connector milestone: RUNTIME-PROVEN
  (Supabase-backed).**

Zero cost. Zero downstream side effects. No source was downloaded, processed,
clipped, published, or distributed.

## Segment boundary (resolved)

- **Segment 2 — Foundation (COMPLETE):** Source Brief contract; normalized
  discovery schema; identity + duplicate handling (deterministic tests);
  shared connector interface; qualification-before-acquisition gate; three
  profile fixtures validate.
- **Segment 3 — Initial Connector Proofs (first milestone RUNTIME-PROVEN):**
  real YouTube source discovered → normalized → deduplicated → evidence stored
  → qualification applied → one duplicate rejected → one eligible source passed
  to manual-ingest boundary → no automatic publication.

Reddit is runtime-proven. TikTok is HELD. X deferred.

## What was built

```
infra/discovery/
  source_brief.py           # Source Brief contract + is_non_production() marker convention
  discovery_record.py       # normalized discovery record + validation (carries profile_id + body_excerpt)
  identity.py               # canonical URL norm + fingerprints + dedup (no paid model) + crosspost_key
  connector.py              # shared connector interface
  qualification.py          # qualification-before-acquisition gate
  store.py                  # SQLite durable store (idempotent upsert, append-only metric history)
  store_supabase.py         # production Supabase store (service-role key by NAME only)
  connectors/youtube.py     # YouTube/search + channel discovery (first-party RSS)
  connectors/reddit.py      # Reddit subreddit + keyword discovery (first-party RSS)
  prove_youtube_connector.py
  prove_supabase_acceptance.py
  prove_supabase_reddit.py
  fixtures/                 # 3 non-provisioned profile briefs
  tests/test_discovery.py   # 38 deterministic tests
n8n-workflows/LOOM-06-Source-Discovery.json   # shared, inactive
infra/supabase/018_loom_source_briefs.sql      # APPLIED
infra/supabase/019_loom_discovered_sources.sql # APPLIED
infra/supabase/020_loom_discovered_sources_profile.sql # APPLIED (15 rows backfilled)
infra/supabase/021_loom_discovered_sources_body_excerpt.sql # APPLIED
infra/supabase/022_loom_topic_assessments.sql            # APPLIED
infra/supabase/023_loom_discovered_sources_content_format.sql # APPLIED
infra/supabase/024_loom_source_reviews.sql                  # APPLIED
infra/supabase/025_loom_content_assessments.sql            # APPLIED
infra/supabase/026_loom_source_selections.sql              # APPLIED
```

## Supabase acceptance proof (12/12 PASS, production Supabase)

Real YouTube data (Linus Tech Tips): 15 candidates discovered via first-party
RSS → normalized + stored (15/15) → replay idempotent (15, not 30) →
metric-history append (1→2, prior preserved) → explicit repost rejection →
manual-ingest boundary only → no forbidden modules imported / RSS-GET-only.
Script: `infra/discovery/prove_supabase_acceptance.py`.

## Production-queue exclusion (acceptance/test profiles)

`profile_id` travels on every discovered record (from its Source Brief) and is
persisted. `is_non_production()` marks acceptance/test/example profiles. Normal
production queue queries use `production_sources()` / `production_fingerprints()`
/ `production_global_fingerprints()`, which exclude non-production profiles by
default. Profile `supabase-acceptance-proof` is non-production acceptance
evidence and is KEPT (not deleted).

## Tests

`python3 -m unittest discover -s infra/discovery/tests` → **62 passed**.

## Bounded real-data proof (Topic Intelligence)

`python3 infra/discovery/prove_topic_intelligence.py` → **28/28 PASS** (local
SQLite, 15 real YouTube candidates via first-party RSS, $0).

`python3 infra/discovery/prove_topic_intelligence_supabase.py` → **13/13 PASS**
(production Supabase, migrations 022/023 applied, $0).

## Git

- LOOM-Factory `master` HEAD `94607d7`, clean, **pushed** to origin.
- Chain: `13a8dc6` → `0e23c54` → `4b1da3e` → `8f03e34` → `5ab5283` →
  `d10f002` → `205f1bf` → `708d472` → `4cedf07` → `317cf96` → `defd4ec` →
  `9d73f12` → `d97fcf5` → `94607d7`.
- No secrets committed; no destructive ops; nested OpenShorts dirty files untouched.

## Costs

$0.00. No paid model calls, no new paid service. Reddit RSS is free.
`caffeinate` active; Docker left running.

## Credentials required (names only)

- `YOUTUBE_DATA_API_V3_KEY` — for YouTube keyword search (absent; channel RSS works without it).
- Reddit — none (first-party RSS; JSON/OAuth API not used).

## Manual actions required (Bryan)

- (Optional) Provide `YOUTUBE_DATA_API_V3_KEY` for YouTube keyword search.
- Migrations 018–026 all applied; nothing blocking.

## Next

Migrations 018–026 applied; Source Review Inbox runtime-proven (8/8). Next
segment: **Acquisition Boundary** — ingest only approve-for-acquisition
candidates (still requires explicit authorization before download/publish).
**Pass 2 (bounded media)** deferred. TikTok HELD until explicitly authorized.
No automatic download/processing/publication at any point.
