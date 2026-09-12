# LOOM — Current State (handoff)

*Updated: 2026-09-12 (EDT)*

## Completed
- **Segment 3E — PASSED.** End-to-end, execution **10082**: callback → completed branch → split → WF02 → WF01 → Qwen scoring → aggregation (`total_candidates: 2`, `status: completed`).
- **Segment 4A — recorded.** Qwen structured scoring validated in code (not prompt-level).
- **All-cached resume edge case — fixed + verified.** Execution **10088** returns the completed aggregate with both candidates.
- **Segment 4B/4C/4D — RUNTIME VERIFIED (2026-09-12).** Discord candidate card send, review intake, and durable decision persistence all pass.

## 4B–4D results (runtime verified)

- **4B Discord candidate card — PASSED.** New workflow **`LOOM — 04 — Review Gate`** (id `3yUBe8H14TjwDCGw`), active. Candidate card webhook `POST /webhook/loom-candidate-card` → normalize → format card → persist score → Discord send. Execution **10105**: Discord returned `{"success": true}`; score persisted to `loom_scores`.
- **Discord v2 node root cause — CONFIRMED + FIXED.** The deployed n8n Discord v2 node requires `authentication: "webhook"` (NOT `"webhookApi"`) and `operation: "sendLegacy"` (NOT `"send"`). WF00 still carries stale values. Fix = explicit `sendLegacy` + `webhook` + reuse credential `LOOM Discord Alerts` (`JbK8O1h4xNVhdMrm`). No new webhook.
- **4C review intake — PASSED.** `POST /webhook/loom-review-decision`. Validation enforces `decision ∈ {approve, reject, hold}`, requires `rejection_category` on reject, maps `action: retry_failed_step` → hold + `retry_requested`.
- **4D durable decisions — PASSED.** `loom_reviews` upsert idempotent by `clip_id`. Single row survives repeated clicks; latest decision wins. Publication NOT triggered (`publication_enabled: false`; no downstream GHL node).
- **Score persistence — PASSED.** `loom_scores` upsert by `clip_id`.

## Reviewer experience — gaps found + fixed (2026-09-12)

- **Preview placeholder → real final clip.** Clip `126d1217-…:0` (repairable, overall 81, hook "Walk like a big dog, but you a small cat.") final render is served at `http://localhost:8000/videos/…/hooked_…_clip_1.mp4` (18 MB, HTTP 200). This is the working final-clip preview. The public Tailscale Funnel URL is Bearer-auth-gated (401) and media is locked local per SOURCE_OF_TRUTH, so `localhost:8000` is the correct preview link for Bryan's Mac.
- **Discordbot auto-fire bug — FOUND + FIXED.** A GET-based `loom-review-action` endpoint (added to make "clickable links") was auto-fetched by `Discordbot/2.0` link-preview on card send, silently persisting `hold`→`reject`→`approve` with no human click (execs 10106–10108). **Removed.** Review decisions are POST-only via `loom-review-decision`. Lesson: never expose a state-mutating GET endpoint in a Discord message — Discord's link-preview crawler will fire it.
- **Candidate record kept for Bryan's test.** `loom_scores` row for `126d1217-…:0` retained (overall 81, repairable); `loom_reviews` empty so Bryan can make the first real decision. Publication disabled.

## Two genuine credential needs (BLOCKERS for full reviewer UX)

Neither is self-servable by Bentley (requires Bryan's Discord admin):

1. **`#clip-review` Discord webhook URL.** Only `LOOM Discord Alerts` (→ #factory-alerts) exists. The candidate card currently routes there because it is the only available webhook. To send review cards to `#clip-review`, Bryan must create a webhook in that channel and provide its URL (Bentley will store it as a dedicated `LOOM Clip Review` credential — do NOT repurpose `LOOM Discord Alerts`, which must stay the failure-alert destination).
2. **Discord bot token (for interactive Approve/Reject/Hold buttons).** A webhook can send messages but cannot receive button clicks/interactions. Real clickable review buttons require a Discord bot token + interaction endpoint. Without it, review controls are limited to the POST decision webhook (which works but is not a Discord-native button).

## Supabase credential — RESOLVED (fresh key)

- Service key from `~/.openclaw/.env` (`LOOM_SUPABASE_SERVICE_ROLE_KEY`, new `sb_secret_…` format) + `LOOM_SUPABASE_URL`. Auth verified HTTP 200. TOOLS.md JWT keys are superseded — do not use.
- Dedicated n8n credential `LOOM Supabase` (`httpHeaderAuth`, header `apikey`, id `JoPSxTOjtLvDXLRV`). Existing `Supabase account` (`buSyhkIhcyb8eFxF`) untouched (not LOOM-exclusive).
- Tables `loom_scores` + `loom_reviews` created (idempotent DDL: `infra/supabase/001_loom_ledger.sql`).

## Credential locations (values NOT recorded)
- n8n API key — `LOOM_N8N_API_KEY` env (`~/.openclaw/.env`).
- OpenRouter / Qwen — Jasmine Phase 1 `docs/TOOLS.md`; n8n credential "OpenRouter API Key (live)".
- Discord webhook — n8n credential "LOOM Discord Alerts" (`JbK8O1h4xNVhdMrm`) → #factory-alerts (failures only).
- Supabase service key — `~/.openclaw/.env` `LOOM_SUPABASE_SERVICE_ROLE_KEY` + `LOOM_SUPABASE_URL`.
- Supabase Management PAT — macOS keychain `Supabase CLI` (base64-wrapped `go-keyring-base64:`).
- n8n Supabase credential — `LOOM Supabase` (`JoPSxTOjtLvDXLRV`).
- GHL — TOOLS.md files.

## Evidence (execution IDs)
- 3E/4A: `10080`, `10082`, `10084`, `10088`.
- 4B: `10098` (example.com card), `10105` (real-preview card, Discord `{"success":true}`).
- 4C/4D: `10099` (approve), `10100` (reject-no-category → error), `10101` (reject+category), `10102` (retry).
- Discordbot auto-fire (bug, now removed): `10106`–`10108`.

## Next task — Segment 5: GHL Media Storage
Transfer one final clip to GHL Media Storage and persist the returned media identifier/URL. First task requiring GHL auth (primary PIT token + location ID). Begin only after the 4B–4D approval gate is confirmed working end-to-end by Bryan, and after the #clip-review webhook + bot-token credentials (above) are provided if native buttons are wanted.

## Locked decisions (abridged)
Hook bound 3–10 words (3–7 preferred) · no-speech clips valid (no fabricated dialogue) · resume via explicit `resume` flag (no media regen) · Qwen via OpenRouter, validated in code · GHL = storage/distribution plane · Supabase/Postgres approved for LOOM ledger · Discord QC via webhook (buttons need bot token) · no publication without a persisted human approve. Integration research: `LOOM_Integration_Research.md`.
