-- LOOM Segment 6B: tenant platform entitlement gate (minimal MVP field).
-- Idempotent: safe to re-run. Fail-closed default (false).
-- Three independent gates per tenant/platform: connected, entitled, enabled_for_distribution.

alter table public.tenant_distribution_profiles
  add column if not exists entitled boolean not null default false;

comment on column public.tenant_distribution_profiles.entitled is
  'Tenant platform entitlement (payment/tier). Fail-closed: false unless explicitly true. Full plan/tier capability is a future Segment 7/33; this is the minimal MVP gate.';
