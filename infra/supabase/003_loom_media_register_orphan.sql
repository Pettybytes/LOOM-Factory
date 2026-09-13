-- LOOM Segment 5A storage-acceptance fixture — register the orphaned GHL upload.
-- Idempotent: repeated runs resolve to the same row (upsert on identity key).
-- This file records a real, verified GHL Media Storage object (not a fabricated record).
--
-- Identity resolution (verified 2026-09-13):
--   LOOM candidate identity is ZERO-BASED: clip_id = "<uuid>:<clip_index>".
--   clip_index=0 -> OpenShorts "clip_1" (first short); clip_index=1 -> "clip_2" (second short).
--   This asset: clip_id = 6c46c3ef-…:1  =>  clip_index=1, human/display ordinal=2.
--   Authoritative OpenShorts render: subtitled_…_clip_2.mp4 ("I'm clownin'" candidate).
--   The GHL filename token "clip1" is the zero-based clip_index=1 (technically consistent).
--
-- Content match (verified, read-only): size 13,821,831 bytes and sha256
--   2b0b1e2ae88c877148b790b8057ef6adcf1c7fa5162a9bbf61ef876ca0f6dfed
--   are byte-identical to openshorts/output/6c46c3ef-…/subtitled_…_clip_2.mp4.
--
-- storage_status='fixture' = storage-acceptance only, NOT publication-approved.

insert into public.loom_media (
  tenant_id, clip_id, asset_version, file_hash, size_bytes,
  ghl_location_id, ghl_folder_id, ghl_media_id, ghl_filename, canonical_url,
  storage_status
) values (
  'dreemzy',
  '6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1',
  'render-1.0',
  '2b0b1e2ae88c877148b790b8057ef6adcf1c7fa5162a9bbf61ef876ca0f6dfed',
  13821831,
  'GNhV1OyhFLzcl67XwTBV',
  '6aa6147849f830e49bf3e637',
  '6aa61533c9a6c7937b6a3f6f',
  'dreemzy__6c46c3ef-5eba-40bc-9b50-8f3457d1db79__clip1__r1_0.mp4',
  'https://assets.cdn.filesafe.space/GNhV1OyhFLzcl67XwTBV/media/6fd1f93b-b143-4b0f-abfe-4556b9b29a37.mp4',
  'fixture'
)
on conflict (tenant_id, clip_id, asset_version) do update set
  file_hash = excluded.file_hash,
  size_bytes = excluded.size_bytes,
  ghl_media_id = excluded.ghl_media_id,
  ghl_filename = excluded.ghl_filename,
  canonical_url = excluded.canonical_url,
  storage_status = excluded.storage_status,
  updated_at = now();
