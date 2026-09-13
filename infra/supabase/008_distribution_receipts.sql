-- LOOM Segment 6B: distribution receipt ledger (idempotency + reconciliation).
-- Idempotent: safe to re-run. No secret values.
-- One row per distribution attempt. GHL Social Planner has NO native idempotency
-- key, so this ledger is the source of truth for dedup + unknown-outcome reconciliation.
--
-- Exact idempotency identity (corrected 2026-09-13):
--   tenant_id + platform + ghl_social_account_id + clip_id + asset_version
-- A duplicate means the SAME tenant, destination account, clip AND asset version —
-- NOT merely the same render-version label (different clips can share render-1.0).

create table if not exists public.distribution_receipts (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  ghl_location_id text not null,
  platform text not null,
  ghl_social_account_id text not null,
  profile_id text,
  clip_id text not null,
  asset_version text not null,
  idempotency_key text not null,
  ghl_post_id text,
  status text not null default 'attempted',
  scheduled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_reconciled_at timestamptz,
  constraint distribution_receipts_identity
    unique (tenant_id, platform, ghl_social_account_id, clip_id, asset_version),
  constraint distribution_receipts_status_check
    check (status in ('attempted','created','scheduled','published','failed','unknown','reconciled'))
);

create index if not exists idx_distribution_receipts_tenant on public.distribution_receipts (tenant_id);
create index if not exists idx_distribution_receipts_status on public.distribution_receipts (status);

comment on column public.distribution_receipts.idempotency_key is
  'Exact idempotency key: tenant_id:platform:ghl_social_account_id:clip_id:asset_version. Unique; prevents duplicate distribution attempts.';
comment on column public.distribution_receipts.status is
  'attempted | created | scheduled | published | failed | unknown | reconciled. unknown = POST outcome unconfirmed; reconcile via GHL GET post.';
comment on column public.distribution_receipts.ghl_post_id is
  'Returned GHL Social Planner post id, when known. Null until a create outcome is confirmed.';
