-- LOOM — Personal Reaction Library: tenant-neutral acquired source assets.
-- Idempotent: safe to re-run. No secret values.
--
-- Stores assets acquired for Bryan's reaction workflow. Tenant-neutral
-- (destination = 'personal_reaction_library'); a tenant association may be
-- added later via a separate audited routing action without rewriting
-- provenance.
--
-- Dedup: unique on asset_hash (sha256) — identical acquired bytes are stored
-- once. Retention: bounded (retention_days); expires_at computed at insert or
-- by the sweep. Files live in GHL Media Storage (canonical media store);
-- this table is the metadata/provenance ledger (Supabase does not replace GHL
-- media storage).

create table if not exists public.loom_reaction_library (
  id uuid primary key default gen_random_uuid(),
  duplicate_fingerprint text not null,
  platform text not null,
  platform_content_id text not null default '',
  canonical_url text not null default '',
  acquisition_job_id uuid,
  destination text not null default 'personal_reaction_library',
  asset_filename text not null,
  asset_hash text not null,
  size_bytes bigint,
  duration_seconds numeric,
  content_type text not null default 'video/mp4',
  storage_url text not null default '',
  retention_days integer not null default 30,
  expires_at timestamptz,
  consumed_at timestamptz,
  provenance jsonb,
  rights_usage_note text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_reaction_library_hash unique (asset_hash)
);

create index if not exists idx_loom_rl_fingerprint on public.loom_reaction_library (duplicate_fingerprint);
create index if not exists idx_loom_rl_platform on public.loom_reaction_library (platform);
create index if not exists idx_loom_rl_expires on public.loom_reaction_library (expires_at);

comment on table public.loom_reaction_library is
  'Personal Reaction Library: tenant-neutral acquired source assets for Bryan''s reaction workflow. Dedup by asset_hash (sha256). Retention bounded by retention_days. Files in GHL Media Storage; this is the metadata/provenance ledger.';
comment on column public.loom_reaction_library.provenance is
  'Immutable provenance: source fingerprint, platform, content id, canonical URL, acquisition job id.';
