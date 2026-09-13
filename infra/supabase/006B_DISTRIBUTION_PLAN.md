# LOOM — Segment 6B: Distribution preparation (PREPARED, NOT DEPLOYED)

Status: research + design complete. Nothing deployed, no tenant mapping enabled,
no post created/scheduled/published. Do NOT deploy or enable until 6B is authorized.

## GHL Social Planner API (v3, verified live)

| Operation | Endpoint |
|---|---|
| List connected accounts | `GET /social-media-posting/:locationId/accounts` |
| Create post | `POST /social-media-posting/:locationId/posts` |
| Get post (status/reconcile) | `GET /social-media-posting/:locationId/posts/:id` |

Auth: Bearer PIT token (sub-account scoped, single location). Header `Version: v3`.

## YouTube post fields (create-post)

- `accountIds` — required; the `id` field from Get Accounts.
- `summary` — description/caption (YouTube description max 5,000 chars).
- `media` — array of media objects (YouTube: video required, max 1 video).
- `type` — top-level post type: `post` | `story` | `reel` (Shorts = `reel`).
- `status` — `scheduled` (requires `scheduleDate`) | `draft` | `published` | … (see status vocabulary).
- `scheduleDate` — ISO schedule timestamp (required for scheduled/in_review).
- `youtubePostDetails.title` — video title (max 100 chars).
- `youtubePostDetails.type` — `video` (regular) or `short` (YouTube Shorts).
- `youtubePostDetails.privacyLevel` — `private` | `public` | `unlisted`.

## Media identifiers

The `media` object array references media by URL (or GHL media id). The exact
`media` object JSON shape is NOT fully specified in the docs (only size/format
limits are). To be confirmed against the live API at deployment time before the
first real create.

## Idempotency key (exact — CORRECTED 2026-09-13)

GHL Social Planner has NO native idempotency key. The `distribution_receipts`
ledger is the source of truth. Exact key:

```
idempotency_key = "{tenant_id}:{platform}:{ghl_social_account_id}:{clip_id}:{asset_version}"
```

Enforced by `distribution_receipts` unique
`(tenant_id, platform, ghl_social_account_id, clip_id, asset_version)`.
A duplicate means the SAME tenant, destination account, clip AND asset version —
NOT merely the same render-version label (different clips can share render-1.0).
A duplicate request with the same key must resolve to the existing receipt —
never re-POST to GHL unless reconciliation proves the prior POST did not create a post.

## Sample create-post payload (placeholders only)

```json
{
  "accountIds": ["{{ghl_social_account_id}}"],
  "summary": "{{description}}",
  "media": [{"url": "{{canonical_url}}", "type": "video"}],
  "type": "reel",
  "status": "scheduled",
  "scheduleDate": "{{schedule_date_iso}}",
  "youtubePostDetails": {
    "title": "{{title}}",
    "type": "short",
    "privacyLevel": "{{privacy_level}}"
  }
}
```

## Mandatory preflight guard (workflow must reject unless ALL true)

1. `enabled_for_distribution === true` (tenant mapping).
2. tenant mapping exists and `connection_status === 'active'`.
3. live GHL account check returns `active===true && isExpired===false`.
4. `loom_media.publication_status === 'approved'`.
5. `loom_media.publication_review_id` is non-null.
6. linked `loom_reviews.decision === 'approve'`.
7. `loom_reviews.review_stage === 'final_publication'`.
8. `tenant_id`, `clip_id`, `asset_version` match across media + review + request.
9. `loom_media.storage_status` is verified/eligible (not `fixture`).
10. asset is not a fixture.
11. no existing `distribution_receipts` row for `(tenant_id, platform, asset_version)`.
12. scheduled time is valid for the tenant timezone.

## Unknown create/schedule outcome

- Never auto-retry a POST whose outcome is unknown.
- Reconcile via `GET /social-media-posting/:locationId/posts/:id` (or list) using the idempotency/correlation key.
- Retry only after proving the prior operation did NOT create a post.

## Rollback plan (6B)

- Workflow stays inactive until explicitly activated; deactivating/deleting the workflow is the primary rollback.
- Revert `008_distribution_receipts.sql` (drop table) if the schema must be removed.
- Revert the git commits (prepared artifacts).
- No data loss: no posts are created during preparation.

## Bounded acceptance procedure (at 6B deployment, later)

1. Confirm mapping `enabled_for_distribution=false`; run the workflow → must reject (guard).
2. Flip `enabled_for_distribution=true` for a controlled test.
3. Run with a nonpublic (unlisted) test post using a harmless test video.
4. Confirm exactly one `distribution_receipts` row (idempotency).
5. Confirm `GET post` returns the created post (reconciliation).
6. Confirm a duplicate request resolves to the same receipt (no re-POST).
7. Revert `enabled_for_distribution=false` + clean test records.

## Safest test option

GHL/YouTube SUPPORTS nonpublic posts via `youtubePostDetails.privacyLevel` = `private` | `unlisted`.

- **A** (existing finished reaction): none exists — the Segment 5 fixture is storage-acceptance only, not a finished reaction, and is explicitly prohibited.
- **B** (harmless original LOOM distribution-test video): requires generating a test asset (deferred — "do not generate a test asset yet").
- **C** (nonpublic method): SUPPORTED. Safest = **unlisted** YouTube test post via `privacyLevel: unlisted`.

Recommendation (Bryan chooses; I do not publish): **C — unlisted** test post, combined with a harmless test video generated later (B), once 6B is authorized.

## Three independent gates (per tenant/platform) — added 2026-09-13

1. **connected** — a live, active, verified, non-expired GHL social-account mapping exists.
2. **entitled** — the tenant's configuration/payment tier permits that platform (minimal MVP field `entitled`, fail-closed default `false`; full plan/tier = future Segment 7/33).
3. **enabled_for_distribution** — explicit operational authorization is `true`.

For the MVP controlled test: Dreemzy YouTube may be entitled; TikTok/Instagram/Facebook remain disabled; the expired Instagram is NOT eligible; other platforms do not gate the initial YouTube proof. Schema/workflow stays platform-neutral.

## Media payload schema — CONFIRMED (2026-09-13, live)

- `media` object: `{"url": <canonical GHL media URL>, "type": <MIME type>}`.
- `media.type` = the actual MIME type from the GHL media-library `contentType` (for this MP4: `video/mp4`). Do NOT hard-code `video/mp4` universally — resolve/validate `contentType` per asset.
- YouTube: `privacyLevel` supports `unlisted`; `youtubePostDetails.type=short`; top-level `type=reel`.
- create-post requires `userId` (valid GHL user ObjectId) — resolved live from `GET /users/?locationId=...` (Bryan Petty = `uT8mleycXWhVCWcGw3f6`); do NOT infer or globally hard-code.
- A `published` create response may OMIT the post id; reconcile/retrieve via **`POST /social-media-posting/:locationId/posts/list`** (NOT an assumed GET list route).
- Unknown POST outcomes must be reconciled (via list/get) before any retry.

## Generated test asset identity (2026-09-13)

- file: `/tmp/loom6b/loom_distribution_test.mp4`
- codec/h264, 1080×1920, 8.0s, 29,163 bytes
- sha256: `b753eced6e06c319ee10588ef80123e245471955583e378a35f76e54c8f69107`
- content: plain branded background + text "LOOM Distribution Test / Unlisted" (silent, no copyrighted material)
- NOT the Segment 5 copyrighted fixture; registered as a controlled distribution-test asset (unique tenant/clip/asset_version) before use.
