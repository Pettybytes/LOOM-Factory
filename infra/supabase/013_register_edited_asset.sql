-- LOOM Stage 2 — Editor (Discord-centered OpenShorts): atomic, immutable
-- version registration (Step 4). Idempotent: safe to re-run. No secret values.
--
-- One transactional RPC registers an edited render as a NEW asset_version:
--   * never overwrites the source asset or mutates its approval;
--   * allocates the next version deterministically (max minor + 1) under an
--     advisory lock, so two edit sessions cannot choose the same version;
--   * writes the lineage row (previous_asset_version) + immutable correction log
--     + marks the edit session completed — all in one transaction;
--   * idempotent: a repeated completion returns the existing result, never a
--     second version.
--
-- The edited render is NOT the canonical approved GHL master: publication_status
-- is set to 'final_review_pending'; only a later final_publication approve may
-- advance it toward approved storage/distribution.

create or replace function public.register_edited_asset(
  p_edit_session_id uuid,
  p_content_type text,
  p_file_hash text,
  p_size_bytes bigint,
  p_trim_start numeric,
  p_trim_end numeric,
  p_caption_changes jsonb,
  p_crop_reframe_changes jsonb,
  p_instruction text,
  p_llm_assist_used boolean,
  p_edl_metadata jsonb,
  p_ghl_location_id text default null,
  p_ghl_folder_id text default null,
  p_ghl_media_id text default null,
  p_ghl_filename text default null,
  p_canonical_url text default null
)
returns jsonb
language plpgsql
security invoker
as $$
declare
  v_tenant text;
  v_clip text;
  v_source text;
  v_reviewer text;
  v_status text;
  v_target text;
  v_major int;
  v_minor int;
  v_new_version text;
  v_new_media_id uuid;
  v_correction_id uuid;
begin
  -- Read identity from the durable session (source of truth).
  select tenant_id, clip_id, source_asset_version, reviewer, status, target_asset_version
    into v_tenant, v_clip, v_source, v_reviewer, v_status, v_target
    from public.loom_edit_sessions
    where id = p_edit_session_id;
  if v_tenant is null then
    raise exception 'edit session not found';
  end if;

  -- Serialize concurrent completions on the same clip (version allocation).
  perform pg_advisory_xact_lock(hashtext('loom_edit:' || v_tenant || ':' || v_clip));

  -- Re-check idempotency under the lock (a competing completion already won).
  select status, target_asset_version into v_status, v_target
    from public.loom_edit_sessions where id = p_edit_session_id;

  if v_status = 'completed' and v_target is not null then
    return jsonb_build_object(
      'idempotent', true,
      'tenant_id', v_tenant,
      'clip_id', v_clip,
      'asset_version', v_target
    );
  end if;

  if v_status <> 'active' then
    raise exception 'edit session is not active (status %)', v_status;
  end if;

  -- Deterministic next version: same major as source, max existing minor + 1.
  v_major := coalesce((split_part(split_part(v_source, '-', 2), '.', 1))::int, 1);
  if v_major < 1 then v_major := 1; end if;
  select coalesce(max((split_part(asset_version, '.', 2))::int), -1) into v_minor
    from public.loom_media
    where tenant_id = v_tenant and clip_id = v_clip;
  v_new_version := 'render-' || v_major || '.' || (v_minor + 1);

  -- New lineage row (final_review_pending; never approved; never overwrites source).
  insert into public.loom_media (
    tenant_id, clip_id, asset_version, previous_asset_version,
    publication_status, content_type, storage_status,
    file_hash, size_bytes, ghl_location_id, ghl_folder_id,
    ghl_media_id, ghl_filename, canonical_url
  ) values (
    v_tenant, v_clip, v_new_version, v_source,
    'final_review_pending', p_content_type, 'uploaded',
    p_file_hash, p_size_bytes, p_ghl_location_id, p_ghl_folder_id,
    p_ghl_media_id, p_ghl_filename, p_canonical_url
  )
  returning id into v_new_media_id;

  -- Immutable correction log.
  insert into public.loom_edit_corrections (
    edit_session_id, tenant_id, clip_id, source_asset_version, target_asset_version,
    reviewer, trim_start, trim_end, caption_changes, crop_reframe_changes,
    written_instruction, llm_assist_used, edl_metadata, render_status
  ) values (
    p_edit_session_id, v_tenant, v_clip, v_source, v_new_version,
    v_reviewer, p_trim_start, p_trim_end, p_caption_changes, p_crop_reframe_changes,
    p_instruction, p_llm_assist_used, p_edl_metadata, 'success'
  )
  returning id into v_correction_id;

  -- Mark the session completed only after registration succeeds (same txn).
  update public.loom_edit_sessions
    set status = 'completed', target_asset_version = v_new_version, updated_at = now()
    where id = p_edit_session_id;

  return jsonb_build_object(
    'idempotent', false,
    'tenant_id', v_tenant,
    'clip_id', v_clip,
    'source_asset_version', v_source,
    'asset_version', v_new_version,
    'media_id', v_new_media_id,
    'correction_id', v_correction_id,
    'reviewer', v_reviewer
  );
end;
$$;

-- Security: this RPC is server-side ONLY. The browser/Discord Activity NEVER
-- calls it directly — it calls the adapter's /discord/editor/complete, which
-- validates the active session and invokes the RPC with the service_role key.
-- PUBLIC and anon are explicitly revoked; only service_role may execute.
revoke execute on function public.register_edited_asset(
  uuid, text, text, bigint, numeric, numeric, jsonb, jsonb, text, boolean, jsonb,
  text, text, text, text, text
) from public, anon;

grant execute on function public.register_edited_asset(
  uuid, text, text, bigint, numeric, numeric, jsonb, jsonb, text, boolean, jsonb,
  text, text, text, text, text
) to service_role;

comment on function public.register_edited_asset(
  uuid, text, text, bigint, numeric, numeric, jsonb, jsonb, text, boolean, jsonb,
  text, text, text, text, text
) is
  'Atomic, idempotent edited-asset registration (Stage 2 Editor Step 4, server-side only). Allocates the next asset_version deterministically, writes lineage + immutable correction log, and marks the edit session completed in one transaction. The new asset is final_review_pending — never approved, never promoted, never distributed. Execute is restricted to service_role; the browser/Activity never calls this directly.';
