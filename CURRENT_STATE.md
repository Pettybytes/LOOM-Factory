# LOOM — Current State (handoff)

> **This file is the execution authority.** If `HANDOFF.md` disagrees, this file wins.

---

---

## STOP STATE — 2026-09-17 ~17:35 EDT — COBALT SIDECAR PROVEN (anonymous Instagram resolution works; image finding)

**Migrations 031 + 032 APPLIED + verified (both tables HTTP 200).**

**Cobalt sidecar installed + anonymous resolution PROVEN:**
- `infra/cobalt/docker-compose.yml` — image pinned by arm64 digest (cobalt 11.7.1),
  host `127.0.0.1:9000` only, API-key auth (gitignored `keys.json`), Instagram-only
  service, `DURATION_LIMIT=300` (5 min). Fixed loopback-binding gotcha.
- POST `/` resolution test: `status:"tunnel"` → Cobalt resolves Instagram
  **anonymously (no cookies)**.
- **Finding:** the acceptance URL `…/reel/DdQE_RUS0GJ/` resolves to a **`.jpg`
  image** (all three modes) — NOT a video. **JPG is NOT accepted as video proof.**
  Instagram VIDEO acquisition stays `implemented_not_proven` (not runtime-proven).
  Pending Bryan's verification: photo/carousel → he supplies a confirmed video
  Reel; or it plays as video → record as Cobalt misclassification. Cobalt sidecar
  KEPT RUNNING. No tunnel fetch until a confirmed video Reel + explicit go-ahead.

No media downloaded. No cookies. No OpenShorts/OBS/GHL/publish.

---

## STOP STATE — 2026-09-17 ~17:25 EDT — ADAPTER EVALUATION COMPLETE (awaiting install/proof approval)

**Acquisition adapter ladder evaluated; first output produced (commit `a4884e1`).**

- Adapter ladder: yt-dlp → streamlink → self-hosted Cobalt sidecar → gallery-dl →
  Chrome extension + local helper → (external provider, last resort).
- Tool inventory: yt-dlp 2026.03.13 + ffmpeg 8.0.1 + Docker 28.4.0 + Node 22 +
  supabase CLI present; streamlink + gallery-dl NOT installed.
- Capability matrix updated: yt-dlp-extractor platforms → `implemented_not_proven`;
  Instagram stays `blocked_pending_adapter` (anonymous yt-dlp = "login required").
- Cobalt: AGPL-3.0; run as separate contained sidecar (no source copied into LOOM),
  pin version, 127.0.0.1 bind, API-key protected; needs `cookies.json` only for
  auth-required content (we will not provide cookies).
- Full assessment in `infra/acquisition/ADAPTER_EVALUATION.md` (architecture, 14-dim
  platform matrix, Cobalt deployment, Chrome-extension design, Instagram proof
  plan, $0 cost, install/security decisions).
- Tests: **218 pass** (62 discovery + 55 content + 78 discord-review + 23 acquisition).

**NOT performed (awaiting approval):** media download, service install (Cobalt/
gallery-dl/streamlink), Chrome extension build, migrations 031/032 application,
Apify, cookie extraction.

---

## STOP STATE — 2026-09-17 ~17:10 EDT — ACQUISITION BOUNDARY V1 CONTRACTS BUILT (awaiting proof authorization)

**Acquisition Boundary V1 — deterministic contracts: DONE (no download).**

Product-semantics correction applied: `Acquire for Reaction` is PRIMARY (records
authorization + creates an idempotent job immediately; allows Unassigned; routes
to Personal Reaction Library; never authorizes publishing). `Save Link Only` is
secondary. `Cancel` is third. No ambiguous `Submit Source` label.

Built (all deterministic, zero download):
- `infra/acquisition/platform_capabilities.py` — capability states + honest
  acquisition-readiness matrix (NO platform runtime-proven yet; YouTube =
  implemented_not_proven; others blocked_pending_adapter).
- `infra/acquisition/acquisition.py` — durable idempotent acquisition-job
  contract (`plan_acquisition_job`, `dedup_key`, status machine, collection
  rejection, bounded-range validation, `persist_job`).
- `infra/acquisition/reaction_library.py` — Personal Reaction Library contract
  (sha256 dedup, deterministic filename, bounded retention, immutable provenance).
- Migrations `031_loom_acquisition_jobs.sql` + `032_loom_reaction_library.sql`
  (SQL files staged; **NOT applied — Bryan applies**).
- Plan/capability matrix + storage proposal in `infra/acquisition/ACQUISITION_BOUNDARY.md`.
- 22 new deterministic tests.

Tests: **217 pass** (62 discovery + 55 content + 78 discord-review + 22 acquisition).

**STOP GATE — not crossed:** no real media download. Awaiting Bryan review of
storage destination (proposed: GHL Media Storage files + Supabase ledger) + the
adapter limits + proof target, and explicit authorization for ONE bounded,
non-production acquisition proof.

