-- LOOM Stage 2 — Editor reliability: append-only editor session credentials.
--
-- Problem this fixes: editor credentials were stored as a SINGLE mutable column
-- on loom_edit_sessions (session_token_hash). Minting a token for a session that
-- already had one OVERWROTE the previous hash, silently orphaning the editor
-- that was still holding the earlier token — every later call from that editor
-- was rejected as `hash_mismatch` even though its credential was legitimate.
-- Two legitimate mints for one session happen in practice: the deep-link
-- `/discord/editor/exchange` and the Activity `/discord/editor/pending` resolve
-- the SAME active session for the same clip/user.
--
-- Fix: keep one row per minted credential. Tokens are appended, never rebound
-- or revoked by a later mint, so an open editor keeps working while a second
-- editor can also authenticate. Validation joins a presented token's hash to its
-- session row; the legacy column remains as a write-once fallback for
-- deployments where this table is not present.
--
-- No secret is stored: only the SHA-256 of the opaque token.
--
-- Idempotent: safe to re-run.

create table if not exists public.loom_editor_session_tokens (
  id              uuid primary key default gen_random_uuid(),
  edit_session_id uuid not null references public.loom_edit_sessions(id) on delete cascade,
  token_hash      text not null unique,
  created_at      timestamptz not null default now(),
  expires_at      timestamptz,
  revoked_at      timestamptz
);

create index if not exists idx_loom_editor_session_tokens_session
  on public.loom_editor_session_tokens (edit_session_id);

create index if not exists idx_loom_editor_session_tokens_hash
  on public.loom_editor_session_tokens (token_hash);

comment on table public.loom_editor_session_tokens is
  'Append-only editor session credentials. One row per minted opaque token (SHA-256 only, never the raw token). Tokens are never rebound: minting a second credential for a session leaves the first one valid, which is what removes the orphaned-editor hash_mismatch defect. A row with revoked_at set is rejected like any other terminal credential.';

comment on column public.loom_editor_session_tokens.token_hash is
  'SHA-256 of the opaque editor session token. The raw token is returned once, at mint time, and never stored.';

-- Service-role only: the adapter mints and validates credentials server-side.
-- The browser/Activity never touches this table directly.
alter table public.loom_editor_session_tokens enable row level security;

revoke all on public.loom_editor_session_tokens from public, anon, authenticated;
grant select, insert, update on public.loom_editor_session_tokens to service_role;
