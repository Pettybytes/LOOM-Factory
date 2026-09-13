-- LOOM Segment 5D → 6: loom_reviews reaction-lane identity (asset_version + review_stage).
-- Idempotent: safe to re-run. No secret values.
--
-- Introduces Bryan's two-stage reaction-lane review identity:
--   1. review_stage='moment'            — does the extracted source moment enter the reaction queue?
--   2. review_stage='final_publication' — may a specific finished reaction asset enter GHL/distribution?
--
-- Idempotency moves from clip_id alone to the composite
--   (tenant_id, clip_id, asset_version, review_stage),
-- preserving "first terminal decision wins" per stage.

-- 1) Add columns
alter table public.loom_reviews
  add column if not exists asset_version text,
  add column if not exists review_stage text not null default 'moment';

-- 2) review_stage vocabulary
alter table public.loom_reviews drop constraint if exists loom_reviews_review_stage_check;
alter table public.loom_reviews add constraint loom_reviews_review_stage_check
  check (review_stage in ('moment','final_publication'));

-- 3) Drop the legacy single-column unique on clip_id (replaced by composite identity).
--    Postgres default name for `clip_id text unique` is loom_reviews_clip_id_key.
alter table public.loom_reviews drop constraint if exists loom_reviews_clip_id_key;

--    Robust fallback: drop any remaining UNIQUE constraint defined exactly on clip_id,
--    regardless of its (possibly non-default) name.
do $$
declare c text;
begin
  select conname into c
  from pg_constraint
  where conrelid = 'public.loom_reviews'::regclass
    and contype = 'u'
    and pg_get_constraintdef(oid) = 'UNIQUE (clip_id)';
  if c is not null then
    execute format('alter table public.loom_reviews drop constraint %I', c);
  end if;
end $$;

-- 4) New composite idempotency key (first terminal decision wins per stage).
alter table public.loom_reviews drop constraint if exists loom_reviews_identity;
alter table public.loom_reviews add constraint loom_reviews_identity
  unique (tenant_id, clip_id, asset_version, review_stage);

-- 5) Indexes
create index if not exists idx_loom_reviews_stage on public.loom_reviews (review_stage);
create index if not exists idx_loom_reviews_asset on public.loom_reviews (tenant_id, clip_id, asset_version);

-- 6) Backfill existing rows (idempotent; currently loom_reviews is empty).
--    Pre-005 rows are candidate (moment) decisions. asset_version was not tracked,
--    so use the documented legacy sentinel rather than inventing a value.
update public.loom_reviews
set
  review_stage = 'moment',
  asset_version = 'legacy_unknown'
where review_stage is null
   or asset_version is null
   or asset_version = '';

-- 7) Column documentation
comment on column public.loom_reviews.asset_version is
  'Asset/render version the decision applies to (e.g. render-1.0). ''legacy_unknown'' = pre-005 row with no proven version.';
comment on column public.loom_reviews.review_stage is
  'Reaction-lane stage: ''moment'' (enter reaction queue) or ''final_publication'' (GHL/distribution eligibility).';

-- ---------------------------------------------------------------------------
-- Reaction-lane rules (documented policy; enforced by application code):
--   * A moment approval MUST NOT populate loom_media.publication_review_id,
--     set publication_status='approved', authorize GHL publication storage,
--     or authorize scheduling/publication.
--   * Only an approved review with review_stage='final_publication' AND matching
--     tenant_id + clip_id + asset_version may populate publication_review_id /
--     publication_approved_at or transition publication_status to 'approved'.
--   * First terminal decision wins per (tenant_id, clip_id, asset_version, review_stage).
-- ---------------------------------------------------------------------------
