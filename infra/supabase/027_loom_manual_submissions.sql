-- LOOM — Manual Source Intake V1: manual submissions.
-- Idempotent: safe to re-run. No secret values.
--
-- One row per accepted manual submission, deduplicated deterministically by
-- duplicate_fingerprint (platform + canonical URL). First submission wins
-- (ON CONFLICT DO NOTHING). A manual submission is a CANDIDATE only — never
-- approval for acquisition, never an analyzed score.

create table if not exists public.loom_manual_submissions (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  profile_id text not null default '',
  platform text not null,
  source_url text not null,
  original_url text not null default '',
  platform_content_id text not null default '',
  content_format text not null,
  time_start_s numeric,
  time_end_s numeric,
  reviewer_note text not null default '',
  submitter text not null default '',
  status text not null default 'accepted',
  duplicate_fingerprint text not null,
  submitted_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint loom_manual_submissions_identity unique (duplicate_fingerprint)
);

create index if not exists idx_loom_ms_tenant on public.loom_manual_submissions (tenant_id);
create index if not exists idx_loom_ms_profile on public.loom_manual_submissions (profile_id);
create index if not exists idx_loom_ms_platform on public.loom_manual_submissions (platform);
create index if not exists idx_loom_ms_status on public.loom_manual_submissions (status);

comment on table public.loom_manual_submissions is
  'Manual Source Intake V1. One row per accepted manual submission, deduplicated by duplicate_fingerprint. status: accepted (youtube/reddit, may enter qualification later) or accepted_pending_platform_support (tiktok/instagram/twitch/kick). A submission is a candidate only — never approval for acquisition, never a fabricated score.';
