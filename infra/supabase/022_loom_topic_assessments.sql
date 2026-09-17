-- LOOM Stage 2 — Topic Intelligence V1: derived opportunity assessments.
-- Idempotent: safe to re-run. No secret values.
--
-- Stores the DERIVED Topic Intelligence score per discovered source, kept
-- separate from the RAW observation data (loom_discovered_sources.observed_metrics
-- + metric_history). One row per (duplicate_fingerprint, score_version): a
-- re-scoring of the same source with the same score version upserts in place
-- (never appends a duplicate), so replay is idempotent and score history is
-- never corrupted.
--
-- Topic Intelligence is deterministic and explainable: it measures observable
-- signals (freshness, engagement, velocity, relevance, provenance, cross-platform
-- confirmation, saturation) and produces a 0-100 opportunity score, an
-- opportunity class, a confidence value, human-readable reasons, and explicit
-- missing-data / low-confidence flags. It does NOT judge emotional/hook content
-- (that is later Moment Mining / Clip Ranking).

create table if not exists public.loom_topic_assessments (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  profile_id text not null default '',
  platform text not null,
  platform_content_id text not null,
  duplicate_fingerprint text not null,
  score_version text not null,
  opportunity_score integer not null default 0,
  opportunity_class text not null default '',
  confidence numeric not null default 0,
  data_completeness numeric not null default 0,
  content_format text not null default 'hold_unassigned',
  reasons jsonb not null default '[]'::jsonb,
  flags jsonb not null default '[]'::jsonb,
  signal_scores jsonb not null default '{}'::jsonb,
  normalized_signals jsonb not null default '{}'::jsonb,
  velocity jsonb not null default '{}'::jsonb,
  assessed_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_topic_assessments_identity unique (duplicate_fingerprint, score_version)
);

create index if not exists idx_loom_ta_tenant on public.loom_topic_assessments (tenant_id);
create index if not exists idx_loom_ta_profile on public.loom_topic_assessments (profile_id);
create index if not exists idx_loom_ta_class on public.loom_topic_assessments (opportunity_class);
create index if not exists idx_loom_ta_platform on public.loom_topic_assessments (platform);

comment on table public.loom_topic_assessments is
  'Derived Topic Intelligence assessment (Automated Source Discovery V1, Topic Intelligence V1). Deterministic 0-100 opportunity score + class + confidence + reasons + flags, stored separately from raw observations. Idempotent per (duplicate_fingerprint, score_version). Does NOT judge emotional/hook content. profile_id enables production-queue exclusion of acceptance/test/proof profiles.';
