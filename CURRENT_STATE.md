# LOOM — Current State (handoff)

*Updated: 2026-09-13 (EDT) — Segment 4 COMPLETE (runtime verified)*

## ✅ Segment 4 — Discord QC — COMPLETE (runtime verified end-to-end)

Bryan accepted the Discord Interactions Endpoint URL and personally clicked
**Hold** exactly once on the labeled test card. The full signed path is now
runtime-verified, not just locally simulated.

### Evidence (2026-09-13 02:52 UTC)

1. **Signed interaction reached the adapter** — Discord POSTed to the public
   endpoint `https://users-macbook-pro-1.taild000b2.ts.net/discord/interactions`;
   the adapter Ed25519-verified it over the exact raw body.
2. **Acknowledged within deadline** — the card was edited ~1s after the stored
   decision timestamp (`created_at` 02:52:33.489 → `edited_timestamp`
   02:52:34.583), within Discord's 3s interaction deadline.
3. **Correct clip / tenant / reviewer preserved** — clip
   `6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1`, tenant `loom-acceptance-test`,
   reviewer `passportpetty#688976899094872148` (Bryan's real Discord identity).
4. **Exactly one Hold decision** — `loom_reviews` had exactly one row:
   `decision=hold`, `final_status=on_hold`, `rejection_category=null`.
5. **No publication / GHL / retry / model / media regen** — `publication_enabled:
   false` at every node; the n8n review-decision webhook (exec `10214`, success)
   re-persisted idempotently; no GHL upload, no retry, no Gemini/Qwen call, no
   media regeneration.
6. **Duplicate handling cannot create/overwrite a second terminal decision** —
   DB-level `ON CONFLICT (clip_id) DO NOTHING` (first-terminal-decision-wins);
   verified empirically in prior sessions and preserved in this path.
7. **Card reflects the stored Hold** — embed title "✅ Decision recorded",
   fields Decision=hold (on_hold) / Reviewer=passportpetty#… / Timestamp, and
   `components` emptied (controls disabled).

### Cleanup (done)
- The clearly-labeled test Hold decision and a stale self-signed keypair-test
  row were **deleted** from `loom_reviews` (now empty). Bryan has NOT made a
  production review decision — this was a labeled test click.

## Next segment

**Segment 5A — GHL Media Storage.** Start with GHL authentication and the
media-upload plane (canonical storage for approved/published media). Do NOT
proceed to distribution until a real approved candidate exists. Segment 3 is
closed and must not be reopened.

## Adapter / infra (unchanged this round, still correct)
- Tailscale Funnel exposes ONLY `/discord/interactions → 127.0.0.1:8090`; public
  DNS resolves; valid Let's Encrypt cert; signature rejection (401) + unrelated
  paths (404) verified via public URL.
- Adapter supervised via `infra/discord-review/supervise.sh`; accepts both `/`
  and `/discord/interactions`; source vs proposed title separation; explicit-
  language masking; publication status field.
- All 5 LOOM workflows active; Jasmine/Derm untouched.
- OpenShorts caption-contract fix preserved in `openshorts-recovery/` (upstream
  push 403, no fork).

## n8n execution IDs (this round)
- `10214` — WF04 review-decision webhook (Hold), success.

## Commit / push status
- This round's handoff + roadmap updates: committed and pushed below.
