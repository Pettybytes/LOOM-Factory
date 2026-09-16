-- LOOM Stage 2 — Automated Source Discovery V1: Source Brief contract store.
-- Idempotent: safe to re-run. No secret values.
--
-- A Source Brief expresses WHAT a tenant wants to discover (topics, keywords,
-- channels, freshness, rights policy). It is the shared, versioned input to
-- every discovery connector. The three planned content profiles (Bryan
-- face/reaction, pets ranking/commentary, wild-moments/story commentary) are
-- represented ONLY as non-production fixtures — they are NOT provisioned
-- tenants and MUST NOT be inserted as tenant rows here.

create table if not exists public.loom_source_briefs (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  profile_id text not null,
  version text not null,
  schema_version text not null default '1.0.0',
  target_platforms jsonb not null default '[]'::jsonb,
  topics jsonb not null default '[]'::jsonb,
  entities jsonb not null default '[]'::jsonb,
  keywords jsonb not null default '[]'::jsonb,
  exclusions jsonb not null default '[]'::jsonb,
  creator_channel_allowlist jsonb not null default '[]'::jsonb,
  creator_channel_blocklist jsonb not null default '[]'::jsonb,
  freshness_window_days numeric not null default 14,
  content_type_requirements jsonb not null default '["video"]'::jsonb,
  language text not null default 'en',
  provenance_rights_requirements text not null default '',
  acquisition_eligibility boolean not null default true,
  cost_ceiling_usd numeric not null default 0,
  manual_review_required boolean not null default true,
  connector_restrictions jsonb not null default '[]'::jsonb,
  min_evidence_requirements jsonb not null default '[]'::jsonb,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_source_briefs_identity unique (tenant_id, profile_id, version)
);

create index if not exists idx_loom_source_briefs_tenant on public.loom_source_briefs (tenant_id);
create index if not exists idx_loom_source_briefs_profile on public.loom_source_briefs (profile_id);

comment on table public.loom_source_briefs is
  'Versioned, tenant-scoped Source Brief contract (Automated Source Discovery V1). A brief describes what a tenant wants to discover; it never provisions a tenant and never authorizes publication.';
