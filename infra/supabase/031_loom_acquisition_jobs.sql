-- LOOM — Acquisition Boundary V1: durable, idempotent acquisition jobs.
-- Idempotent: safe to re-run. No secret values.
--
-- An acquisition job is created ONLY by an explicit 'acquire_for_reaction'
-- human action or a Source Review 'approved_for_acquisition' decision. Creating
-- a job records authorization and produces a status — it NEVER downloads media.
-- Actual download is a separate, explicitly-authorized step gated on a
-- runtime-proven platform adapter.
--
-- Idempotency: dedup_key is deterministic (fingerprint + authorization_type +
-- acquisition_mode + requested range); unique, so a double-click / redelivery
-- cannot create duplicate jobs. tenant_id / destination: '' or a tenant id, or
-- 'personal_reaction_library' (tenant-neutral).

create table if not exists public.loom_acquisition_jobs (
  id uuid primary key default gen_random_uuid(),
  dedup_key text not null,
  duplicate_fingerprint text not null,
  platform text not null,
  platform_content_id text not null,
  canonical_url text not null default '',
  authorization_type text not null,
  actor text not null default '',
  authorized_at timestamptz not null default now(),
  destination text not null default 'personal_reaction_library',
  acquisition_mode text not null default 'full_item',
  requested_start_s numeric,
  requested_end_s numeric,
  status text not null default 'queued',
  status_reason text not null default '',
  retry_count integer not null default 0,
  max_bytes bigint,
  max_duration_seconds numeric,
  asset_provenance jsonb,
  rights_usage_note text not null default '',
  correlation_id text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_acquisition_jobs_dedup unique (dedup_key)
);

create index if not exists idx_loom_acq_fingerprint on public.loom_acquisition_jobs (duplicate_fingerprint);
create index if not exists idx_loom_acq_platform on public.loom_acquisition_jobs (platform);
create index if not exists idx_loom_acq_status on public.loom_acquisition_jobs (status);
create index if not exists idx_loom_acq_destination on public.loom_acquisition_jobs (destination);

comment on table public.loom_acquisition_jobs is
  'Acquisition Boundary V1. One idempotent job per (fingerprint, authorization_type, mode, requested range). status: queued | running | acquired | blocked_pending_platform_adapter | failed. Records authorization only — never downloads media. destination: tenant id, '''' (unassigned), or ''personal_reaction_library''.';
comment on column public.loom_acquisition_jobs.authorization_type is
  'acquire_for_reaction (explicit human action) or approved_for_acquisition (Source Review decision).';
comment on column public.loom_acquisition_jobs.destination is
  'personal_reaction_library (tenant-neutral), a tenant id, or '''' (unassigned).';
comment on column public.loom_acquisition_jobs.asset_provenance is
  'Resulting asset provenance (filename/hash/size/storage_url) once acquired; null until then.';
