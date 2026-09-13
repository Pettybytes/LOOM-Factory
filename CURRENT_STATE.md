# LOOM — Current State (handoff)

> ## 🚀 START HERE NEXT SESSION
> **Repository:** `/Users/user/Desktop/LOOM-Factory` · **Remote:** `Pettybytes/LOOM-Factory`
> **Completed segments:** 3 (ingest) · 4 (Discord QC) · 5 (GHL media storage) · 6A (tenant mapping)
> **6B status:** API contract **runtime-verified via a manual curl harness**; `Workflow 05` **corrected + fully implemented (no-duplicate receipt short-circuit + POST /posts/list reconciliation + canonical MIME gate) but INACTIVE / not yet runtime-verified**
> **Workflow 05 n8n ID:** `9eYbHX8ryps46lH0` (20 nodes; inactive; 12-point guard + receipt router → `already_distributed`/`failed` short-circuit + reconciliation paths + MIME gate)
> **Unlisted YouTube proof:** https://www.youtube.com/watch?v=ua50Kuxwi-g
> **GHL post ID:** `6aa63853d1928cc532472362` · **Receipt ID:** `76abb31d-effd-492b-b03c-3fdd9524c0d0`
> **Safety:** `entitled=false`, `enabled_for_distribution=false` (fail-closed — both gates remain false)
> **Migration 010:** applied — **runtime verified** (`publisher_user_id` = `uT8mleycXWhVCWcGw3f6` present on the single Dreemzy/YouTube row)
> **Migration 011 (`011_loom_media_content_type.sql`):** written + committed — **PENDING manual application in SQL Editor** (DDL not reachable via REST). Fail-closed: `loom_media.content_type` does not exist yet, so the MIME gate rejects until applied + a real asset's `content_type` is populated.
> **Credentials wired (IDs only, no secret values in JSON):** GHL Social Planner PIT (`LOOM GHL Social Planner (Dreemzy)`, on `Create Post` + `Reconcile Post (GHL posts/list)`) + `LOOM Supabase` (reused on 5 Supabase nodes: 4 lookups + `Record Receipt`). Both gates remain `false`; **zero executions, zero publications**.
> **First next action (runtime test):** use the EXISTING receipt identity only (tenant `dreemzy`, proof clip/asset) to prove the no-duplicate guard returns `already_distributed` — without another YouTube upload.
> **Remaining before 6C:** apply migration 011 (SQL Editor) → runtime-verify Workflow 05 end-to-end.
> **Live deps:** Discord review adapter (`infra/discord-review/supervise.sh`) · GHL PIT token (single-location) · Supabase `edtcewkvnxrlzhqebayr`
> *(no credential values here)*

*Updated: 2026-09-13 (EDT) — Segments 3/4/5/6A complete; Segment 6B API runtime-verified via manual harness; migration 010 runtime-verified; Workflow 05 corrected (receipt short-circuit + reconciliation + MIME gate, 20 nodes) + credentials wired (inactive, guarded, zero executions, not yet runtime-verified); migration 011 written (pending SQL Editor)*

## ✅ Segment 4 — Discord QC — COMPLETE (runtime verified end-to-end)

Bryan accepted the Discord Interactions Endpoint URL and personally clicked
**Hold** exactly once on the labeled test card. The full signed path is now
runtime-verified, not just locally simulated.

### Evidence (2026-09-13 02:52 UTC)
1. Signed interaction reached the adapter; Ed25519-verified over the raw body.
2. Acknowledged within Discord's 3s deadline (edited ~1s after stored decision).
3. Correct clip/tenant/reviewer preserved — clip `6c46c3ef-…:1`, tenant
   `loom-acceptance-test`, reviewer `passportpetty#688976899094872148`.
4. Exactly one Hold decision persisted (`loom_reviews`); `on_hold`, null category.
5. No publication/GHL/retry/model/media-regen — `publication_enabled: false` at
   every node; webhook re-persisted idempotently (exec `10214`).
6. Duplicate handling: DB-level `ON CONFLICT (clip_id) DO NOTHING`.
7. Card reflected the stored Hold; controls disabled.

### Cleanup (done)
- The labeled test Hold + stale keypair-test row were deleted from `loom_reviews`
  (now empty). No production review decision has been made.

## ✅ Segment 5A–5D — GHL Media Storage — COMPLETE (storage-acceptance fixture)

Storage spine built and verified end-to-end against the live GHL Media Storage API.

- **5A auth / upload plane** — GHL PIT token + `Version: v3` confirmed; folder tree
  `LOOM/dreemzy/{Approved,Candidates,Published,Rejected,Sources}` present.
- **5B folder structure** — existing folders reused (no recreation).
- **5C canonical-media ledger** — `loom_media` table (migration
  `infra/supabase/002_loom_media.sql`); idempotent key `(tenant_id, clip_id, asset_version)`.
- **5D retrieve / validate / idempotency** — fixture registered; retrieval validated
  (HTTP 200, exact size); repeat-upsert proven idempotent (same row id, count = 1).

