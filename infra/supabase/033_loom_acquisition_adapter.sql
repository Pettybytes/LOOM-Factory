-- LOOM — Acquisition Boundary: adapter identity + non-production classification.
-- Idempotent: safe to re-run. No secret values.

alter table public.loom_acquisition_jobs
  add column if not exists adapter text not null default '';

alter table public.loom_reaction_library
  add column if not exists is_non_production boolean not null default false;

comment on column public.loom_acquisition_jobs.adapter is
  'Adapter that performed the acquisition (e.g. chrome_network_capture).';
comment on column public.loom_reaction_library.is_non_production is
  'True for non-production proof assets (excluded from production queues).';
