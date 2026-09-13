# LOOM — Segment 6 pre-distribution: Workflow 04 compatibility plan (reaction-lane)

*Status: PREPARED, NOT deployed. Do not run 005 or deploy these changes until Bryan approves.*

This is a **reaction-lane extension discovered before distribution** — it is NOT a
reopening of Segment 4's historical acceptance. Segment 4's runtime-verified Hold
path remains closed.

## Why this exists

Bryan's reaction lane needs **two separate durable decisions** per candidate:

1. `review_stage='moment'` — does the extracted source moment enter the reaction queue?
2. `review_stage='final_publication'` — may a specific finished reaction asset enter GHL/distribution?

The current `loom_reviews` idempotency key (`clip_id` alone) cannot hold both decisions
for the same clip, nor distinguish asset versions.

## Current state (inspected)

- **Schema:** `loom_reviews.clip_id text not null unique` (constraint `loom_reviews_clip_id_key`),
  PK on `id`, CHECK `decision in ('approve','reject','hold')`. No `asset_version`, no `review_stage`.
- **Adapter** (`infra/discord-review/adapter.py`):
  - `supabase_upsert(..., ignore_duplicates=True)` → `on_conflict=clip_id`, `resolution=ignore-duplicates` (first-wins).
  - `persist_decision()` payload has NO `asset_version` / `review_stage`.
  - `supabase_get("loom_reviews", clip_id)` filters by `clip_id` only.
- **n8n Workflow 04:**
  - "Validate Review Decision" validates `clip_id` + `decision` (+ `rejection_category` for reject);
    builds `review_payload` WITHOUT `asset_version` / `review_stage`.
  - "Persist Review Decision" → `loom_reviews?on_conflict=clip_id` with `resolution=merge-duplicates` (overwrites).

> ⚠️ Discrepancy found: the adapter uses `ignore-duplicates` (first-wins), but n8n uses
> `merge-duplicates` (last-write-wins). Both currently key on `clip_id`. This plan
> unifies both to `ignore-duplicates` on the composite key.

## Changes required (deploy lockstep with 005)

### A. Adapter (`adapter.py`)
1. `persist_decision(...)`: add `asset_version` + `review_stage` parameters; include both in the payload.
2. `supabase_upsert(...)`: change `on_conflict=clip_id` → `on_conflict=tenant_id,clip_id,asset_version,review_stage`;
   keep `ignore-duplicates` (first-wins).
3. `supabase_get(...)`: filter by full identity (clip_id + review_stage [+ asset_version]),
   not `clip_id` alone (a clip can now have 2+ rows).
4. `_decide_and_update(...)`: derive `review_stage` from which card/button was used (moment vs final_publication).
5. `forward_to_n8n(...)`: include `asset_version` + `review_stage`.
6. **Preserve unchanged:** Ed25519 signature verification over the raw body; POST-only
   state changes (GET only serves `/health`); explicit-language display masking.

### B. n8n Workflow 04
1. "Validate Review Decision": require + validate `asset_version` and `review_stage`
   (`moment` | `final_publication`); include both in `review_payload`.
2. "Persist Review Decision": change URL to
   `loom_reviews?on_conflict=tenant_id,clip_id,asset_version,review_stage`;
   change `Prefer` to `resolution=ignore-duplicates,return=representation` (first-wins, matches adapter).
3. **Preserve unchanged:** webhook POST path; the adapter owns Discord signature verification
   (n8n only receives the already-verified, normalized event).

### C. Guard enforcement (application code, not schema)
- A `moment` approval must NOT populate `loom_media.publication_review_id`,
  set `publication_status='approved'`, authorize GHL publication storage, or authorize scheduling.
- Only `final_publication` + `approve` + matching `tenant_id` + `clip_id` + `asset_version`
  may populate `publication_review_id` / `publication_approved_at` / transition `publication_status`.

## Safe migration / deployment order (revised for durable tokens)

1. Confirm **no active review traffic** (Review Gate is currently idle; Segment 4 test is closed).
2. Run `006_loom_review_tokens.sql` in the Supabase SQL Editor (durable token table).
3. Run `005_loom_reviews_asset_stage.sql` in the Supabase SQL Editor (composite review key).
4. Update + deploy the adapter (restart via `infra/discord-review/supervise.sh`).
5. Update Workflow 04 **by exact workflow ID** — n8n API `PUT /api/v1/workflows/{id}` — do NOT use “Import from File”, which can create a duplicate active Review Gate.
6. Run the bounded tests below.

> Deploying 005 before the code changes (or vice versa) breaks the upsert: after 005,
> `on_conflict=clip_id` no longer matches a unique constraint, so PostgREST returns an error.
> They must ship together; the idle Review Gate makes this safe.

## Bounded test (after deploy)

1. Insert two decisions for the SAME clip — `moment` and `final_publication` — confirm **2 rows** persist.
2. Re-send the same `(tenant, clip, asset_version, stage)` decision — confirm it is **ignored** (first-wins, count unchanged).
3. Submit a `moment` approval and confirm it does **not** touch `loom_media.publication_*` and does **not** set `publication_status='approved'`.
4. Submit a `final_publication` approve with matching identity and confirm it **may** populate
   `publication_review_id` / `publication_approved_at` / `publication_status='approved'` (and a mismatched identity is rejected).

## Durable review tokens (006)

The reaction-lane custom_id carries only `{decision, token}`; the full identity is
resolved from a durable Supabase table (`loom_review_tokens`) at decision time.

- `token_hash` (SHA-256) is stored, never the raw token; no secrets or trusted identity in custom_id.
- `resolve` rejects unknown / expired / consumed tokens.
- Restart-survivable: an adapter/Mac/supervisor restart does NOT invalidate an unexpired card.
- First terminal decision atomically consumes the token (`PATCH … WHERE status='pending'`), so a competing action affects zero rows.
- Reject modal reuses the same token between open and submit; only final submit consumes it.
- Expiry (72h) rejects the interaction → manual Hold; never auto-approve.
- Retention: consumed/expired rows retained for audit; purge after 30 days.

## Restart-survival acceptance test (after deploy)

1. Create a labeled test review card/token.
2. Restart the adapter (`pkill -f 'python3 adapter.py'`; supervise.sh restarts it).
3. Perform one real human Hold action from that pre-restart card.
4. Confirm the durable token resolves correctly (decision persists).
5. Confirm exactly one composite `loom_reviews` row.
6. Attempt a competing action and confirm it cannot overwrite the first decision.
7. Clean only the labeled test records afterward (delete the test `loom_reviews` row + token row).
