-- LOOM Segment 5A storage-acceptance fixture — register the orphaned GHL upload.
-- Idempotent: repeated runs resolve to the same row (upsert on identity key).
-- This file records a real, verified GHL Media Storage object (not a fabricated record).
--
-- Verified 2026-09-13 (read-only):
--   retrieval HTTP 200, size 13,821,831 bytes (matches GHL size field exactly),
--   sha256 = 2b0b1e2ae88c877148b790b8057ef6adcf1c7fa5162a9bbf61ef876ca0f6dfed.
--
-- Identity note: clip_id uses the same "<uuid>:<index>" form as loom_reviews /
-- CURRENT_STATE.md ("6c46c3ef-...:1"); asset_version "r1" derives from the
-- filename token "r1_0". GHL folder/media IDs are the live values.

insert into public.loom_media (
  tenant_id, clip_id, asset_version, file_hash, size_bytes,
  ghl_location_id, ghl_folder_id, ghl_media_id, ghl_filename, canonical_url,
  storage_status
) values (
  'dreemzy',
  '6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1',
  'r1',
  '2b0b1e2ae88c877148b790b8057ef6adcf1c7fa5162a9bbf61ef876ca0f6dfed',
  13821831,
  'GNhV1OyhFLzcl67XwTBV',
  '6aa6147849f830e49bf3e637',
  '6aa61533c9a6c7937b6a3f6f',
  'dreemzy__6c46c3ef-5eba-40bc-9b50-8f3457d1db79__clip1__r1_0.mp4',
  'https://assets.cdn.filesafe.space/GNhV1OyhFLzcl67XwTBV/media/6fd1f93b-b143-4b0f-abfe-4556b9b29a37.mp4',
  'uploaded'
)
on conflict (tenant_id, clip_id, asset_version) do update set
  file_hash = excluded.file_hash,
  size_bytes = excluded.size_bytes,
  ghl_media_id = excluded.ghl_media_id,
  ghl_filename = excluded.ghl_filename,
  canonical_url = excluded.canonical_url,
  updated_at = now();
