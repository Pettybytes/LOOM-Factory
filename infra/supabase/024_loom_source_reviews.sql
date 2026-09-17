-- LOOM — Source Review Inbox: source review decisions.
-- Idempotent: safe to re-run. No secret values.
--
-- One row per reviewed source (deduplicated by duplicate_fingerprint). First
-- terminal decision wins AT THE DATABASE LEVEL: ON CONFLICT DO NOTHING, so a
-- duplicate click / delivery cannot overwrite the first decision.
--
-- APPROVE FOR ACQUISITION records authorization and triggers NOTHING
-- (no download, no OpenShorts, no publishing). HOLD and REJECT are persisted
-- decisions only.

create table if not exists public.loom_source_reviews (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  profile_id text not null default '',
  duplicate_fingerprint text not null,
  platform text not null,
  platform_content_id text not null,
  title text not null default '',
  canonical_url text not null default '',
  decision text not null,
  reviewer text not null default '',
  rejection_category text,
  reviewer_instructions text,
  decided_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_source_reviews_identity unique (duplicate_fingerprint)
);

create index if not exists idx_loom_sr_tenant on public.loom_source_reviews (tenant_id);
create index if not exists idx_loom_sr_profile on public.loom_source_reviews (profile_id);
create index if not exists idx_loom_sr_decision on public.loom_source_reviews (decision);

comment on table public.loom_source_reviews is
  'Source Review Inbox decisions. First terminal decision wins per duplicate_fingerprint. approve_for_acquisition records authorization only (triggers nothing); hold and reject are persisted decisions. Non-production profiles excluded from production queues.';
