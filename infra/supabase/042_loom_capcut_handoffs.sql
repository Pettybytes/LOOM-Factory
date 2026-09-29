-- LOOM 042 — durable provenance for a successful "Finish in CapCut" handoff.
-- Idempotent: safe to re-run. No secret values.
--
-- PURPOSE (provenance only): preserve the AUTHORITATIVE BEFORE state of one
-- handoff, so a later BEFORE/AFTER comparison needs no archaeology. A handoff
-- hands the operator's CURRENT version to CapCut as an editable project; this
-- row records exactly what was handed over and where it came from.
--
-- This is NOT a learning system and NOT a feedback loop. There is deliberately
-- no return/diff/learning table here, and no speculative columns: a row exists
-- so a future design can diff from a known-good baseline, nothing more.
--
-- ONE row per SUCCESSFUL handoff, append-only: `handoff_id` is the durable
-- identity and rows are never updated. A handoff that FAILED writes NO row —
-- there is no success record without a real generated CapCut project.
--
-- The record has NO effect on candidate state, approval state, rendering,
-- acquisition, Content Intelligence, publishing, SourceWindow, GHL routing or
-- normal editor operation: nothing reads it on any operational path.

create table if not exists public.loom_capcut_handoffs (
  handoff_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  -- The review candidate this version came from (the durable candidate key).
  -- Null when a version predates the candidate/fingerprint model.
  candidate_id text,
  -- LOOM asset identity: the clip and the exact version that was exported.
  clip_id text not null,
  asset_version text not null,
  -- Correction/version identity "where applicable": the version the correction
  -- was applied TO, and the correction row itself (null for a version that was
  -- never produced by an edit, e.g. the original render).
  source_asset_version text,
  correction_id uuid,
  -- Working-media identity: the sha256 of the exact WORKING asset CapCut was
  -- given. Content-addressed, so the same bytes are recognisable later.
  media_sha256 text not null,
  -- The ORIGINAL-source time that corresponds to working t=0 (0 for a full
  -- item). Recorded because it is the only way to interpret a working-timebase
  -- range against the source without re-deriving it.
  working_timebase_origin_s numeric not null default 0,
  -- The IMMUTABLE initial CapCut handoff specification, exactly as compiled.
  -- It carries the draft name and the working-coordinate trim, so subsequent
  -- CapCut edits can be compared against it.
  spec jsonb not null,
  -- Donor/compiler identity ({"name","version","commit","url"}) so the spec can
  -- be interpreted deterministically against the pinned compiler that made it.
  compiler jsonb not null,
  created_at timestamptz not null default now()
);

comment on table public.loom_capcut_handoffs is
  'Immutable provenance for a SUCCESSFUL LOOM -> CapCut handoff: the BEFORE state (exact compiled spec + working-media hash + coordinate origin + compiler identity). Provenance only; no operational path reads it. Handoffs that failed record nothing.';

comment on column public.loom_capcut_handoffs.handoff_id is
  'Durable identity of one successful handoff. Append-only; never updated.';

comment on column public.loom_capcut_handoffs.media_sha256 is
  'sha256 of the WORKING asset handed to CapCut (the bytes, not the container path) so the exported media is identifiable later.';

comment on column public.loom_capcut_handoffs.working_timebase_origin_s is
  'ORIGINAL source seconds corresponding to working t=0. 0 for a full-item asset.';

comment on column public.loom_capcut_handoffs.spec is
  'The immutable initial handoff specification as compiled (seconds, not microseconds; working coordinates only).';

create index if not exists idx_loom_capcut_handoffs_asset
  on public.loom_capcut_handoffs (tenant_id, clip_id, asset_version);

create index if not exists idx_loom_capcut_handoffs_created
  on public.loom_capcut_handoffs (tenant_id, created_at desc);
