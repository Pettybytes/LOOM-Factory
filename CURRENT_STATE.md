# LOOM — Current State (handoff)

*Updated: 2026-09-13 (EDT) — Segment 5A–5D storage spine COMPLETE (fixture registered)*

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

## Next segment

**Segment 6A — GHL Social Planner / distribution.** Do NOT begin until a real
*approved* candidate exists (the current fixture is storage-acceptance only).
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
- Segment 5A–5D: migration `002`, registration `003`, and this handoff — committed and pushed.
