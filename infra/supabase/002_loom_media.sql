-- LOOM canonical media storage ledger (Segment 5C)
-- Idempotent: safe to re-run. No secret values.
-- One row per stored media asset version; idempotent by (tenant_id, clip_id, asset_version).
-- GHL Media Storage is canonical for approved/published media (see LOOM_SOURCE_OF_TRUTH.md).

create table if not exists public.loom_media (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  run_id text,
  job_id text,
  clip_id text not null,
  asset_version text not null default 'r1',
  file_hash text,
  size_bytes bigint,
  duration_seconds numeric,
  ghl_location_id text,
  ghl_folder_id text,
  ghl_media_id text,
  ghl_filename text,
  canonical_url text,
  storage_status text not null default 'uploaded',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_media_identity unique (tenant_id, clip_id, asset_version)
);

create index if not exists idx_loom_media_tenant on public.loom_media (tenant_id);
create index if not exists idx_loom_media_clip on public.loom_media (clip_id);
create index if not exists idx_loom_media_status on public.loom_media (storage_status);
