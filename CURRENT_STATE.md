# LOOM — Current State (handoff)

*Updated: 2026-09-12 (EDT)*

## Completed
- **Segment 3E — PASSED.** End-to-end, execution **10082**: callback → completed branch → split → WF02 → WF01 → Qwen scoring → aggregation (`total_candidates: 2`, `status: completed`).
- **Segment 4A — recorded.** Qwen structured scoring validated in code (not prompt-level).
- **All-cached resume edge case — fixed + verified.** Execution **10088** returns the completed aggregate with both candidates.

## 3E scores (job `126d1217-…`)
- `…:0` → **repairable, overall 81** (no-speech/instrumental; 10-word hook now within 3–10 bound).
- `…:1` → **reject, overall 31** (`rights_or_policy_risk` + `unintelligible_transcript`).
- Summary: `{publishable: 0, repairable: 1, reject: 1, failed: 0}`.

## 4B–4D — designed, not runtime-complete
- Design: `SEGMENT_4_DESIGN.md` (card fields, actions, `loom_reviews` schema, retry semantics, score persistence).
- Runtime tests **not completed**. Two blockers (causes **unconfirmed**):

1. **Supabase authentication error** — `edtcewkvnxrlzhqebayr.supabase.co/rest/v1/` returns **HTTP 401 "Invalid API key"** using the `service_role` key in TOOLS.md. *Claimed cause (unconfirmed):* stale key after project recreation (project created 2026-02-03; key `iat` 2025-01-22).
2. **Discord card send** — failed execution **10092** with `Could not get parameter "operation"` (Discord v2 router). Node config identical to the node that **succeeded** in execution **10059** (WF00 `Send Discord Alert`, integrated mode). *Claimed cause (unconfirmed):* n8n Discord v2 node quirk. (Prior attempt: execution 10091, same error.)

## Credential locations (values NOT recorded)
- n8n API key — `LOOM_N8N_API_KEY` env (`~/.openclaw/.env`); canonical in Jasmine Phase 1 `docs/TOOLS.md`.
- OpenRouter / Qwen — Jasmine Phase 1 `docs/TOOLS.md`; n8n credential "OpenRouter API Key (live)".
- Discord webhook — n8n credential "LOOM Discord Alerts" (id `JbK8O1h4xNVhdMrm`).
- Supabase `service_role` — Dreemzy Main Brain `TOOLS.md` + workspace `TOOLS.md` (currently 401).
- GHL — TOOLS.md files.

## Evidence (execution IDs)
3E/4A: `10080` (clip 0 Qwen), `10082` (3E accept), `10084` (clip 1 Qwen), `10088` (all-cached resume).
4B–4D: `10059` (Discord send success), `10091` + `10092` (Discord send failure — `operation` param).

## Next task
Resolve the two 4B–4D blockers (fresh Supabase key; Discord send path), then Segment 5: transfer one final clip to GHL Media Storage and persist the returned media identifier/URL.

## Locked decisions (abridged)
Hook bound 3–10 words (3–7 preferred) · no-speech clips valid (no fabricated dialogue) · resume via explicit `resume` flag (no media regen) · Qwen via OpenRouter, validated in code · GHL = storage/distribution plane · Supabase/Postgres approved for LOOM ledger. Integration research: `LOOM_Integration_Research.md`.
