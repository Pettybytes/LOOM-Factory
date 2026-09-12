-- LOOM operational + decision ledger (Segment 4D)
-- Idempotent: safe to re-run. No secret values.
-- Tables: loom_scores (candidate score persistence), loom_reviews (human decisions).

-- ---------------------------------------------------------------------------
-- loom_scores — persist candidate scores, idempotent by clip_id
-- ---------------------------------------------------------------------------
create table if not exists public.loom_scores (
  id uuid primary key default gen_random_uuid(),
  clip_id text not null unique,
  asset_url text,
  tenant_id text,
  run_id text,
  job_id text,
  schema_version text,
  hook_score integer,
  completeness_score integer,
  clarity_score integer,
  emotional_intensity_score integer,
  shareability_score integer,
  context_dependency_score integer,
  duration_score integer,
  overall_score integer,
  decision text,
  suggested_hook text,
  suggested_caption text,
  openshorts_score integer,
  confidence numeric,
  evidence_summary jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- loom_reviews — durable human decisions (approve | reject | hold)
-- One current decision per clip_id (upsert); idempotent by clip_id so
-- repeated clicks do not duplicate downstream actions.
-- ---------------------------------------------------------------------------
create table if not exists public.loom_reviews (
  id uuid primary key default gen_random_uuid(),
  clip_id text not null unique,
  asset_url text,
  tenant_id text,
  reviewer text,
  decision text not null check (decision in ('approve', 'reject', 'hold')),
  rejection_category text,
  reviewer_instructions text,
  final_status text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Idempotent indexes (create if not exists)
-- ---------------------------------------------------------------------------
create index if not exists idx_loom_scores_tenant on public.loom_scores (tenant_id);
create index if not exists idx_loom_scores_run on public.loom_scores (run_id);
create index if not exists idx_loom_reviews_tenant on public.loom_reviews (tenant_id);
create index if not exists idx_loom_reviews_decision on public.loom_reviews (decision);
