-- LOOM Stage 2 — Editor: durable trim draft (survives Activity reload).
-- Idempotent: safe to re-run. No secrets.
--
-- The editor persists the in-progress trim (and other manual-edit intent) as a
-- JSON blob on the edit session BEFORE starting a render, so an Activity reload
-- or re-open cannot erase the user's draft. It is never used to mutate asset
-- version or review state — only to restore the editor UI.

alter table public.loom_edit_sessions
  add column if not exists draft jsonb;

comment on column public.loom_edit_sessions.draft is
  'In-progress manual-edit draft (trim_start/trim_end, framing, caption intent) persisted before render so an Activity reload cannot erase it. Never used to mutate asset_version or review state.';
