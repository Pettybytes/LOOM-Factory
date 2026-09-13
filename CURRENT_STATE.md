# LOOM — Current State (handoff)

*Updated: 2026-09-12 (EDT)*

## Completed
- **Segment 3E — PASSED** (runtime verified, exec `10082`): callback → completed branch → split → WF02 → WF01 → Qwen scoring → aggregation (`total_candidates: 2`, `status: completed`).
- **Segment 4A — recorded.** Qwen structured scoring validated in code (not prompt-level).
- **All-cached resume edge case — fixed + verified** (exec `10088`).
- **Segment 4B/4C/4D — RUNTIME VERIFIED (2026-09-12).** Discord candidate card send, review intake, and durable decision persistence all pass.
- **Card routing to #clip-review — COMPLETE + COMMITTED.** Candidate card posts via the `Loom Clip Review` webhook credential; `LOOM Discord Alerts` remains the failure-alert destination (unchanged).

## 4B–4D results (runtime verified)
- **4B Discord card** — workflow `LOOM — 04 — Review Gate` (`3yUBe8H14TjwDCGw`), active. `POST /webhook/loom-candidate-card` → normalize → format → persist score → Discord send (exec `10105`: `{"success": true}`).
- **Discord v2 node fix** — requires `authentication: "webhook"` + `operation: "sendLegacy"` (NOT `webhookApi`/`send`).
- **4C review intake** — `POST /webhook/loom-review-decision`. Validates `decision ∈ {approve, reject, hold}`, requires `rejection_category` on reject, maps `retry_failed_step` → hold + `retry_requested`.
- **4D durable decisions** — `loom_reviews` upsert idempotent by `clip_id`. Publication NOT triggered (`publication_enabled: false`).
- **Score persistence** — `loom_scores` upsert by `clip_id`.

## Pending
- **Native Discord interaction implementation (buttons) — PENDING.** Requires Ed25519 signature verification + native `custom_id` buttons + interactions endpoint. Not yet built.
- **Caption / final-asset correction — PENDING.** The previous review card used a `hooked_` derivative (audible speech + hook overlay, no burned captions) and was possibly misclassified `no_speech`. Systemic fix in WF02/WF03 not yet applied.
- **No paid acceptance run** should occur until the Discord interaction endpoint and the deterministic caption/final-asset corrections are ready.

## Discord credentials (status)
- The three required values have been added to `~/.openclaw/.env` as:
  - `LOOM_DISCORD_APP_ID`
  - `LOOM_DISCORD_PUBLIC_KEY`
  - `LOOM_DISCORD_BOT_TOKEN`
- ⚠️ They were added **after this OpenClaw session started** — a **gateway restart / new session is required** before attempting to read them.
- ⚠️ Do **not** attempt to retrieve secrets from the n8n credential API again (it returns metadata only, never secret values).
- Existing n8n credentials (do not modify/replace): `LOOM Discord Bot` (id `WRrZpiVtwMx4wRqa`, discordBotApi), `Loom Clip Review` (id `ZHHRF1nydWXp2wh9`, discordWebhookApi), `LOOM Supabase` (id `JoPSxTOjtLvDXLRV`).

## Source status
- The earlier OpenShorts job (`126d1217-…`) **disappeared from the running container** (backend restarted; job state is in-memory and its output dir was cleared), so its live files can no longer be inspected.
- **Next controlled source** (when the pending corrections are ready): the previously used video `https://www.youtube.com/watch?v=b7QosSwMW84`.
- **TABLED — do NOT submit:** the newer source `https://www.youtube.com/watch?v=UfQHEpf2q8k`.

## Supabase credential — RESOLVED (fresh key)
- Service key in `~/.openclaw/.env` (`LOOM_SUPABASE_SERVICE_ROLE_KEY`, `sb_secret_…` format) + `LOOM_SUPABASE_URL`. Auth verified HTTP 200. TOOLS.md JWT keys are superseded.
- Tables `loom_scores` + `loom_reviews` created (idempotent DDL: `infra/supabase/001_loom_ledger.sql`).

## Credential locations (values NOT recorded)
- n8n API key — `LOOM_N8N_API_KEY` env.
- OpenRouter / Qwen — Jasmine Phase 1 `docs/TOOLS.md`; n8n credential "OpenRouter API Key (live)".
- Discord webhook (failures) — n8n credential "LOOM Discord Alerts" (`JbK8O1h4xNVhdMrm`) → #factory-alerts.
- Discord webhook (review cards) — n8n credential "Loom Clip Review" (`ZHHRF1nydWXp2wh9`) → #clip-review.
- Discord bot (native buttons, pending) — `~/.openclaw/.env` `LOOM_DISCORD_APP_ID` / `LOOM_DISCORD_PUBLIC_KEY` / `LOOM_DISCORD_BOT_TOKEN` (new session required to read).
- Supabase service key — `~/.openclaw/.env` `LOOM_SUPABASE_SERVICE_ROLE_KEY` + `LOOM_SUPABASE_URL`.
- GHL — TOOLS.md files.

## Evidence (execution IDs)
- 3E/4A: `10080`, `10082`, `10084`, `10088`.
- 4B: `10098` (example.com card), `10105` (real-preview card), `10200` (#clip-review card).
- 4C/4D: `10099` (approve), `10100` (reject-no-category → error), `10101` (reject+category), `10102` (retry).
- Discordbot auto-fire bug (removed): `10106`–`10108`.

## Next steps (in order, when resumed in a fresh session)
1. Read the three Discord values from `~/.openclaw/.env`.
2. Build the native Discord interaction endpoint (Ed25519 + PING + 3s ack) with Approve/Reject/Hold buttons + reject modal.
3. Apply the caption/final-asset correction + `no_speech` vs `caption_render_failed` fix in WF02/WF03.
4. Run ONE controlled source (`b7QosSwMW84`), NOT `UfQHEpf2q8k`.

## Locked decisions (abridged)
Hook bound 3–10 words (3–7 preferred) · no-speech clips valid (no fabricated dialogue) · resume via explicit `resume` flag (no media regen) · Qwen via OpenRouter, validated in code · GHL = storage/distribution plane · Supabase/Postgres approved for LOOM ledger · Discord QC via webhook (buttons need bot token) · no publication without a persisted human approve. Integration research: `LOOM_Integration_Research.md`.
