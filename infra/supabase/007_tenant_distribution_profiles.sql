-- LOOM Segment 6A: tenant-to-distribution-profile mapping.
-- Idempotent: safe to re-run. No secret values.
-- Maps a tenant to its authorized distribution profile(s) per platform.
-- One shared distribution workflow loads this dynamically — no per-tenant
-- workflow duplication, no hard-coded tenant in the workflow.

create table if not exists public.tenant_distribution_profiles (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  ghl_location_id text not null,
  platform text not null,
  ghl_social_account_id text,
  profile_id text,
  display_name text,
  connection_status text not null default 'disconnected',
  enabled_for_distribution boolean not null default false,
  timezone text,
  expires_at timestamptz,
  is_expired boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_verified_at timestamptz,
  constraint tenant_distribution_profiles_identity
    unique (tenant_id, ghl_location_id, platform, profile_id)
);

create index if not exists idx_tenant_distribution_tenant
  on public.tenant_distribution_profiles (tenant_id);
create index if not exists idx_tenant_distribution_enabled
  on public.tenant_distribution_profiles (tenant_id, platform, enabled_for_distribution);

comment on column public.tenant_distribution_profiles.enabled_for_distribution is
  'Fail-closed: false until explicitly authorized. Distribution only reads enabled=true rows with a matching, active mapping.';
comment on column public.tenant_distribution_profiles.connection_status is
  'active | disconnected | expired | unknown. Derived from GHL Social Planner account state (active + isExpired).';
comment on column public.tenant_distribution_profiles.profile_id is
  'External platform identifier exposed by GHL (e.g. YouTube channel id = originId). Never inferred or fabricated.';
comment on column public.tenant_distribution_profiles.last_verified_at is
  'Timestamp of the last read-only verification against the GHL Social Planner API.';
