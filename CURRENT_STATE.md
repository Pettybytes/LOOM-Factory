# LOOM — Current State (handoff)

> **This file is the execution authority.** If `HANDOFF.md` disagrees, this file wins.

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

- `loom_source_briefs`: 1 row — profile `supabase-acceptance-proof`
  (non-production acceptance evidence, KEPT).
- `loom_discovered_sources`: 15 rows, 15 distinct fingerprints (dedup holds).
  `metric_history` on the sampled candidate: 2 observations.
  `qualification_status`: 1 `manual_review`, 14 `pending`.
  `processing_eligibility`: 15/15 `manual_ingest_only`.

## Migrations

| # | Change | Status |
|---|--------|--------|
| 018 | `loom_source_briefs` (versioned tenant-scoped briefs) | **Applied** (Bryan, 2026-09-16) |
| 019 | `loom_discovered_sources` (normalized records, fingerprint-unique, append-only metric history) | **Applied** (Bryan, 2026-09-16) |
| 020 | `loom_discovered_sources.profile_id` (+ index) | **Applied** (Bryan, 2026-09-16) |
| 021 | `loom_discovered_sources.body_excerpt` | **Applied** (Bryan, 2026-09-16) |

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

- **TikTok** — HELD until explicitly authorized. Instagram/Facebook planned; X deferred.

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

**TikTok** remains HELD until explicitly authorized. No automatic
download/processing/publication at any point.