Stop conditions remain: no publishing/distribution; no Chrome extension; TikTok
held; Instagram/Twitch/Kick `blocked_pending_adapter`.

---

## STOP STATE — 2026-09-17 ~16:50 EDT — CARD-BASED INTAKE WIZARD DEPLOYED (awaiting Bryan test)

**Manual Source Intake — card-based ephemeral wizard: DONE (commit `10430c8`).**

Replaced the linear `/submit-source` slash form with a compact ephemeral card:
- `/submit-source` takes only the required `url` (Discord min-1-input).
- Validates + canonicalizes without persisting; returns an ephemeral "Manual
  Source Intake" card (platform, canonical URL, destination, format, timestamps,
  note, candidate-only notice).
- `Choose Destination` + `Choose Format` select menus (active `loom_tenants` +
  "Unassigned / General"); `Add Details` modal (start/end/note); `Submit Source`
  (green, disabled until format chosen); `Cancel` (gray).
- Persists ONLY on Submit → final ephemeral confirmation card (submission id,
  platform, destination, format, routing, platform-support, duplicate/new,
  candidate-only, "No media downloaded").
- In-memory expiring session store (15 min TTL) keyed by unguessable token;
  atomic consume guards double-submit/replay; compact deterministic custom_ids
  (`mi:<action>:<token>`); no secrets/user data in custom_ids.
- Fix: strip Instagram `stkn` tracking param in canonicalization.

Commands re-registered (url-only `submit-source`). Adapter restarted; `/health` 200.
Tests: **195 pass** (62 discovery + 55 content + 78 discord-review; +17 intake).

**NOT run: acceptance test.** Awaiting Bryan's real Discord Instagram test.

Stop conditions remain: no Chrome extension; no Acquisition Boundary;
TikTok/Instagram/Twitch/Kick remain `accepted_pending_platform_support`.

---

## STOP STATE — 2026-09-17 ~16:45 EDT — HUMAN ACCEPTANCE REPAIR COMPLETE (awaiting Bryan re-test)

**Manual Source Intake V1.1 Human Acceptance Repair: DONE (commit `149a7b4`).**

Diagnosis confirmed:
1. **Stale adapter** — the running process (PID started Sep 15 22:53) predated the
   type-2 slash handler (code modified Sep 17). `Unsupported interaction.` came
   from the stale process, NOT the committed code. Restarted via `supervise.sh`;
   `/health` 200; now running the current handler.
