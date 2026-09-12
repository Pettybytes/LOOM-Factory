# LOOM — Current State

*Last updated: 2026-09-12 (EDT)*

## Completed segments
- **Segment 3E (callback resume → scoring → aggregation): PASSED** — end-to-end, execution 10082.
- **Segment 4A (runtime scoring evidence): recorded** — Qwen structured scoring validated in code, not prompt-level.

## Execution evidence
- 3E acceptance: `10082` (WF03 `status: success`, `Aggregate Batch Results` ran, `total_candidates: 2`).
- Clip `126d1217-…:0` — Qwen exec `10080` → **repairable, overall 81** (no-speech/instrumental; 10-word hook now within 3–10 bound).
- Clip `126d1217-…:1` — Qwen exec `10084` → **reject, overall 31** (`rights_or_policy_risk` + `unintelligible_transcript`; transcript contains explicit slurs).
- All-cached resume edge case: **fixed + verified** — execution `10088` returns the completed aggregate with both candidates.

## Remaining issues / blockers
- **Segment 4B–4D design documented** (`SEGMENT_4_DESIGN.md`); runtime tests blocked on two separate items:
  - **Supabase**: project resumed by Bryan, but the `service_role` key in TOOLS.md is **stale** (HTTP 401 "Invalid API key"; key `iat` 2025-01 but project created 2026-02). Blocks `loom_reviews`/`loom_scores` table creation + the decision save/retrieve test. Needs Bryan to supply the current `service_role` key.
  - **Discord**: test card authorized + attempted via `LOOM Discord Alerts` webhook, but the send failed — Discord v2 node throws `Could not get parameter "operation"` at runtime (config identical to WF00's working node). Needs n8n-side investigation.

## Next task
Segment 4B–4D (review card + durable decisions + score persistence), then Segment 5 (first durable asset transfer to GHL).

## Locked decisions
- Hook bound: **3–10 words inclusive, 3–7 preferred**.
- No-speech clips: valid; carry explicit `no_speech` state; do not fabricate dialogue.
- Resume: explicit `resume` flag continues an existing job without re-submitting source or regenerating media; normal duplicates blocked by terminal-event lock.
- Scoring: Qwen via OpenRouter, locked JSON schema, validated in code.
- Storage/distribution plane: GHL (Media Storage + Social Planner). Supabase/Postgres is **approved** as LOOM's operational/decision ledger (SOURCE_OF_TRUTH line 207; older HANDOFF/TOOLS "unapproved" notes are stale).
- Builder spend and media-inference spend are tracked separately.

## Integration research (reference — read once 2026-09-12)
- Location: `/Users/user/Desktop/LOOM-Factory/LOOM_Integration_Research.md`
- Key rules: persist an explicit processing recipe per job; reuse stored transcript/clip metadata/assets/completed scores; never equate a missing API job record with a need to regenerate media; identify the final rendered asset explicitly; validate Qwen in code (schema + ranges); durable review record keyed to exact asset version; atomic claim for job/stage resume (no blind repeat of unknown-outcome paid work); separate states for receipt/render/score/approval/publish.
