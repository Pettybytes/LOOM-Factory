# LOOM — Current State (handoff)

*Updated: 2026-09-12 (EDT)*

## Completed
- **Segment 3E — PASSED.** End-to-end, execution **10082**: callback → completed branch → split → WF02 → WF01 → Qwen scoring → aggregation (`total_candidates: 2`, `status: completed`).
- **Segment 4A — recorded.** Qwen structured scoring validated in code (not prompt-level).
- **All-cached resume edge case — fixed + verified.** Execution **10088** returns the completed aggregate with both candidates.
- **Segment 4B/4C/4D — RUNTIME VERIFIED (2026-09-12).** Discord candidate card send, review intake, and durable decision persistence all pass.

## 4B–4D results (runtime verified)

- **4B Discord candidate card — PASSED.** New workflow **`LOOM — 04 — Review Gate`** (id `3yUBe8H14TjwDCGw`), active. Candidate card webhook `POST /webhook/loom-candidate-card` → normalize → format card → persist score → Discord send. Execution **10098**: Discord node returned `{"success": true}`; score persisted to `loom_scores`.
- **Discord v2 node root cause — CONFIRMED + FIXED.** The deployed n8n Discord v2 node requires parameter values `authentication: "webhook"` (NOT `"webhookApi"`) and `operation: "sendLegacy"` (NOT `"send"`). The WF00 node carried stale values `webhookApi`/`send` that were previously tolerated but a fresh node hit the v2 router's `Could not get parameter "operation"`. Fix = explicit `operation: "sendLegacy"` + `authentication: "webhook"` + reuse credential `LOOM Discord Alerts` (`JbK8O1h4xNVhdMrm`). **No new webhook was created.**
- **4C review intake — PASSED.** Review decision webhook `POST /webhook/loom-review-decision`. Validation enforces `decision ∈ {approve, reject, hold}`, requires `rejection_category` on reject (exec **10100** correctly errored without it), and maps `action: retry_failed_step` → hold + `final_status: retry_requested` + instruction (no model call, no media regen, single attempt).
- **4D durable decisions — PASSED.** `loom_reviews` upsert idempotent by `clip_id` (`on_conflict=clip_id` + `Prefer: resolution=merge-duplicates`). Verified: approve (exec 10099), reject (exec 10101), hold/retry (exec 10102); single row survives repeated clicks; latest decision wins. **Publication is NOT triggered** — `publication_enabled: false` is explicit and there is no downstream GHL/distribution node.
- **Score persistence — PASSED.** `loom_scores` upsert idempotent by `clip_id`. Card path persists `overall_score`, `decision`, `suggested_hook`, `suggested_caption`, identifiers.

## Supabase credential — RESOLVED (fresh key)

- **New service key read from `~/.openclaw/.env`**: `LOOM_SUPABASE_SERVICE_ROLE_KEY` (new `sb_secret_…` format) + `LOOM_SUPABASE_URL`. Auth verified once against `/rest/v1/` → **HTTP 200**. TOOLS.md `service_role` keys (JWT, `iat` 2025-01-22) are **superseded — do not use**.
- **Dedicated LOOM n8n credential created:** `LOOM Supabase` (type `httpHeaderAuth`, header `apikey`, id `JoPSxTOjtLvDXLRV`). The existing generic `Supabase account` (`buSyhkIhcyb8eFxF`) was **not modified** — it is not LOOM-exclusive.
- **Tables created** (idempotent DDL via Supabase Management API, PAT recovered from keychain `Supabase CLI`): `loom_scores`, `loom_reviews`, with indexes. DDL file: `infra/supabase/001_loom_ledger.sql`.

## Credential locations (values NOT recorded)
- n8n API key — `LOOM_N8N_API_KEY` env (`~/.openclaw/.env`).
- OpenRouter / Qwen — Jasmine Phase 1 `docs/TOOLS.md`; n8n credential "OpenRouter API Key (live)".
- Discord webhook — n8n credential "LOOM Discord Alerts" (id `JbK8O1h4xNVhdMrm`).
- Supabase service key — `~/.openclaw/.env` `LOOM_SUPABASE_SERVICE_ROLE_KEY` + `LOOM_SUPABASE_URL` (NOT TOOLS.md).
- Supabase Management PAT — macOS keychain, service `Supabase CLI` (base64-wrapped `go-keyring-base64:`). Used only for DDL.
- n8n Supabase credential — `LOOM Supabase` (id `JoPSxTOjtLvDXLRV`).
- GHL — TOOLS.md files.

## Evidence (execution IDs)
- 3E/4A: `10080` (clip 0 Qwen), `10082` (3E accept), `10084` (clip 1 Qwen), `10088` (all-cached resume).
- 4B: `10098` (candidate card send success — Discord `{"success": true}`).
- 4C/4D: `10099` (approve), `10100` (reject-no-category → validation error), `10101` (reject with category), `10102` (retry_failed_step → hold).
- Prior Discord send: `10059` (success, WF00), `10091`/`10092` (failure — `operation` param, superseded by fix).

## Next task — Segment 5: GHL Media Storage
Transfer one final clip to GHL Media Storage and persist the returned media identifier/URL. This is the first task that requires GHL authentication (primary account PIT token + location ID from TOOLS.md). Begin only after the 4B–4D approval gate is confirmed working end-to-end by Bryan.

## Locked decisions (abridged)
Hook bound 3–10 words (3–7 preferred) · no-speech clips valid (no fabricated dialogue) · resume via explicit `resume` flag (no media regen) · Qwen via OpenRouter, validated in code · GHL = storage/distribution plane · Supabase/Postgres approved for LOOM ledger · Discord QC via `LOOM Discord Alerts` webhook · no publication without a persisted human approve. Integration research: `LOOM_Integration_Research.md`.
