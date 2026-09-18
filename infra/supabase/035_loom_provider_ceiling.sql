-- LOOM — Paid-provider circuit breaker (single-row spending state).
-- Idempotent: safe to re-run. No secret values.
--
-- A single row tracks lifetime spend + successful-acquisition count against the
-- pilot ceiling. Dispatch is disabled when either bound is reached. Budget
-- (Bryan, 2026-09-17): $1.00 lifetime ceiling + max 10 successful acquisitions.

create table if not exists public.loom_provider_ceiling (
  id integer primary key default 1 check (id = 1),
  ceiling_micro bigint not null default 1000000,
  lifetime_spent_micro bigint not null default 0,
  max_acquisitions integer not null default 10,
  acquisitions_succeeded integer not null default 0,
  disabled boolean not null default false,
  updated_at timestamptz not null default now()
);

insert into public.loom_provider_ceiling
  (id, ceiling_micro, lifetime_spent_micro, max_acquisitions, acquisitions_succeeded, disabled)
values (1, 1000000, 0, 10, 0, false)
on conflict (id) do nothing;

comment on table public.loom_provider_ceiling is
  'Single-row circuit breaker: $1 lifetime ceiling + max 10 successful acquisitions.';