### Registered fixture (storage acceptance — NOT publication-approved)
- `loom_media` row `f8502269-a3f2-40ec-9562-97948273b765`
- tenant `dreemzy` · clip_id `6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1` · asset_version `render-1.0`
- storage_status `fixture`
- size 13,821,831 · sha256 `2b0b1e2a…f6dfed` (byte-identical to OpenShorts `subtitled_…_clip_2.mp4`)
- GHL media id `6aa61533c9a6c7937b6a3f6f` (folder `Approved`, location `GNhV1OyhFLzcl67XwTBV`)
- canonical URL `https://assets.cdn.filesafe.space/GNhV1OyhFLzcl67XwTBV/media/6fd1f93b-b143-4b0f-abfe-4556b9b29a37.mp4`
- Exactly one `loom_media` row; exactly one file in GHL `Approved` (no re-upload).

### Media filename naming contract (new)
Candidate identity is **zero-based**: `clip_id = <job_id>:<clip_index>` where
`clip_index=0` → OpenShorts `clip_1` (first short), `clip_index=1` → `clip_2`
(second short). The GHL filename token `clip1` denotes zero-based `clip_index=1`
(= second short), which is technically consistent but ambiguous. Going forward,
filenames must disambiguate explicitly, e.g.:
`index-01__ordinal-02__render-1.0` (zero-based index, human ordinal, render version).

## ✅ Segment 6 pre-distribution — reaction-lane identity + durable tokens (DEPLOYED & verified)

Migrations applied + code deployed (2026-09-13):

- **004** — `loom_media` explicit lineage + publication eligibility
  (`clip_index`, `human_ordinal`, `source_filename`, `publication_status`,
  `publication_review_id`, `publication_approved_at`) + Segment 6 distribution guard.
- **005** — `loom_reviews` reaction-lane identity: `asset_version` + `review_stage`
  (`moment` | `final_publication`); composite unique
  `(tenant_id, clip_id, asset_version, review_stage)`; first-terminal-decision-wins per stage.
- **006** — `loom_review_tokens` durable review tokens (restart-survivable; only the
  token hash is stored, never the raw token).
- Adapter + Workflow 04 deployed: composite review key, `ignore-duplicates` first-wins,
  durable token resolution, publication guard (moment decisions never touch publication).

Runtime verified end-to-end:
- Restart-survival: a card created before an adapter restart resolved its durable token
  correctly afterward (Hold persisted).
- Exactly one composite decision; a competing terminal action was ignored (first-wins).
- Moment Hold left `loom_media` publication state untouched.
- Test records cleaned; Segment 5 fixture remains `fixture` / `not_approved`.

## ✅ Segment 6A — Tenant Distribution Mapping — COMPLETE

GHL Social Planner API (`GET /social-media-posting/:locationId/accounts`, `Version: v3`)
verified read-only. One Dreemzy YouTube profile discovered + persisted.

- Migration **007** — `tenant_distribution_profiles` (idempotent key
  `(tenant_id, ghl_location_id, platform, profile_id)`).
- Mapping (exactly one row): tenant `dreemzy` · platform `youtube` ·
  profile `UCt1eM01Nyp0xC-mIAxfByBw` · display `Bryan Petty` ·
  connection `active` · `enabled_for_distribution=false` (fail-closed).
- Idempotency: repeat upsert → still exactly one row.
- Fail-closed verified: unknown tenant → empty; mismatched location → empty;
  expired Instagram (`be_petty_`) not mapped/enabled.
- Zero posts created/scheduled/published (read-only discovery only).

## Next segment

**6B remainder → 6C.** 6B API contract is runtime-verified (unlisted post live via a manual curl harness), but `Workflow 05` is still **prepared/not deployed**. First action tomorrow: run `010` + deploy Workflow 05 (guarded) + re-run the path through the workflow. 6C is then gated on Workflow 05 runtime verification + an explicitly `enabled_for_distribution=true` mapping + a real approved asset.

Segment 3 is closed and must not be reopened.

## Adapter / infra (unchanged this round, still correct)
- Tailscale Funnel exposes ONLY `/discord/interactions → 127.0.0.1:8090`; public
  DNS resolves; valid Let's Encrypt cert; signature rejection (401) + unrelated
  paths (404) verified via public URL.
- Adapter supervised via `infra/discord-review/supervise.sh`; source vs proposed
  title separation; explicit-language masking; publication status field.
- All 5 LOOM workflows active; Jasmine/Derm untouched.
- OpenShorts caption-contract fix preserved in `openshorts-recovery/` (upstream
  push 403, no fork).

## n8n execution IDs (this round)
- `10214` — WF04 review-decision webhook (Hold), success. (Segment 4)

## Commit / push status
- Segment 5A–5D: migration `002`, registration `003` — committed and pushed.
- Segment 6 pre-distribution: migrations `004`/`005`/`006`, adapter, Workflow 04 — committed and pushed.
- Segment 6A: migration `007` + this handoff — committed and pushed.
