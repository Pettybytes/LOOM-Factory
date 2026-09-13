-- LOOM Segment 6 pre-distribution: durable review tokens (Discord card → decision).
-- Idempotent: safe to re-run. No secret values.
--
-- Replaces the adapter's in-memory review-token map so an adapter/Mac/supervisor
-- restart does NOT invalidate pending Discord review cards.
-- Only an opaque token HASH and its server-side identity mapping are stored —
-- never secrets, never the raw token, never trusted identity inside custom_id.

create table if not exists public.loom_review_tokens (
  id uuid primary key default gen_random_uuid(),
  token_hash text not null unique,
  tenant_id text not null,
  clip_id text not null,
  asset_version text,
  review_stage text not null default 'moment',
  discord_message_id text,
  status text not null default 'pending',
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  constraint loom_review_tokens_stage_check
    check (review_stage in ('moment','final_publication')),
  constraint loom_review_tokens_status_check
    check (status in ('pending','consumed','expired'))
);

create index if not exists idx_loom_review_tokens_status on public.loom_review_tokens (status);
create index if not exists idx_loom_review_tokens_expires on public.loom_review_tokens (expires_at);

comment on column public.loom_review_tokens.token_hash is
  'SHA-256 hex of the opaque review token. The raw token is never stored and never logged.';
comment on column public.loom_review_tokens.status is
  'pending | consumed | expired. Expired tokens reject interactions (manual Hold), never auto-approve.';
comment on column public.loom_review_tokens.consumed_at is
  'Set when the first terminal decision consumes the token (first-wins).';
comment on column public.loom_review_tokens.expires_at is
  'Hard expiry (72h). Expiry = manual Hold/re-send, never approval.';

-- ---------------------------------------------------------------------------
-- Retention / expiry policy (documented):
--   * TTL = 72 hours (REVIEW_TOKEN_TTL_SECONDS in the adapter).
--   * On expiry, resolve_review_token() rejects the interaction; the candidate
--     remains undecided = manual Hold (never auto-approved).
--   * consumed/expired rows are retained for audit and may be purged after 30 days.
-- ---------------------------------------------------------------------------
