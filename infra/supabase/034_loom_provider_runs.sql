-- LOOM — Paid-provider acquisition: provider run correlation + cost ledger.
-- Idempotent: safe to re-run. No secret values.
--
-- One row per provider (Apify) run. Records provider + Actor identity, the
-- acquisition job it served, the provider run id + build, status, and the
-- estimated + actual cost in microdollars (1e-6 USD). Never stores signed URLs.

create table if not exists public.loom_provider_runs (
  id uuid primary key default gen_random_uuid(),
  provider text not null default 'apify',
  actor_id text not null,
  actor_build text not null default '',
  run_id text not null,
  acquisition_job_id uuid references public.loom_acquisition_jobs(id),
  status text not null default '',
  cost_estimate_micro bigint not null default 0,
  cost_actual_micro bigint,
  output_ref text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_provider_runs_run unique (run_id)
);

create index if not exists loom_provider_runs_job_idx
  on public.loom_provider_runs (acquisition_job_id);

comment on table public.loom_provider_runs is
  'One row per paid-provider run (correlation + cost ledger). No signed URLs.';
