-- LOOM — Combined Source Selection V1: source selections.
-- Idempotent: safe to re-run. No secret values.
--
-- Stores the combined SOURCE SELECTION (market + content + fit + provenance)
-- as a SEPARATE score and a SEPARATE confidence. Keyed by idempotency_key
-- (fingerprint + scoring-policy version) so replay is idempotent and a policy
-- change creates a new versioned row. Non-production profiles excluded via
-- profile_id.

create table if not exists public.loom_source_selections (
  id uuid primary key default gen_random_uuid(),
  idempotency_key text not null,
  tenant_id text not null,
  profile_id text not null default '',
  platform text not null,
  duplicate_fingerprint text not null,
  scoring_policy_version text not null,
  market_opportunity_score integer not null default 0,
  market_confidence numeric not null default 0,
  content_worthiness_score integer not null default 0,
  content_confidence numeric not null default 0,
  tenant_format_fit_score integer not null default 0,
  provenance_usability_score integer not null default 0,
  source_selection_score integer not null default 0,
  source_selection_confidence numeric not null default 0,
  recommended_format text not null default 'hold_unassigned',
  recommended_angle text not null default '',
  suggested_timestamps jsonb not null default '[]'::jsonb,
  reason_codes jsonb not null default '[]'::jsonb,
  explanation text not null default '',
  missing_evidence jsonb not null default '[]'::jsonb,
  disqualifying_risks jsonb not null default '[]'::jsonb,
  outcome text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_source_selections_identity unique (idempotency_key)
);

create index if not exists idx_loom_ss_fp on public.loom_source_selections (duplicate_fingerprint);
create index if not exists idx_loom_ss_tenant on public.loom_source_selections (tenant_id);
create index if not exists idx_loom_ss_profile on public.loom_source_selections (profile_id);
create index if not exists idx_loom_ss_outcome on public.loom_source_selections (outcome);

comment on table public.loom_source_selections is
  'Combined Source Selection V1: weighted (35% market + 45% content + 15% fit + 5% provenance) score + separate confidence + deterministic outcome. Idempotent per idempotency_key.';
