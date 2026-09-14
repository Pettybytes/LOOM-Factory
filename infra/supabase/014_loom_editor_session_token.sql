-- LOOM Stage 2 — Editor boundary security: short-lived editor session token.
-- Idempotent: safe to re-run. No secret values.
--
-- The launch token is single-use and consumed at /discord/editor/exchange. A NEW
-- opaque session token is then minted and its SHA-256 stored here, so the editor
-- can authenticate its /api/* calls (Authorization: Bearer) without exposing the
-- session id (a UUID) — public UUIDs are not authentication. Raw token never stored.

alter table public.loom_edit_sessions
  add column if not exists session_token_hash text;

create index if not exists idx_loom_edit_sessions_session_token_hash
  on public.loom_edit_sessions (session_token_hash);

comment on column public.loom_edit_sessions.session_token_hash is
  'SHA-256 of the short-lived editor session token minted at exchange (after the launch token is consumed). Presented by the editor as Authorization: Bearer for its authenticated /api/* calls. Raw token never stored; cleared/ignored once the session completes or expires.';
