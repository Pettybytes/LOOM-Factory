-- LOOM — creator performance history (baseline evidence) + sourcing seed yield.
-- Idempotent: safe to re-run. No secret values.
--
-- WHY: the deterministic baseline/outlier math already existed but had no durable
-- evidence. Creator-relative performance (views vs the creator's OWN median) needs
-- accumulated observations to be trustworthy. This is LOOM's own history — it is
-- NOT a second database, just two tables in the existing store.
--
-- Baseline rules enforced in code (not here): MEDIAN not mean, bounded recent
-- window, pinned/admin content excluded, insufficient history returns UNKNOWN, and
-- a baseline is never fabricated.

create table if not exists public.loom_creator_observations (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  platform text not null,
  creator_id text not null,
  creator_name text not null default '',
  source_content_id text not null,
  published_at timestamptz,
  observed_at timestamptz not null default now(),
  views bigint,
  likes bigint,
  comments bigint,
  shares bigint,
  saves bigint,
  is_pinned boolean not null default false,
  provider text not null default '',
  provenance text not null default '',
  -- Re-observing the same source UPDATES its row instead of duplicating it.
  constraint loom_creator_obs_identity unique (tenant_id, platform, creator_id, source_content_id)
);

create index if not exists idx_loom_creator_obs_lookup
  on public.loom_creator_observations (tenant_id, platform, creator_id, published_at desc);

comment on table public.loom_creator_observations is
  'Per-source performance observations used to derive creator-relative baselines. Upsert on (tenant_id, platform, creator_id, source_content_id) so repeated observation of the same source cannot duplicate history.';

create table if not exists public.loom_seed_yield (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  platform text not null,
  provider text not null default '',
  seed_key text not null,
  query_text text not null default '',
  creator_seed text not null default '',
  discovered int not null default 0,
  market_passed int not null default 0,
  market_rejected int not null default 0,
  tenant_passed int not null default 0,
  tenant_rejected int not null default 0,
  inspected int not null default 0,
  fire_passed int not null default 0,
  carded int not null default 0,
  approved int not null default 0,
  held int not null default 0,
  rejected int not null default 0,
  provider_cost_usd numeric not null default 0,
  vidiq_credits numeric not null default 0,
  first_seen_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_seed_yield_identity unique (tenant_id, platform, provider, seed_key)
);

create index if not exists idx_loom_seed_yield_lookup
  on public.loom_seed_yield (tenant_id, platform, seed_key);

comment on table public.loom_seed_yield is
  'Sourcing effectiveness per seed/query: counters plus provider cost and vidIQ credits. Instrumentation only - no ML, no automatic query mutation.';
