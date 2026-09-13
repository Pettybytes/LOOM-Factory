-- LOOM Segment 7 (Discord-centered editor): edit sessions, correction log,
-- and asset-version lineage. Idempotent: safe to re-run. No secret values.
--
-- The editor is a lightweight MANUAL correction interface (trim/reframe/caption),
-- NOT a full nonlinear editor. OpenShorts has no manual timeline editor today;
-- this schema records the durable side of a manual correction: a single-use
-- editor session (token hash only) and an immutable correction log, so a new
-- asset_version can be produced without ever overwriting the source.

-- ---------------------------------------------------------------------------
-- 1) Edit sessions — one row per editor launch (Discord "Edit" click).
--    Only the token HASH is stored; the raw token is short-lived (15 min) and
--    single-use. Full identity (tenant/clip/asset_version) is resolved here,
--    never trusted from the client or embedded in the Discord custom_id/URL.
-- ---------------------------------------------------------------------------
create table if not exists public.loom_edit_sessions (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  clip_id text not null,
  source_asset_version text not null,
  target_asset_version text,
  reviewer text,
  token_hash text not null,
  status text not null default 'active'
    check (status in ('active','completed','expired','canceled','failed')),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_loom_edit_sessions_tenant
  on public.loom_edit_sessions (tenant_id, clip_id);
create index if not exists idx_loom_edit_sessions_token_hash
  on public.loom_edit_sessions (token_hash);

comment on column public.loom_edit_sessions.token_hash is
  'SHA-256 of the single-use editor token (raw token never stored).';
comment on column public.loom_edit_sessions.source_asset_version is
  'The asset_version being edited. Never overwritten; the edit produces a NEW target_asset_version.';
comment on column public.loom_edit_sessions.status is
  'active | completed | expired | canceled | failed. consumed_at enforces single-use replay protection.';

-- ---------------------------------------------------------------------------
-- 2) Correction log — immutable record of what an edit changed, plus the
--    resulting EDL/render metadata and render status. One row per completed
--    edit (or per failed attempt, for the audit trail).
-- ---------------------------------------------------------------------------
create table if not exists public.loom_edit_corrections (
  id uuid primary key default gen_random_uuid(),
  edit_session_id uuid references public.loom_edit_sessions(id) on delete set null,
  tenant_id text not null,
  clip_id text not null,
  source_asset_version text not null,
  target_asset_version text not null,
  reviewer text,
  trim_start numeric,
  trim_end numeric,
  caption_changes jsonb,
  crop_reframe_changes jsonb,
  written_instruction text,
  llm_assist_used boolean not null default false,
  edl_metadata jsonb,
  render_status text not null default 'pending'
    check (render_status in ('pending','rendering','success','failed')),
  created_at timestamptz not null default now()
);

create index if not exists idx_loom_edit_corrections_asset
  on public.loom_edit_corrections (tenant_id, clip_id, target_asset_version);

comment on column public.loom_edit_corrections.trim_start is
  'New trim start (seconds). Null = unchanged.';
comment on column public.loom_edit_corrections.llm_assist_used is
  'True only when the optional written instruction triggered a Gemini/FFmpeg LLM edit (bounded cost).';
comment on column public.loom_edit_corrections.render_status is
  'pending | rendering | success | failed. The new asset_version is only registered on success.';

-- ---------------------------------------------------------------------------
-- 3) Asset-version lineage — previous_asset_version on loom_media, so any
--    edited asset is traceable to its source without re-reading the log.
-- ---------------------------------------------------------------------------
alter table public.loom_media
  add column if not exists previous_asset_version text;

comment on column public.loom_media.previous_asset_version is
  'Source asset_version this asset was edited/derived from. NULL = original render (no lineage). Never used to overwrite; edits always mint a new asset_version.';

-- ---------------------------------------------------------------------------
-- Deterministic asset_version scheme (documented policy, enforced in app code):
--   Original render  -> render-<major>.0
--   First edit       -> render-<major>.1 (minor increment, deterministic from
--                       the source version — not random, not content-addressed)
--   Subsequent edits -> render-<major>.<minor+1>
-- The new version carries previous_asset_version = the version it was edited
-- from, so lineage is a chain. The original + full review history is preserved.
-- ---------------------------------------------------------------------------
