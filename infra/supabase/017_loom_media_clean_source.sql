-- LOOM Stage 2 — Editor: durable clean pre-hook/pre-subtitle input source.
-- Idempotent: safe to re-run. No secret values.
--
-- Problem (2026-09-15 manual-acceptance blocker): hook replacement must re-render
-- from the CLEAN pre-hook/pre-subtitle master, never stack a new hook on the
-- already-burned derivative. The clean master existed only transiently on the
-- OpenShorts backend disk (`<base>_clip_<n>.mp4`), so once the render job was
-- evicted the editor could no longer resolve a clean source (clean_source_url was
-- null) and hook replacement against a fully-evicted job was impossible.
--
-- Fix: persist the clean input as an immutable, tenant-scoped artifact on the
-- ORIGINAL render's loom_media row (the row whose previous_asset_version IS NULL).
-- The clean source URL is captured at ingest time and never overwritten; edits
-- always mint a NEW asset_version (register_edited_asset), leaving the original
-- render + its clean source intact.

-- ---------------------------------------------------------------------------
-- 1) Clean-source columns (idempotent)
-- ---------------------------------------------------------------------------
alter table public.loom_media
  add column if not exists clean_source_url text,
  add column if not exists clean_source_hash text,
  add column if not exists clean_source_size_bytes bigint,
  add column if not exists clean_source_filename text;

comment on column public.loom_media.clean_source_url is
  'Durable, reachable URL of the clean pre-hook/pre-subtitle input master (the "<base>_clip_<n>.mp4" reframe before any hook or caption burn). Captured at ingest time; never overwritten. NULL = no durable clean source (replacement unavailable).';
comment on column public.loom_media.clean_source_hash is
  'SHA-256 of the clean source file (identity proof). NULL = unverified.';
comment on column public.loom_media.clean_source_size_bytes is
  'Byte size of the clean source file (identity proof). NULL = unverified.';
comment on column public.loom_media.clean_source_filename is
  'Authoritative OpenShorts clean reframe filename (base_name_clip_<n>.mp4) this clean source was produced from.';

-- ---------------------------------------------------------------------------
-- 2) NO backfill of the existing fixture
-- ---------------------------------------------------------------------------
-- Deliberately do NOT backfill the existing fixture
--   (tenant_id='dreemzy', clip_id='6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1',
--    asset_version='render-1.0').
-- Its clean source (`DeenTheGreat_&_Adrien_Broner_-_WHO_TF_IS_DDG_(Official_Music_Video)_Powered_By_Zeus_Network_clip_2.mp4`)
-- was never durably stored — no URL, no hash, no size was ever recorded — and the
-- OpenShorts job that produced it is evicted (backend returns "Job not found"; the
-- output/ dir is empty). Its file identity therefore CANNOT be proven, and a
-- guessed/fabricated clean_source_url would be a placeholder in production (violates
-- the ironclad no-placeholder rule). The existing fixture is instead left with
-- clean_source_url NULL, so the bootstrap honestly reports replacement-unavailable.
--
-- A fresh fixture with a PROVEN clean source is registered separately (see the
-- loom_media registration for tenant 'dreemzy' clip 'loom-clean-fixture:*').
