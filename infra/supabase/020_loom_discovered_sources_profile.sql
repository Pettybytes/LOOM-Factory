-- LOOM Stage 2 — Automated Source Discovery V1: profile_id on discovered sources.
-- Idempotent: safe to re-run. No secret values.
--
-- profile_id identifies which Source Brief profile produced each discovered
-- source. Normal production queue queries exclude acceptance/test/example
-- profiles by default (see discovery/source_brief.py is_non_production).
-- The acceptance profile 'supabase-acceptance-proof' is non-production evidence.

alter table public.loom_discovered_sources
  add column if not exists profile_id text not null default '';

create index if not exists idx_loom_ds_profile on public.loom_discovered_sources (profile_id);

comment on column public.loom_discovered_sources.profile_id is
  'Source Brief profile_id that produced this record. Normal production queue queries exclude acceptance/test/example profiles by default; profile_id is how that exclusion is scoped. Default '''' = non-production/unknown.';
