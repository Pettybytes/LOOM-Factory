-- LOOM — Reaction Library: human-readable title columns.
-- Idempotent: safe to re-run. No secret values.
--
-- Titles are for humans; ids and hashes preserve machine identity. These columns
-- are DISTINCT from identity: ``display_title`` never replaces
-- ``platform_content_id``, and ``source_title`` keeps the platform's own words.
--
-- Until this migration is applied, both the writer and every reader keep the same
-- four fields inside the existing ``provenance`` JSONB column (a real column, so
-- no schema drift), and ``reaction_library.asset_titles()`` reads either shape.
-- Applying this migration therefore promotes existing values rather than
-- requiring a rewrite.

alter table public.loom_reaction_library
  add column if not exists source_title   text not null default '';

alter table public.loom_reaction_library
  add column if not exists display_title  text not null default '';

alter table public.loom_reaction_library
  add column if not exists creator_name   text not null default '';

alter table public.loom_reaction_library
  add column if not exists creator_handle text not null default '';

-- Promote the values already durably stored in provenance, without touching a
-- row that already carries a first-class value.
update public.loom_reaction_library
   set source_title   = coalesce(nullif(source_title, ''),   provenance ->> 'source_title', ''),
       display_title  = coalesce(nullif(display_title, ''),  provenance ->> 'display_title', ''),
       creator_name   = coalesce(nullif(creator_name, ''),   provenance ->> 'creator_name', ''),
       creator_handle = coalesce(nullif(creator_handle, ''), provenance ->> 'creator_handle', '')
 where coalesce(provenance, '{}'::jsonb) ?| array['source_title','display_title','creator_name','creator_handle'];

create index if not exists loom_reaction_library_display_title_idx
  on public.loom_reaction_library (display_title);

comment on column public.loom_reaction_library.display_title is
  'Cleaned human-readable title shown in Discord and in the Reaction Library.';
comment on column public.loom_reaction_library.source_title is
  'Title/caption exactly as the platform supplied it (cleaned only).';
