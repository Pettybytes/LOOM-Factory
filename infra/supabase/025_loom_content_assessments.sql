-- LOOM — Content Intelligence Preflight V1: content assessments.
-- Idempotent: safe to re-run. No secret values.
--
-- Stores the DERIVED Content Intelligence assessment per discovered source,
-- kept separate from the RAW observations. Keyed by idempotency_key so replay
-- is idempotent AND a new evidence version creates a new row (history is never
-- corrupted). Non-production profiles are excluded from production queues via
-- profile_id (mirrors discovery/source_brief.py markers).

create table if not exists public.loom_content_assessments (
  id uuid primary key default gen_random_uuid(),
  idempotency_key text not null,
  tenant_id text not null,
  profile_id text not null default '',
  platform text not null,
  duplicate_fingerprint text not null,
  content_assessment_version text not null,
  evidence_level integer not null default 0,
  evidence_types jsonb not null default '[]'::jsonb,
  evidence_completeness numeric not null default 0,
  scores jsonb not null default '{}'::jsonb,
  content_worthiness_score integer not null default 0,
  content_confidence numeric not null default 0,
  transcript_availability text not null default 'unavailable',
  transcript_quality text not null default '',
  candidate_timestamps jsonb not null default '[]'::jsonb,
  recommended_content_format text not null default 'hold_unassigned',
  recommended_angle text not null default '',
  reasons jsonb not null default '[]'::jsonb,
  flags jsonb not null default '[]'::jsonb,
  risk_flags jsonb not null default '[]'::jsonb,
  model_provider text not null default '',
  model_version text not null default '',
  prompt_version text not null default '',
  raw_model_reference text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_content_assessments_identity unique (idempotency_key)
);

create index if not exists idx_loom_ca_fp on public.loom_content_assessments (duplicate_fingerprint);
create index if not exists idx_loom_ca_tenant on public.loom_content_assessments (tenant_id);
create index if not exists idx_loom_ca_profile on public.loom_content_assessments (profile_id);
create index if not exists idx_loom_ca_level on public.loom_content_assessments (evidence_level);

comment on table public.loom_content_assessments is
  'Content Intelligence Preflight V1 assessment. Strictly evidence-gated: a content dimension may only be scored when its required evidence level is present. content_confidence is capped by the evidence-level ceiling. Idempotent per idempotency_key; a new evidence version creates a new row.';
