# LOOM — Current State (handoff)

*Updated: 2026-09-12/13 (EDT)*

## Native Discord review controls — BUILT + RUNTIME VERIFIED

The native Discord review experience is now implemented and runtime-tested
end-to-end (not just statically).

### What was built
- **`infra/discord-review/adapter.py`** — a local Python adapter (binds
  127.0.0.1:8090) that owns Ed25519 signature verification (n8n cannot expose
  the exact raw request body) and forwards only VERIFIED normalized events.
  - `POST /discord/interactions` → Ed25519 verify → PING/PONG, component
    buttons (approve/reject/hold), and reject modal.
  - `POST /discord/card` → generates a Docker-independent review proxy
    (H.264/AAC/yuv420p/faststart, ≤ ~23 MB, 9:16 preserved), uploads it as a
    Discord attachment, and posts the card AS THE BOT with Approve/Reject/Hold
    buttons (opaque base64url `custom_id`, no secrets, <100 chars).
  - Decision persistence is idempotent **at the database level**:
    `ON CONFLICT (clip_id) DO NOTHING` → first terminal decision wins; duplicate
    deliveries/clicks cannot create a second decision or overwrite the first.
  - After a decision, the card is edited (type 7) to show reviewer/decision/
    timestamp and the controls are disabled. Reject opens a modal collecting a
    structured `rejection_category` (validated enum) + optional instructions.
  - Never uses state-changing GET; publication remains disabled.
- **`infra/discord-review/caption_contract.py`** — deterministic classifier for
  the caption/final-asset contract (4 cases all PASS).
- **Caddy (`infra/tunnel-proxy/Caddyfile`)** — new routes: `/discord/interactions*`
  (public, Ed25519 in adapter) and `/discord/card*` (tunnel-token gated) →
  `host.docker.internal:8090`.

### Runtime evidence (adapter + buttons + idempotency)
- Ed25519 verify unit test: valid sig → True; tampered body / wrong timestamp /
  wrong key → False. ✅
- Interactions endpoint E2E (self-generated keypair on a throwaway :8091
  instance): PING→`{"type":1}`; bad sig→401; approve→type 7 "Decision recorded"
  + Supabase row; reject→type 9 modal; modal submit→persists reject category. ✅
- Supabase idempotency (real DB): approve creates 1 row; subsequent hold/reject
  do NOT overwrite (first terminal decision wins); exactly 1 row remains. ✅
- Real card delivered to #clip-review (see acceptance run below). ✅

## Caption / final-asset contract — CORRECTED + RUNTIME VERIFIED

### Defect found and fixed (OpenShorts `app.py`)
- **`_canonical_clip_file` selected the final asset by `max(mtime)`**, which can
  surface a `hooked_` intermediate (hook burned, captions absent) instead of the
  renderer-declared final `subtitled_...hooked_...` file.
- **Fix:** `_clip_asset_contract()` now treats `ready_files` (the `CLIP_READY`
  marker printed by `main.py` only after the FULL reframe→watermark→hook→captions
  chain) as authoritative; `_canonical_clip_file` is only a disk-recovery
  fallback. The completion path and `_recover_jobs_from_disk` both use it.
- **Per-clip `no_speech`** is now derived from AUTHORITATIVE per-clip evidence —
  the transcript words overlapping this clip's `[start,end]` window
  (`_clip_speech_stats`), NOT the whole-video segment count and NOT a
  secondary/empty transcript fetch. A clip cut from an instrumental stretch
  (0 words in window) is `no_speech: true` → captions correctly not required;
  a spoken clip (words in window) is `no_speech: false`.
- The webhook clip entries (`_webhook_clip_entries`) now carry
  `ready_file`, `master_asset`, `captions_applied`, `hook_applied`, `no_speech`,
  `transcript_segment_count`.

### Workflow threading (WF01/WF02/WF03/WF04)
- WF03 `Attach Job and Tenant Context` carries the renderer-declared contract
  (`master_asset`, `ready_file`, `captions_applied`, `hook_applied`, `no_speech`,
  `transcript_segment_count`, `render_version`, `captions_expected`,
  `hook_expected`) through to WF02.
- WF02 `Normalize Candidate Data` uses authoritative `no_speech` from the
  renderer (never derives it from a possibly-empty secondary transcript).
