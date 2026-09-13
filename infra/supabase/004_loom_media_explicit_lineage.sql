-- LOOM Segment 5D → 6 pre-distribution migration: explicit lineage + publication eligibility.
-- Idempotent: safe to re-run. No secret values.
--
-- Adds the smallest fields needed to make asset identity and publication
-- eligibility queryable. Approval must NEVER be inferred from storage presence,
-- fixture status, a moment/source approval, a Discord card, or a successful upload.

-- ---------------------------------------------------------------------------
-- 1) Add columns (idempotent)
-- ---------------------------------------------------------------------------
alter table public.loom_media
  add column if not exists clip_index integer,
  add column if not exists human_ordinal integer,
  add column if not exists source_filename text,
  add column if not exists publication_status text not null default 'not_approved',
  add column if not exists publication_review_id uuid,
  add column if not exists publication_approved_at timestamptz;

-- ---------------------------------------------------------------------------
-- 2) Controlled publication-status vocabulary
-- ---------------------------------------------------------------------------
alter table public.loom_media drop constraint if exists loom_media_publication_status_check;
alter table public.loom_media add constraint loom_media_publication_status_check
  check (publication_status in ('not_approved','final_review_pending','approved','blocked','published'));

-- ---------------------------------------------------------------------------
-- 3) human_ordinal is derived from clip_index (+1) when both are set
-- ---------------------------------------------------------------------------
alter table public.loom_media drop constraint if exists loom_media_human_ordinal_check;
alter table public.loom_media add constraint loom_media_human_ordinal_check
  check (human_ordinal is null or clip_index is null or human_ordinal = clip_index + 1);

-- ---------------------------------------------------------------------------
-- 4) publication_review_id references the durable loom_reviews decision.
--    Hard FK with ON DELETE SET NULL so review cleanup does not block/orphan.
-- ---------------------------------------------------------------------------
alter table public.loom_media drop constraint if exists loom_media_publication_review_fk;
alter table public.loom_media add constraint loom_media_publication_review_fk
  foreign key (publication_review_id) references public.loom_reviews(id) on delete set null;

-- ---------------------------------------------------------------------------
-- 5) Backfill the existing fixture (idempotent; targets only the fixture row)
-- ---------------------------------------------------------------------------
update public.loom_media
set
  clip_index = 1,
  human_ordinal = 2,
  source_filename = 'subtitled_1789264697_hooked_1789264688_DeenTheGreat_&_Adrien_Broner_-_WHO_TF_IS_DDG_(Official_Music_Video)_Powered_By_Zeus_Network_clip_2.mp4',
  publication_status = 'not_approved'
where tenant_id = 'dreemzy'
  and clip_id = '6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1'
  and asset_version = 'render-1.0';

-- ---------------------------------------------------------------------------
-- Column documentation
-- ---------------------------------------------------------------------------
comment on column public.loom_media.clip_index is
  'Zero-based candidate index (clip_id "<uuid>:<index>").';
comment on column public.loom_media.human_ordinal is
  'Human/display ordinal = clip_index + 1 (enforced by CHECK).';
comment on column public.loom_media.source_filename is
  'Authoritative OpenShorts render filename this asset was produced from.';
comment on column public.loom_media.publication_status is
  'Publication eligibility state. Controlled values: not_approved, final_review_pending, approved, blocked, published.';
comment on column public.loom_media.publication_review_id is
  'References loom_reviews(id): the durable final-review decision that approved publication.';
comment on column public.loom_media.publication_approved_at is
  'Timestamp the final-review approval was recorded. Null until approved.';

-- ---------------------------------------------------------------------------
-- Segment 6 distribution guard (documented policy; enforced by application code)
--
-- An asset is eligible for scheduling ONLY when ALL of the following hold:
--   1. storage verification succeeded (retrieval OK + size/hash match);
--   2. publication_status = 'approved';
--   3. a durable final-review decision exists (publication_review_id) and
--      belongs to the same tenant + clip (+ asset version where tracked);
--   4. the asset is not a fixture (storage_status <> 'fixture');
--   5. no prior scheduling/publication receipt exists for that
--      tenant/platform/asset version.
--
-- For Bryan's reaction lane: moment approval NEVER satisfies this guard.
-- ---------------------------------------------------------------------------