2. **Free-form `destination`** — `/submit-source` `destination` is now a Discord
   **autocomplete** option (sourced from active `loom_tenants` + "Unassigned /
   General"). Added `itype == 4` APPLICATION_COMMAND_AUTOCOMPLETE handler.
3. **Result message** now surfaces BOTH dimensions: `Routing: Awaiting tenant
   assignment` / `Assigned to <tenant>` AND `Platform support: Supported` /
   `Pending Instagram support` — plus canonical URL (tracking stripped),
   submission ID, and candidate-only wording (no analysis / score / acquisition /
   download).

Commands re-registered (source-review / submit-source / assign-source: 200 OK).
Tests: **178 pass** (62 discovery + 55 content + 61 discord-review; +3 new).

**NOT re-run: acceptance test.** Per stop conditions, awaiting Bryan's Discord
re-test of the Instagram Reel URL before proceeding.

Stop conditions remain: no Chrome extension; no Acquisition Boundary;
TikTok/Instagram/Twitch/Kick remain `accepted_pending_platform_support`.

---

## STOP STATE — 2026-09-17 ~16:35 EDT — SESSION HANDOFF (human acceptance FAILED)

**Manual Source Intake V1.1: deterministic + runtime-proven, but NOT human-acceptance complete.**

### Status matrix (explicit)
| Layer | Status |
|---|---|
| Code built | ✅ |
| Deterministic-tested | ✅ 175 tests (62 discovery + 55 content + 58 discord-review) |
| Automated runtime-proven | ✅ routing 15/15; manual-intake 12/12 |
| Human-acceptance | ❌ FAILED (2 defects) |
| Chrome extension | ⛔ NOT started / not authorized |
| Acquisition Boundary | ⛔ NOT started / not authorized |

### Human acceptance test — FAILED
Bryan invoked `/submit-source` in Discord with an Instagram Reel. Defects observed:
1. **`destination` is free-form** — required Bryan to type `unassigned`; no controlled options/autocomplete. Intended UX: selectable `Unassigned / General` + active production tenants (from `loom_tenants`).
2. **Bot replied ephemeral `Unsupported interaction.`** — the registered command reached the generic fallback, not the slash-command handler.

### Root-cause hypothesis (NOT yet diagnosed — diagnose before editing)
The COMMITTED `adapter.py` already has the type-2 slash handler
(`itype == 2` → `_handle_slash_command` → `_handle_submit_source_command` at
lines ~1657/1678/1726) and the `Unsupported interaction.` fallback at ~1670.
This strongly implies the RUNNING supervised adapter process is STALE (started
before the slash-command code) and must be restarted to pick up the current
handler. Command registration (Discord API) and deployed handler versions likely
differ. `destination` needs Discord choices or autocomplete sourced from active
`loom_tenants` + Unassigned.

### Instagram acceptance-test URL (recorded)
`https://www.instagram.com/reel/DdQE_RUS0GJ/?utm_source=ig_web_copy_link&stkn=MzRlODBiNWFlZA==`

Intended successful result (both must show):
- Routing: **Awaiting tenant assignment**
- Platform support: **Pending Instagram support**
- Plus: canonical URL with tracking params removed; submission ID; candidate-only
  status; NO analysis, score, acquisition or download.

### Next-session first authorized task
`Manual Source Intake V1.1 Human Acceptance Repair` — diagnose BEFORE editing:
1. Is the deployed/running adapter using the current command handler (check
   `infra/discord-review/adapter.log` / `supervisor.log`; restart via `supervise.sh`).
2. Why does the registered command hit `Unsupported interaction` (stale process).
3. Do command registration vs deployed handler versions differ.
4. Implement Discord choices/autocomplete for `destination` from active tenants + Unassigned.

### Stop conditions
- Do NOT begin the Chrome extension.
- Do NOT begin Acquisition Boundary.
- Do NOT re-run the acceptance test until the flow is fixed and Bryan confirms.
- TikTok/Instagram/Twitch/Kick remain `accepted_pending_platform_support` (no scraping/activation).

---

## STOP STATE — 2026-09-17 ~16:30 EDT

**Manual Source Intake V1.1 — Unassigned Routing + Controlled Tenant Assignment: COMPLETE (built + tested + runtime-proven).**

- Migrations **028/029/030 APPLIED**: `loom_tenants` registry (seeded `dreemzy`), `loom_routing_events` audit, per-tenant dedup (`unique(duplicate_fingerprint, tenant_id)`) + `qualification_required`.
- **tenant vs profile resolved**: `tenant_id` = owning business/account; `profile_id` = content niche (derived server-side, never asked from Bryan). `/submit-source` now takes a single `destination` (`unassigned` or active tenant).
- **`/assign-source`** command registered (id `1550241802446180463`) — assign / reassign / copy.
- `tenant_routing.py` — pure `plan_routing` (assign/reassign/copy/collision/blocked/noop) + `route_submission` (downstream lock, collision, requalification).
- **175 deterministic tests** (62 + 55 + 58).
- **Routing runtime proof 15/15** (unassigned → assign → reassign → copy → collision → downstream block → immutable audit).
- **Routing changes set `qualification_required=true`; scores are never copied or fabricated across tenants.**

Zero download. No OpenShorts/Acquisition/publishing. Proof profile auto-excluded.

---

## STOP STATE — 2026-09-17 ~16:15 EDT

**Manual Source Intake V1: COMPLETE (built + tested + runtime-proven).**

- Migration **027 `loom_manual_submissions` APPLIED** (first-wins dedup).
- **`/submit-source`** guild command registered (id `1550237873637425192`).
- `manual_intake.py` — strict platform allowlist (YouTube/Reddit/TikTok/Instagram/Twitch/Kick); SSRF/credential/private-host/port/scheme rejection; deterministic canonicalization (no fetch/redirect); dedup; status mapping.
- **160 deterministic tests** (62 discovery + 55 content-intelligence + 43 discord-review).
- **Runtime proof 12/12** (command registered; YouTube accepted + canonicalized + persisted; dedup first-wins; TikTok pending-platform; SSRF/credential/host/scheme rejections; proof profile excluded).
- **A manual submission is a CANDIDATE only — never approval for acquisition, never analyzed, never a fabricated score.**

Zero download. No OpenShorts/Acquisition/publishing. Proof profile auto-excluded.

---

## STOP STATE — 2026-09-17 ~15:40 EDT

**Source Review Inbox: COMPLETE (built + tested + runtime-proven).**

- Migration **024 `loom_source_reviews` APPLIED** (first-decision-wins per `duplicate_fingerprint`).
- **`/source-review`** guild slash command registered (Clippy Factory, id `1550229456462028881`).
- `source_review.py` — combined candidate cards (market + content + selection, honest content-confidence), Approve-for-Acquisition / Hold / Reject.
- `adapter.py` — type-2 slash command + source-button routing.
- **128 deterministic tests** (62 discovery + 55 content-intelligence + 11 source-review).
- **Safe runtime proof 8/8** (Discord card post+delete; decision persistence idempotent; production-queue exclusion).
- **APPROVE FOR ACQUISITION records authorization only — triggers nothing** (no download/OpenShorts/publish).

Zero download. No OpenShorts/Acquisition/publishing. Proof profile auto-excluded.

> **Fixture distinction (recorded):** the Linus Tech Tips video used in the demo card is a UI/test fixture ONLY. Its appearance does NOT constitute tenant relevance or source-selection acceptance. Before production, candidate qualification must enforce the selected tenant's niche, audience, content strategy, exclusions and desired reaction formats — a generally-popular video must not enter a tenant's queue merely because it performs well globally.

---

## STOP STATE — 2026-09-17 ~15:30 EDT

**Content Intelligence Operationalization V1.1: COMPLETE (recurring pipeline runtime-proven).**

- A/B (same transcript): **Gemini Flash-Lite vs Qwen** — Gemini is the default transcript-triage provider (2.6s vs 146s; 560 vs 5798 output tokens; both schema-valid + evidence-grounded).
- **Pinned model:** `gemini-3.5-flash-lite` (resolved from `gemini-flash-lite-latest`); Qwen `qwen/qwen3.5-9b` (OpenRouter/SiliconFlow) as explicit secondary (no silent fallback).
- **Real provider layer** promoted: `providers.py` (GeminiProvider + QwenProvider — classified failures, cost/token accounting, model identity persistence, no credential logging).
- **Recurring `pipeline.py` (Pass1Pipeline):** discovery → Topic Intelligence → market shortlist → transcript-only (yt-dlp skip-download, 0 media bytes) → Content Intelligence → Combined Selection → idempotent Supabase. Hard limits + $0.05 run cost ceiling.
- **Selection queue contract:** `selection_queue()` — completed selections only (7 outcomes).
- **117 deterministic tests** (62 discovery + 55 content-intelligence).
- **Recurring-pipeline runtime proof 9/9** ($0.00071, non-production, idempotent, excluded).

Zero full-video download. No Acquisition/OpenShorts/Discord/publish. Proof profile auto-excluded.

---

## STOP STATE — 2026-09-17 ~15:05 EDT

**Content Intelligence Preflight V1 + Combined Source Selection V1: BUILT + TESTED + Pass-1 RUNTIME-PROVEN.**

- Evidence-level contract (metadata/text/static-visual/bounded-media) + per-dimension gating + confidence ceilings.
- Strict Content Assessment schema + validation (rejects malformed / out-of-bounds / evidence-exceeding claims).
- Combined Source Selection (35% market + 45% content + 15% fit + 5% provenance) + separate confidence + decision rules.
- Bounded-media probe contract (temp lifecycle, hard limits, no full download).
- Migrations 025/026 **APPLIED** (Bryan, 2026-09-17).
- **104 deterministic tests** (62 discovery + 42 content-intelligence).
- **Pass-1 real proof 13/13 PASS** (Gemini Flash-Lite, transcript-only, cost ≈ $0.002 < $0.05 ceiling):
  - real YouTube candidate (Linus Tech Tips `qgLaCZyKv_8`), English auto-captions via yt-dlp (skip-download, 0 video/audio bytes);
  - market=59 (Topic Intelligence) + content_worthiness=68 (Gemini) → selection=60, outcome=standard_review;
  - evidence-gated (level 1: no visual/audio claims; confidence capped 0.33);
  - real transcript-derived timestamps + reasons; idempotent Supabase rows.

Zero full-video download. No Acquisition asset, no OpenShorts job, no Discord
card, no publishing/distribution. Proof profile auto-excluded from production.

---

## STOP STATE — 2026-09-17 ~13:50 EDT

**Topic Intelligence V1: SEGMENT COMPLETE (built + tested + runtime-proven — local AND Supabase).**

- Deterministic opportunity scoring layer built on top of Source Discovery V1.
- Platform-aware normalization (YouTube vs Reddit) without pretending metrics are identical.
- Velocity computed ONLY from valid time-separated observations (honest, no fabrication).
- 7-signal weighted score → opportunity class (strong/moderate/weak/stale/duplicate/incomplete) + confidence + reason codes + missing-data flags.
- `content_format` routing field added (default `hold_unassigned`).
- Assessment store: SQLite (built+tested) + Supabase (built + runtime-proven).
- Migrations 022/023 **APPLIED** (Bryan, 2026-09-17).
- Real-data proofs: local SQLite **28/28 PASS**; Supabase **13/13 PASS**.

Zero cost. Zero downstream side effects. No media download, no OpenShorts job,
no publication, no distribution. No candidate advanced beyond `manual_ingest_only`.

**No source was downloaded, processed, clipped, published, or distributed.**

---

## STOP STATE — 2026-09-16 ~02:15 EDT

**Automated Source Discovery V1: SEGMENT CLOSED.**

- **Segment 2 — Foundation: COMPLETE.**
- **Segment 3 — YouTube connector milestone: RUNTIME-PROVEN (Supabase-backed).**
- **Segment 3 — Reddit connector milestone: RUNTIME-PROVEN (Supabase-backed, 12/12).**

Zero cost. Zero downstream side effects (no download, no OpenShorts job, no
publication, no distribution).

**No source was downloaded, processed, clipped, published, or distributed.**

---

## Segment boundary (roadmap overlap resolved — SEGMENT CLOSED)

- **Segment 2 — Automated Source Discovery V1 Foundation (COMPLETE).**
  Exit criteria met: Source Brief contract; normalized discovery schema;
  identity + duplicate handling (deterministic tests); shared connector
  interface; qualification-before-acquisition gate; three profile fixtures
  validate.
- **Segment 3 — Initial Connector Proofs (FIRST MILESTONE RUNTIME-PROVEN).**
  Real YouTube source discovered → normalized → deduplicated → evidence stored
  → qualification applied → one invalid/duplicate rejected → one eligible source
  passed to the manual-ingest boundary → no automatic publication.
- **Segment 3 — Reddit connector milestone (RUNTIME-PROVEN):** connector built
  via first-party RSS (no credential, no scraping); 38 deterministic tests pass;
  25 real r/technology posts discovered, stored, deduped, qualified; explicit
  duplicate/cross-post + policy rejections verified; one eligible source reached
  manual-ingest boundary. Supabase proof 12/12.

TikTok remains **held**. X deferred. Instagram/Facebook planned. Do not start
TikTok until explicitly authorized.

---

## What was built (`infra/discovery/` — Topic Intelligence V1)

| Module | Responsibility |
|---|---|
| `normalization.py` | Platform-aware signal normalization → `NormalizedSignals` |
| `velocity.py` | Velocity from valid time-separated observations (honest) |
| `topic_intelligence.py` | Deterministic scoring + reason codes + opportunity class |
| `prove_topic_intelligence.py` | Bounded $0 real-data proof (local SQLite) |
| `tests/test_topic_intelligence.py` | 24 deterministic tests |

Plus `content_format` on `discovery_record.py` + both stores; migrations
`022_loom_topic_assessments.sql` and `023_loom_discovered_sources_content_format.sql`.

See `infra/discovery/TOPIC_INTELLIGENCE.md` for the full model.

## What was built (`infra/discovery/`)

| Module | Responsibility |
|---|---|
| `source_brief.py` | Versioned, tenant-scoped Source Brief contract + validation |
| `discovery_record.py` | Normalized discovery record schema + validation (reserves Topic Intelligence fields; computes none) |
| `identity.py` | Canonical URL normalization, content-ID extraction, tenant/global fingerprints, duplicate classification (no paid model) |
| `connector.py` | Shared connector interface (pagination, rate limits, auth-by-name, provenance, retry, health, cost, versioning) |
| `qualification.py` | Qualification-before-acquisition gate (deterministic, explicit rejection reasons) |
| `store.py` | Durable store (SQLite, dependency-free) — idempotent upsert + append-only metric history |
| `store_supabase.py` | Production Supabase store (service-role key by NAME only, no DDL, no download) |
| `connectors/youtube.py` | YouTube/search + competitor-channel discovery (first-party RSS, no credential) |
| `connectors/__init__.py` | Connector registry (youtube only; reddit/tiktok planned) |
| `prove_youtube_connector.py` | Bounded live connector proof |
| `prove_supabase_acceptance.py` | 12-criteria Supabase-backed acceptance proof |
| `fixtures/` | 3 non-provisioned profile Source Briefs (design/test examples only) |
| `tests/test_discovery.py` | 34 deterministic unit tests |

Plus `n8n-workflows/LOOM-06-Source-Discovery.json` (shared, tenant-driven,
**inactive** — never deployed/activated).

## Live YouTube proof (real, non-mocked — 2026-09-16)

- **Method:** first-party RSS feed (`/feeds/videos.xml?channel_id=`), no
  credential, no scraping. Keyword *search* (Data API v3) correctly raises
  `AuthRequiredError` for `YOUTUBE_DATA_API_V3_KEY` (name only, absent).
- **Channel:** Linus Tech Tips `UCXuqSBlHAE6Xw-yeJA0Tunw` (public, real).
- **Discovered:** 15 real candidates (e.g. `3xngArcFpek` "I Promise you this is
  NOT Stupid", `3PGKMwgjla0` "Steam Frame Review - This Changes Everything").
- **Normalized + stored:** 15/15, idempotent (fingerprint-keyed).
- **Duplicate rejected:** re-observing `3xngArcFpek` → upsert `updated`, row
  count unchanged; gate returned `rejected` with reason `duplicate (repost):
  global content id already observed`.
- **Qualified:** 15/15 `manual_review` (brief requires manual review).
- **Manual-ingest boundary:** `3xngArcFpek` passed → STOP (no download, no
  OpenShorts, no clip, no publish, no GHL post).
- Reproducible: `python3 infra/discovery/prove_youtube_connector.py`.

## Tests

`python3 -m unittest discover -s infra/discovery/tests` → **38 passed**.

## Reddit connector (RUNTIME-PROVEN, Supabase-backed 12/12)

`infra/discovery/connectors/reddit.py` — subreddit + bounded keyword discovery
via first-party Reddit RSS (no credential, no scraping, no auth evasion).
Captures post id, canonical URL, subreddit, author, title, body excerpt,
publication time, observed metrics (content_type + source URL refs), provenance
(honest note: RSS omits score/ups/comments). Cross-post dedup via author-agnostic
`crosspost_key`. 25 real r/technology posts discovered.

`prove_supabase_reddit.py` → 12/12 PASS against production Supabase: stored
(25/25) → replay idempotent → explicit exact-duplicate + cross-post rejection
→ policy rejection (missing provenance) → manual-ingest boundary only → no
forbidden modules / RSS-GET-only → proof profile excluded from production queue.

## Supabase acceptance proof (12/12 PASS, production Supabase)

`python3 infra/discovery/prove_supabase_acceptance.py` → 12/12 criteria pass:
real candidates discovered (n=15) → normalized + stored (15/15) → replay
idempotent (15, not 30) → metric-history append (1→2, prior preserved) →
explicit repost rejection → manual-ingest boundary only → no forbidden modules
imported / RSS-GET-only. See "Persisted state".

## Production-queue exclusion of acceptance/test profiles

`profile_id` now travels on every discovered record (from its Source Brief) and
is persisted. `is_non_production(profile_id, tenant_id)` marks acceptance/test/
example profiles. Normal production queue queries use
`production_sources()` / `production_fingerprints()` /
`production_global_fingerprints()`, which **exclude non-production profiles by
default**. The acceptance profile `supabase-acceptance-proof` is therefore
excluded from production queries automatically.

## Persisted state (production Supabase, kept intentionally)

- `loom_source_briefs`: 2 rows — profiles `supabase-acceptance-proof` (YouTube)
  and `reddit-acceptance-proof` (Reddit). Both non-production acceptance
  evidence, KEPT.
- `loom_discovered_sources`: **40 retained proof records — 15 YouTube +
  25 Reddit**, 40 distinct fingerprints (dedup holds). All non-production and
  auto-excluded from production queues.
  - YouTube: `qualification_status` 1 `manual_review` / 14 `pending`;
    `metric_history` on sampled candidate = 2 observations.
  - Reddit: 25 rows, all with `body_excerpt`; 1 `manual_review` / 24 `pending`.
  - `processing_eligibility`: 40/40 `manual_ingest_only`.

## Migrations

| # | Change | Status |
|---|--------|--------|
| 018 | `loom_source_briefs` (versioned tenant-scoped briefs) | **Applied** (Bryan, 2026-09-16) |
| 019 | `loom_discovered_sources` (normalized records, fingerprint-unique, append-only metric history) | **Applied** (Bryan, 2026-09-16) |
| 020 | `loom_discovered_sources.profile_id` (+ index) | **Applied** (Bryan, 2026-09-16) |
| 021 | `loom_discovered_sources.body_excerpt` | **Applied** (Bryan, 2026-09-16) |
| 022 | `loom_topic_assessments` (Topic Intelligence V1) | **Applied** (Bryan, 2026-09-17) |
| 023 | `loom_discovered_sources.content_format` | **Applied** (Bryan, 2026-09-17) |
| 024 | `loom_source_reviews` (Source Review Inbox) | **Applied** (Bryan, 2026-09-17) |
| 025 | `loom_content_assessments` (Content Intelligence V1) | **Applied** (Bryan, 2026-09-17) |
| 026 | `loom_source_selections` (Combined Selection V1) | **Applied** (Bryan, 2026-09-17) |
| 027 | `loom_manual_submissions` (Manual Source Intake V1) | **Applied** (Bryan, 2026-09-17) |
| 028 | `loom_tenants` (controlled destination registry) | **Applied** (Bryan, 2026-09-17) |
| 029 | `loom_routing_events` (assignment/reassignment audit) | **Applied** (Bryan, 2026-09-17) |
| 030 | `loom_manual_submissions` per-tenant dedup + `qualification_required` | **Applied** (Bryan, 2026-09-17) |

> Migrations 010–017 remain applied + live-verified (unchanged). 020 added
> `profile_id`; the 15 YouTube acceptance rows were backfilled to
> `supabase-acceptance-proof` and are excluded from production queries.
> 021 added `body_excerpt` (Reddit body capture).

## Git state

- **LOOM-Factory `master`** · HEAD `4cedf07` · clean · **pushed** to
  `origin/master` (https://github.com/Pettybytes/LOOM-Factory.git).
- Commit chain (this discovery build):
  1. `13a8dc6` — Source Brief contract + normalized schema + fixtures + migrations 018/019
  2. `0e23c54` — deterministic identity + duplicate handling
  3. `4b1da3e` — shared connector interface + qualification gate
  4. `8f03e34` — YouTube connector + durable store + live proof + tests
  5. `5ab5283` — README + shared n8n discovery workflow
  6. `d10f002` — explicit exact-duplicate classification
  7. `205f1bf` — docs: discovery V1 handoff
  8. `708d472` — production Supabase store + full acceptance proof (12/12)
  9. `4cedf07` — profile_id on records + production-queue exclusion
- No secret values in any commit. No destructive git/db operations. Unrelated
  dirty files (nested OpenShorts repo) left untouched.

## Costs incurred

**$0.00.** No paid model calls. No new paid service. RSS is free. Docker
Desktop left running (existing containers up: `loom-tunnel-proxy`,
`openshorts-backend`, `openshorts-renderer`, `openshorts-frontend`).
`caffeinate` already active (PID 55082).

## Credentials required (names only — never values)

- `YOUTUBE_DATA_API_V3_KEY` — needed to enable YouTube keyword *search*
  (Data API v3). Channel discovery already works via free RSS without it.
- Reddit: **none** — first-party RSS works with no credential. The public JSON
  API is 403 unauthenticated and the OAuth API requires app registration
  (neither used).
- Supabase DDL path — either the Supabase **SQL editor** or the **database
  password** for `supabase link` + `supabase db push`.

## Manual actions required (Bryan)

1. (Optional) Provide `YOUTUBE_DATA_API_V3_KEY` for YouTube keyword search.
2. (Nothing blocking — migrations 018–021 all applied.)

## Next action

- Migrations 018–030 all **APPLIED**. Manual Intake V1.1 routing **RUNTIME-PROVEN (15/15)**.
- **Next segment: Acquisition Boundary** — ingest candidates Bryan approved-for-acquisition (still no download/publish without further authorization); optionally wire accepted YouTube/Reddit manual submissions into the existing qualification pipeline.
- **Pass 2 (bounded media)** — deferred; needs separate approval.
- **TikTok/Instagram/Twitch/Kick** — submissions accepted as `accepted_pending_platform_support` (no scraping/activation).

---

## 🔒 Locked architecture (editor path — unchanged, do not modify)

```text
durable clean source (loom_media.clean_source_url, filesafe CDN)
  → session-bound temporary OpenShorts job (output/loom-<session>/, reconstructed)
  → native OpenShorts endpoints (/api/clip/rerender, /api/hook, /api/subtitle, …)
  → immutable LOOM version (register_edited_asset RPC, render-<major>.<minor+1>)
  → exactly one Discord final_publication review card
```

- The OpenShorts editor is **NOT stateless**: it needs a live `output/<job_id>/`
  workspace (metadata JSON + clip files) and an in-memory `jobs[id]` entry.
- LOOM's adapter (`infra/discord-review/adapter.py`) rehydrates that workspace on
  demand from the durable clean source, so editing survives OpenShorts job eviction.
- The client (Discord Activity / editor) never chooses the temp job id; it is
  derived server-side from the edit session and translated transparently in
  `_handle_editor_api`.

## ✅ Real E2E proof (editor, unchanged)

Fresh non-production clip `loom-e2e-proof-job:0` with its original OpenShorts job
**deleted**, run end-to-end: launch-token exchange → `/discord/editor/bootstrap`
(durable) → `/api/status` rehydration ("Job recovered from disk") →
`/api/clip/rerender` (trim 1.0→5.0) → `/api/hook` → `/api/subtitle` (karaoke) →
`/discord/editor/complete` (one `render-1.1` + one card) → replay idempotent.
Card message id `1549614172055281724`. Script
`infra/supabase/prove_e2e_rehydrate.py`.

## ♻️ Temporary workspace lifecycle (unchanged)

`rehydrate_edit_job()` creates `output/loom-<edit_session_id[:12]>/` per session.
Retain while active/retryable; delete after completion + grace; clean abandoned
workspaces with bounded retention; never delete another tenant/session workspace.
Re-hydration is idempotent.

## 1. Repository state

- **LOOM-Factory** `master` · HEAD `d10f002` · clean · pushed to origin.
- **OpenShorts (nested)** `loom/factory-mvp-baseline-fixes` · HEAD `7f47de5` ·
  pre-existing dirty/untracked (do NOT touch): `dashboard/index.html`,
  `docker-compose.yml`, `security_utils.py` (modified); `aborted-runs/`,
  `security_utils.py.bak-20260908212436` (untracked).
- **Recovery artifacts** (`openshorts-recovery/`) preserved:
  `0002-discord-editor.patch` md5 `b0867c9ec0e20d6550929ee8bce7a4b7`;
  `0001-fix-caption-contract.patch` md5 `c631f000b6b5e904dbffab942d9fba9e`.

## 2. Runtime + infrastructure (unchanged)

**Funnel hostname:** `https://users-macbook-pro-1.taild000b2.ts.net` → Caddy `:8081`

| Component | Process/container | Bind | Health |
|-----------|-------------------|------|--------|
| LOOM adapter | `adapter.py` (host, supervised) | `127.0.0.1:8090` | `/health` 200 |
| Caddy funnel | `loom-tunnel-proxy` | `127.0.0.1:8081–8082` | Up |
| OpenShorts backend | `openshorts-backend` | `127.0.0.1:8000` (loopback) | Up (root 404 normal) |
| OpenShorts renderer | `openshorts-renderer` | `0.0.0.0:3100` | Up |
| OpenShorts frontend (Vite) | `openshorts-frontend` | `0.0.0.0:5175→5173` | Up |

Adapter supervision: `infra/discord-review/supervise.sh` → `supervisor.log`;
launcher `run.sh` (sources `~/.openclaw/.env`); stdout/stderr → `adapter.log`.

Caddy `:8081` routes (unchanged): `/edit /edit/*` (CSP frame-ancestors Discord) ·
`/discord/interactions*` → 8090 (Ed25519) · `/discord/editor/*` → 8090 ·
`/discord/card*` → 8090 (Bearer tunnel token) · `/api/*` → 8090 · default → Vite 5175.
`:8082` → `/relay*` n8n callback relay.

## 3. Discord IDs (unchanged)

- **App:** "LOOM Clip Review" · App ID `1548188833386995742`
- **Guild:** "Clippy Factory" · Guild ID `1546266463663423518`
- **Review channel:** resolved at runtime by name `clip-review`.
- **Reviewer (Bryan):** `688976899094872148`

## 4. Migrations — 010–021 (all applied + live-verified)

| # | Change | Status |
|---|--------|--------|
| 010 | `tenant_distribution_profiles.publisher_user_id` | Applied |
| 011 | `loom_media.content_type` | Applied |
| 012 | `loom_edit_sessions` + `loom_edit_corrections` + lineage | Applied |
| 013 | `register_edited_asset` RPC (service_role only) | Applied |
| 014 | `loom_edit_sessions.session_token_hash` | Applied |
| 015 | editor launch context | Applied |
| 016 | `loom_edit_sessions.draft` (jsonb) | Applied |
| 017 | `loom_media.clean_source_url/hash/size/filename` | Applied |
| 018 | `loom_source_briefs` | Applied (Bryan, 2026-09-16) |
| 019 | `loom_discovered_sources` | Applied (Bryan, 2026-09-16) |
| 020 | `loom_discovered_sources.profile_id` (+ index) | Applied (Bryan, 2026-09-16) |
| 021 | `loom_discovered_sources.body_excerpt` | Applied (Bryan, 2026-09-16) |

## 5. Credential inventory (names + locations only — never values)

**`~/.openclaw/.env`:** `LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`,
`LOOM_DISCORD_BOT_TOKEN`, `LOOM_DISCORD_CLIENT_SECRET`,
`LOOM_DISCORD_OAUTH_REDIRECT_URI`, `LOOM_EDITOR_PUBLIC_URL`, `LOOM_N8N_API_KEY`,
`LOOM_SUPABASE_URL`, `LOOM_SUPABASE_SERVICE_ROLE_KEY`, `LOOM_GHL_TOKEN`,
`LOOM_GHL_LOCATION_ID`.

**`infra/tunnel-proxy/.env`:** `LOOM_TUNNEL_TOKEN`, `LOOM_RELAY_TOKEN`,
`LOOM_GEMINI_KEY`.

> ⚠️ Never record or print secret values. Reference the files only.
> `YOUTUBE_DATA_API_V3_KEY` is **absent** (needed only for keyword search).

## 6. Deferred / NOT in scope

- **Discord Activity (iframe) embedding** — deferred.
- **Temporary workspace sweep** — policy documented; enforcement deferred.
- **Missing preview font** `fonts/NotoSerif-Bold.ttf` — cosmetic, deferred.
- **Topic Intelligence scoring** — reserved fields only in discovery; no scoring
  implemented (correctly out of scope for this segment).
- **Reddit connector** — RUNTIME-PROVEN (Supabase 12/12).
- **TikTok connector** — HELD until explicitly authorized.
- **GHL subaccounts / social accounts / Discord tenant spaces for the three
  profiles** — deliberately NOT created (profiles are fixtures only).

## 7. Next segment

**Acquisition Boundary** — the next segment. Source Review Inbox + Manual Source
Intake are complete: `/source-review` surfaces BOTH halves (market + content →
Combined Selection) and records approve-for-acquisition / hold / reject;
`/submit-source` accepts manual URLs (YouTube/Reddit → `accepted`; TikTok/
Instagram/Twitch/Kick → `accepted_pending_platform_support`). Acquisition would
ingest only approved-for-acquisition candidates (and optionally wire accepted
YouTube/Reddit submissions into qualification) — still requires explicit
authorization before any download/publish. **Pass 2 (bounded media)** deferred
(separate approval). No automatic download/processing/publication at any point.
