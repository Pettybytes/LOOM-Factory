# LOOM — Segment 4 (Discord QC + durable decisions) — Design

## Candidate card (4B)
One Discord embed per candidate. Fields:
- `preview_url` — final rendered asset
- `source` — source URL / job id
- `hook` — `suggested_hook`
- `caption` — `suggested_caption`
- `loom_score` — `overall_score` + `decision`
- `duration_seconds`
- `tenant_id`
- `run_id`
- `clip_id` — candidate/asset identity
- `recipe` — output format, layout, hook style, caption settings, duration target
- `version` — scoring `schema_version` + processing recipe version

## Human actions (4C)
- `approve` — distribution-eligible (requires persisted decision)
- `reject` — structured rejection category (required)
- `hold` — manual review, no downstream action
- `retry_failed_step` — technical-failure-only, smallest failed operation, one attempt then hold

## Durable decision record (4D, Supabase `loom_reviews`)
- `id` (uuid)
- `clip_id` / `asset_url` — candidate/asset identity
- `tenant_id`
- `reviewer`
- `decision` (`approve` | `reject` | `hold`)
- `rejection_category` — structured, for reject
- `reviewer_instructions` — optional
- `final_status`
- `created_at`

## Retry semantics
- Retry targets the smallest failed operation (e.g. re-fetch transcript), never a paid model call with an unknown outcome.
- One retry attempt, then hold.
- Never auto-regenerate media.

## Score persistence
- Persist candidate scores to Supabase (`loom_scores`), not only via resume payloads.
- Idempotent by `clip_id` — repeated review clicks must not duplicate downstream actions.
- Distribution requires a successfully persisted human approval.
