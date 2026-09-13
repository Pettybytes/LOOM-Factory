-- LOOM Segment 6B (correction 3): canonical MIME type on loom_media.
-- Idempotent: safe to re-run. No secret values.
--
-- Fail-closed strategy: existing rows are left NULL (unknown) — NOT defaulted to a
-- guessed MIME. The distribution workflow REJECTS content_type that is NULL or not
-- in the supported set, so no asset can distribute on a URL-extension-inferred MIME.

alter table public.loom_media
  add column if not exists content_type text;

-- Fail-closed constraint: NULL is allowed only to mean "unknown" (which the workflow
-- rejects); any non-NULL value must be a supported distribution MIME. Adding a new
-- MIME type requires amending this constraint (deliberate, not accidental).
alter table public.loom_media
  drop constraint if exists loom_media_content_type_check;
alter table public.loom_media
  add constraint loom_media_content_type_check
  check (
    content_type is null
    or content_type in ('video/mp4','video/quicktime','video/webm','video/x-m4v')
  );

comment on column public.loom_media.content_type is
  'Canonical MIME type from the GHL media-library contentType. NULL = unknown (fail-closed: distribution rejects). Never inferred from URL extension.';

-- No backfill: existing rows (incl. the Segment 5 fixture) remain NULL/fail-closed.
-- The fixture is storage_status='fixture' and not distribution-eligible regardless.
-- A real approved asset must populate content_type from the GHL media-library
-- contentType during storage verification (Segment 5 flow), not by URL sniffing.
