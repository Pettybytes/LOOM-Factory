# LOOM — Discord-Centered OpenShorts MVP Editor: Implementation Proposal

**Status:** APPROVED for implementation (2026-09-13). This is the schema +
exact-files plan. Begin with this, then proceed.

**Correction to prior inspection:** OpenShorts has **no manual timeline editor**
today. Its "editor" is LLM-driven (`VideoEditor` → Gemini → FFmpeg filter/EDL).
A lightweight manual correction interface must be **added**, reusing the existing
transcript/scenes/EDL/Remotion-preview/reframe/subtitle/rerender infrastructure.

**Launch model:** browser deep-link is the development scaffold + secure fallback.
The final Stage 1 acceptance endpoint is a Discord Activity/embedded experience
(feasibility previously verified: the dashboard is an embeddable React SPA; the
only blocker is `X-Frame-Options: SAMEORIGIN` on the Activity route).

---

## 1. Schema — migration `012_loom_edit_sessions.sql` (idempotent, no secrets)

Three additions:

1. **`loom_edit_sessions`** — one row per editor launch (Discord "Edit" click).
   Holds `tenant_id`, `clip_id`, `source_asset_version`, `target_asset_version`,
   `reviewer`, `token_hash` (SHA-256 only), `status`
   (`active|completed|expired|canceled|failed`), `expires_at`, `consumed_at`.
   Single-use + 15-minute expiry + replay protection.
2. **`loom_edit_corrections`** — immutable correction log. Holds the full change
   record: `trim_start`/`trim_end`, `caption_changes` (jsonb),
   `crop_reframe_changes` (jsonb), `written_instruction`, `llm_assist_used`,
   `edl_metadata` (jsonb), `render_status`, plus tenant/clip/source+target
   versions/reviewer. New `asset_version` is registered **only** on `success`.
3. **`loom_media.previous_asset_version`** — lineage chain. NULL = original.

**Deterministic asset_version:** `render-<major>.0` → edit → `render-<major>.1` →
`render-<major>.2` (minor increment). The new row's `previous_asset_version`
points at the version it was edited from. Original + full review history preserved.

> ⚠️ DDL is not reachable via PostgREST — migration 012 must be applied in the
> Supabase SQL Editor (same as 010/011), before the adapter/editor run.

---

## 2. Exact files to change

| # | File | Change |
|---|---|---|
| 1 | `infra/supabase/012_loom_edit_sessions.sql` | **NEW** — sessions + corrections + lineage |
| 2 | `infra/discord-review/adapter.py` | Add `Edit` (4th) button + `edit` decision + `register_editor_token` (single-use 15-min, hash-only) + `_handle_edit` launch |
| 3 | `openshorts/dashboard/src/components/ManualTrimEditor.jsx` | **NEW** — manual trim/reframe/caption/instruction editor |
| 4 | `openshorts/dashboard/src/lib/api.js` | editor-token validation + rerender + version-registration calls |
| 5 | `openshorts/dashboard/src/` (route/entry) | deep-link entry (`/edit?job_id=&clip_index=&token=`) |
| 6 | `openshorts/dashboard/nginx.conf` | scoped `frame-ancestors https://discord.com` on the Activity route only (no arbitrary origins) |
| 7 | LOOM version-registration (Supabase RPC or n8n workflow) | new `asset_version` + correction log + `final_review_pending` + new `final_publication` card |
| 8 | `CURRENT_STATE.md` + authority files | status updates (this segment) |

> Secrets stay out of `custom_id`/URL/workflow JSON. Editor token is short-lived
> (15 min) + single-use; only its hash is stored. No paid recurring service.

---

## 3. Implementation sequence (matches the approved spec)

1. ✅ **Schema proposal + migration 012** (this doc + SQL).
2. **Discord `Edit` launch** — 4th button, token mint/validation (adapter.py).
3. **Manual editor** — trim/reframe/caption/instruction UI reusing OpenShorts.
4. **Immutable rerender/version registration** — new `asset_version`, never overwrite.
5. **Return to `final_publication` review** — new card for the new version.
6. **Discord Activity embed** — CSP `frame-ancestors`, Embedded App SDK + OAuth.
7. **Bounded acceptance test** — non-production fixture first.

---

## 4. Security constraints (enforced, not aspirational)

- Tenant isolation: editor resolves and enforces `tenant_id`; one tenant edits only its own clip.
- Single-use 15-min editor token; only SHA-256 hash stored; expiry + replay protection.
- No secrets/unrestricted access in Discord `custom_id` or URL.
- Edits always mint a **new** `asset_version`; a preview/proxy/temporary render is never promoted to the canonical approved master.
- The edited asset still flows through the existing distribution guard (MIME G13 + publication review) before scheduling — gates unchanged.
- Scoped `frame-ancestors` allowlist (Discord only), never `*`.
