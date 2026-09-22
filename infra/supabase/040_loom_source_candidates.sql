-- LOOM — 040: authoritative source candidates + FIRE fields on selections.
-- Idempotent: safe to re-run. No secret values.
--
-- The Source Review queue needs ONE joined record per candidate carrying the
-- FINAL pipeline state (source identity + scores + FIRE + boundaries +
-- completeness + ranking/dedup + inspection evidence). Previously FIRE and
-- moment evidence lived only in memory: the queue read pre-FIRE selection rows,
-- so candidates surfaced without fire_passed/completeness and with blank titles
-- when no discovered_sources row existed.
--
-- This migration:
--   1. adds the FIRE/moment columns to loom_source_selections (so the existing
--      queue join keeps working and is FIRE-filterable), and
--   2. creates loom_source_candidates, the single authoritative record the
--      pipeline persists after FIRE + run-level ranking.

-- 1. FIRE / moment evidence on selections (nullable: pre-migration rows lack it).
alter table public.loom_source_selections
  add column if not exists fire_passed boolean,
  add column if not exists fire_reasons jsonb,
  add column if not exists evidence_confidence integer,
  add column if not exists content_score_recalibrated integer,
  add column if not exists moment_evidence jsonb,
  add column if not exists boundary_plan jsonb,
  add column if not exists completeness jsonb,
  add column if not exists moment_type jsonb,
  add column if not exists source_mode text not null default '',
  add column if not exists source_relative_rank integer,
  add column if not exists source_candidate_count integer,
  add column if not exists duplicate_state text not null default '',
  add column if not exists rank_evidence jsonb,
  add column if not exists inspection_attempts jsonb,
  add column if not exists media_assessment_status text not null default '';

create index if not exists idx_loom_ss_fire
  on public.loom_source_selections (fire_passed)
  where fire_passed is true;

-- 2. The authoritative joined candidate record.
create table if not exists public.loom_source_candidates (
  id uuid primary key default gen_random_uuid(),
  duplicate_fingerprint text not null,
  tenant_id text not null default '',
  profile_id text not null default '',
  platform text not null default '',
  platform_content_id text not null default '',
  title text not null default '',
  canonical_url text not null default '',
  creator_channel_identity text not null default '',
  publication_timestamp timestamptz,
  -- scores (final, post-evidence)
  market_score integer not null default 0,
  content_score integer not null default 0,
  selection_score integer not null default 0,
  confidence_score integer not null default 0,
  selection_outcome text not null default '',
  -- qualification
  fire_passed boolean not null default false,
  fire_reasons jsonb not null default '[]'::jsonb,
  completeness jsonb,
  completeness_verdict text not null default '',
  boundary_plan jsonb,
  moment_type jsonb,
  moment_evidence jsonb,
  evidence_depth text not null default '',
  source_mode text not null default '',
  -- run-level ordering / dedup
  source_relative_rank integer,
  source_candidate_count integer not null default 0,
  duplicate_state text not null default '',
  rank_evidence jsonb,
  -- inspection honesty
  inspection_attempts jsonb,
  media_assessment_status text not null default '',
  media_assessment jsonb,
  signal_status text not null default '',
  status text not null default '',
  rejection_reason text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_source_candidates_identity unique (duplicate_fingerprint)
);

create index if not exists idx_loom_sc_fire
  on public.loom_source_candidates (fire_passed)
  where fire_passed is true;
create index if not exists idx_loom_sc_tenant on public.loom_source_candidates (tenant_id);
create index if not exists idx_loom_sc_profile on public.loom_source_candidates (profile_id);
create index if not exists idx_loom_sc_dup
  on public.loom_source_candidates (duplicate_state);

comment on table public.loom_source_candidates is
  'Authoritative joined candidate record persisted AFTER FIRE + run-level ranking: source identity, final scores, fire/completeness/boundaries/evidence, dedup state. The Source Review queue consumes this table.';
