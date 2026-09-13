-- LOOM Segment 6B: tenant distribution publisher user mapping.
-- Idempotent: safe to re-run. No secret values.
--
-- Adds the smallest field needed to record which GHL user creates/publishes
-- posts on behalf of a tenant/platform. The GHL Social Planner create-post API
-- requires `userId` (a valid GHL user MongoDB ObjectId); resolve it dynamically
-- (via GET /users/?locationId=...) rather than hard-coding it in the workflow.

alter table public.tenant_distribution_profiles
  add column if not exists publisher_user_id text;

comment on column public.tenant_distribution_profiles.publisher_user_id is
  'GHL user id (MongoDB ObjectId) that creates/publishes posts for this mapping. Non-secret; tenant/location scoped; dynamically resolved via the GHL Users API; never globally hard-coded.';

-- Backfill the Dreemzy YouTube mapping with the live-verified Bryan Petty user id
-- (resolved from the GHL Users API on 2026-09-13, not inferred).
update public.tenant_distribution_profiles
set publisher_user_id = 'uT8mleycXWhVCWcGw3f6'
where tenant_id = 'dreemzy'
  and platform = 'youtube'
  and profile_id = 'UCt1eM01Nyp0xC-mIAxfByBw';
