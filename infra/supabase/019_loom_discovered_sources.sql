-- LOOM Stage 2 — Automated Source Discovery V1: normalized discovery record.
-- Idempotent: safe to re-run. No secret values.
--
-- One row per discovered source, deduplicated by a tenant-scoped fingerprint.
-- Metric history is preserved via jsonb (appended, never overwritten).
-- Velocity inputs and opportunity classification are RESERVED for Topic
-- Intelligence and MUST NOT be computed in this segment.

create table if not exists public.loom_discovered_sources (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  source_brief_version text not null default '',
  platform text not null,
  canonical_url text not null,
  platform_content_id text not null,
  creator_channel_identity text not null default '',
  creator_channel_url text not null default '',
  title text not null default '',
  publication_timestamp timestamptz,
  observed_metrics jsonb not null default '{}'::jsonb,
  metric_observation_timestamp timestamptz,
  metric_history jsonb not null default '[]'::jsonb,
  velocity_inputs jsonb not null default '{}'::jsonb,
  discovery_query text not null default '',
  discovery_reason text not null default '',
  opportunity_classification text not null default '',
  evidence_confidence numeric,
  provenance_rights_evidence text not null default '',
  duplicate_fingerprint text not null,
  global_fingerprint text not null default '',
  near_duplicate_key text not null default '',
  processing_eligibility text not null default 'manual_ingest_only',
  qualification_status text not null default 'pending',
  rejection_reason text not null default '',
  connector_name text not null default '',
  connector_version text not null default '',
  raw_evidence_reference text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_discovered_sources_identity unique (duplicate_fingerprint)
);

create index if not exists idx_loom_ds_tenant on public.loom_discovered_sources (tenant_id);
create index if not exists idx_loom_ds_platform on public.loom_discovered_sources (platform);
create index if not exists idx_loom_ds_global_fp on public.loom_discovered_sources (global_fingerprint);
create index if not exists idx_loom_ds_near_key on public.loom_discovered_sources (near_duplicate_key);
create index if not exists idx_loom_ds_status on public.loom_discovered_sources (qualification_status);

comment on table public.loom_discovered_sources is
  'Normalized discovery record (Automated Source Discovery V1). Deduplicated by tenant-scoped fingerprint; metric history is append-only. velocity_inputs/opportunity_classification are RESERVED (Topic Intelligence, not this segment). A qualified source only ever advances to the manual-ingest boundary — never auto-downloaded, processed, or published.';
