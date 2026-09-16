-- LOOM Stage 2 — Automated Source Discovery V1: body_excerpt on discovered sources.
-- Idempotent: safe to re-run. No secret values.
--
-- body_excerpt is the bounded plain-text excerpt of a source's body (populated
-- by connectors that expose a text body, e.g. Reddit RSS). Optional; empty
-- string when not applicable.

alter table public.loom_discovered_sources
  add column if not exists body_excerpt text not null default '';

comment on column public.loom_discovered_sources.body_excerpt is
  'Bounded plain-text excerpt of the source body (populated by connectors with a text body, e.g. Reddit RSS). Optional; '''' when not applicable.';
