-- LOOM Stage 2 — Editor: pending Activity-launch context (clip-specific
-- launch via LAUNCH_ACTIVITY callback type 12). Idempotent: safe to re-run.
-- No secrets.
--
-- When a reviewer clicks Edit on a review card, the adapter resolves the
-- durable review token, creates/resumes a pending edit session, and binds it to
-- the authenticated Discord user + interaction guild/channel context. It then
-- responds with LAUNCH_ACTIVITY (type 12) — NO raw launch token, job id, or
-- unrestricted clip identity ever enters a public URL.
--
-- When the Activity loads at its root, it boots the Embedded App SDK + OAuth,
-- then fetches the pending edit context for the authenticated user. The adapter
-- looks the session up by the numeric Discord user id (server-side, resolved
-- from the OAuth access token — never trusted from the client), validates the
-- user matches, mints the short-lived editor session token, and returns the
-- exact tenant/clip/asset_version.

alter table public.loom_edit_sessions
  add column if not exists launch_user_id text;

alter table public.loom_edit_sessions
  add column if not exists launch_guild_id text;

alter table public.loom_edit_sessions
  add column if not exists launch_channel_id text;

create index if not exists idx_loom_edit_sessions_launch_user
  on public.loom_edit_sessions (launch_user_id, status);

comment on column public.loom_edit_sessions.launch_user_id is
  'Numeric Discord user id of the reviewer who clicked Edit (resolved from the signed interaction). Bound at launch so the Activity can resolve the pending edit session after OAuth — never placed in a URL.';
comment on column public.loom_edit_sessions.launch_guild_id is
  'Discord guild id where Edit was clicked (interaction context).';
comment on column public.loom_edit_sessions.launch_channel_id is
  'Discord channel id where Edit was clicked (interaction context).';
