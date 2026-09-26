-- LOOM — Manual Intelligence Intake V1.2: supplied-media origin + durable media reference.
-- Idempotent: safe to re-run. No secret values.
--
-- V1 stored URL-only submissions. V1.2 additionally accepts operator-supplied
-- MEDIA (already acquired externally, commonly via Cobalt). These columns let
-- downstream stages distinguish a supplied-media submission from a URL-only one
-- without inventing a parallel model, and record who chose the clip window.

alter table public.loom_manual_submissions
  add column if not exists origin text not null default 'manual_url';

alter table public.loom_manual_submissions
  add column if not exists media_reference jsonb;

alter table public.loom_manual_submissions
  add column if not exists boundary_provenance text;

comment on column public.loom_manual_submissions.origin is
  'manual_url = URL-only submission (V1 intake). manual_media = operator supplied the media bytes (V1.2). Never overwritten silently on re-submit.';

comment on column public.loom_manual_submissions.media_reference is
  'Durable GHL/filesafe media identity for supplied media: {url,file_id,sha256,size_bytes,filename,duration_seconds,width,height,folder_id,registered_at}. Null for URL-only submissions. The bytes are NOT archived locally; this is the canonical durable pointer.';

comment on column public.loom_manual_submissions.boundary_provenance is
  'Who chose the candidate clip window: machine (Content Intelligence) or manual_intake (operator-supplied start/end). Null until a candidate boundary exists.';

create index if not exists idx_loom_ms_origin on public.loom_manual_submissions (origin);
