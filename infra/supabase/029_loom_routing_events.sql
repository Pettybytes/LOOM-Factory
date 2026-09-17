-- LOOM — Manual Source Intake V1.1: routing events (assignment/reassignment/copy audit).
-- Idempotent: safe to re-run. No secret values.
--
-- Immutable audit history. Each routing event records candidate, old/new
-- destination, operation, authorized actor, timestamp, optional reason, and a
-- correlation/idempotency identifier. The original submission is never
-- rewritten; history is append-only.

create table if not exists public.loom_routing_events (
  id uuid primary key default gen_random_uuid(),
  duplicate_fingerprint text not null,
  old_destination text not null default '',
  new_destination text not null default '',
  operation text not null,
  actor text not null default '',
  reason text not null default '',
  correlation_id text not null default '',
  created_at timestamptz not null default now()
);

create index if not exists idx_loom_re_fp on public.loom_routing_events (duplicate_fingerprint);
create index if not exists idx_loom_re_corr on public.loom_routing_events (correlation_id);

comment on table public.loom_routing_events is
  'Immutable routing audit (assign/reassign/copy). operation: assign | reassign | copy. old_destination/new_destination are tenant ids (empty string = Unassigned). correlation_id makes repeated delivery idempotent.';
