-- LOOM — Manual Source Intake V1.1: tenant registry (controlled destinations).
-- Idempotent: safe to re-run. No secret values.
--
-- The single source of truth for ACTIVE production tenants. Manual intake and
-- assignment only accept destinations that are active rows here (plus the
-- Unassigned routing state, which is NOT a tenant and is not a row here).

create table if not exists public.loom_tenants (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  name text not null,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_tenants_identity unique (tenant_id)
);

create index if not exists idx_loom_tenants_status on public.loom_tenants (status);

-- Seed the one real tenant (Dreemzy's own). Real client tenants are provisioned
-- later by Bryan. Fail-closed: intake/assignment only reads status='active'.
insert into public.loom_tenants (tenant_id, name, status) values
  ('dreemzy', 'Dreemzy AI (General)', 'active')
on conflict (tenant_id) do nothing;

comment on table public.loom_tenants is
  'Controlled active production tenant registry (Manual Source Intake V1.1). Destinations must be an active row here or the Unassigned routing state (which is NOT a tenant). Never accept arbitrary free-form tenant ids.';