- WF01 `Return Validated Result` forwards the contract unchanged.
- WF04 `Normalize Candidate` + `Format Candidate Card` carry the contract into
  the card payload; `Send Candidate Card` now POSTs to the adapter
  (`/discord/card`) as the bot (buttons + attachment) instead of the legacy
  webhook.

## One controlled acceptance run — COMPLETE

- **Source (exactly once):** `https://www.youtube.com/watch?v=b7QosSwMW84`
  (free yt-dlp metadata check first: 124s music video, 1.58M views, 48 formats,
  not a playlist). `UfQHEpf2q8k` NOT submitted.
- **OpenShorts job:** `6c46c3ef-5eba-40bc-9b50-8f3457d1db79` — submitted via
  `/api/process` with explicit `auto_hook=1`, `auto_hook_style=classic`,
  `output_format=vertical` (captions on by default). Single submission; no
  resubmission.
- **Result:** 2 clips, both hook-applied, `no_speech` per-clip correct:
  - clip 0 (21–36s, instrumental window, 0 words) → `hooked_` (hook, no
    captions), `no_speech: true` → captions correctly NOT required.
  - clip 1 (36–51.74s, spoken, 2 segments) → `subtitled_...hooked_...`
    (captions + hook), `captions_applied: true`, `no_speech: false`. ✅
- **Gemini cost:** `gemini-3.1-flash-lite`, 1634 in / 493 out tokens,
  total `$0.001148` (single OpenShorts job's internal Gemini call). No separate
  Gemini call; no Qwen score was run in this acceptance (scoring spine was
  already runtime-verified in prior sessions).
- **Real candidate card:** clip 1 delivered by the BOT to #clip-review
  (message `1548515929871360020`) with:
  - Discord attachment (16.37 MB MP4, under the 23 MB upload limit; plays from
    Discord CDN, no localhost:8000 dependency), master untouched (1080×1920).
  - Genuine Approve/Reject/Hold buttons; captions `expected=True applied=True`;
    hook `expected=True applied=True`; `no_speech: False`.
- **Button decision test:** persisted an `approve` for the real clip, verified a
  duplicate `hold`/`reject` did NOT overwrite, then **deleted the clearly-labeled
  test decision** (Supabase `loom_reviews` back to empty; no claim Bryan reviewed).

## Remaining blocker (precise)

- **Tailscale Funnel is NOT publicly resolvable.** `dig @8.8.8.8
  users-macbook-pro-1.taild000b2.ts.net` → NXDOMAIN; it only resolves via
  tailnet MagicDNS (100.87.102.23). Consequence: **n8n Cloud cannot reach the
  local adapter** (`WF04 "Send Candidate Card"` failed with "connection cannot
  be established" — executions `10209`/`10210`), and **Discord cannot reach the
  Interactions Endpoint** either.
- The adapter itself is fully verified locally (via a throwaway loopback
  instance). The card was delivered by invoking the adapter directly.
- **Manual step for Bryan:** the Interactions Endpoint URL
  `https://users-macbook-pro-1.taild000b2.ts.net/discord/interactions` must be
  entered in the Discord Developer Portal (Bot → "Interactions Endpoint URL"
  field), AND the Tailscale Funnel must be publicly resolvable first (fix the
  funnel public DNS / re-run `tailscale funnel`). Until then, real Discord button
  clicks cannot reach the adapter and n8n→adapter card sends fail.

## Discord credentials (unchanged, all nonempty in ~/.openclaw/.env)
`LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`, `LOOM_DISCORD_BOT_TOKEN` —
confirmed nonempty; values never printed/committed.

## n8n execution IDs (this session)
- WF04 card send: `10207`/`10206` (success, pre-change webhook path), `10209`/
  `10210` (error — funnel unreachable from n8n Cloud).

## Workflow active status (all 5 LOOM workflows active)
00 `IYB1Pp8cTMrghaoK`, 01 `Vttea1FqX4TVn4yH`, 02 `6RNa6TXeppCQ7bhh`,
03 `x1ZYzsLc6Jaow0Wm`, 04 `3yUBe8H14TjwDCGw` — all `active=True`. Jasmine/Derm
workflows untouched.
