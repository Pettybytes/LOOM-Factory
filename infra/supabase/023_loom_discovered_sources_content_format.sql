-- LOOM Stage 2 — Topic Intelligence V1: content_format routing field.
-- Idempotent: safe to re-run. No secret values.
--
-- content_format is the content routing field (deferred assignment — nothing
-- is routed until a later segment). Reserved values:
--   reaction_single | reaction_compilation | commentary_story |
--   ranking_top5 | stream_highlight | hold_unassigned
--
-- Default 'hold_unassigned' — a discovered source is never auto-routed. This
-- field exists now so later routing (and the deferred Streamer Moment Scout
-- source type) do not require a breaking schema change.

alter table public.loom_discovered_sources
  add column if not exists content_format text not null default 'hold_unassigned';

comment on column public.loom_discovered_sources.content_format is
  'Content routing field (deferred assignment). Reserved values: reaction_single, reaction_compilation, commentary_story, ranking_top5, stream_highlight, hold_unassigned. Default hold_unassigned — nothing is auto-routed.';
