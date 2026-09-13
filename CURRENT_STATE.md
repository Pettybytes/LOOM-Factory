# LOOM — Current State (handoff)

*Updated: 2026-09-13 (EDT) — public Discord interaction path completion*

## This session's objective — COMPLETE (one real click still pending Bryan)

Bounded completion of the public Discord interaction path. **No OpenShorts
submission, no media job, no Gemini/Qwen call, no rescore, no master
regeneration, no publication** — all media evidence reused from acceptance job
`6c46c3ef-5eba-40bc-9b50-8f3457d1db79`.

## 1. Public reachability — REPAIRED + VERIFIED

- **Root cause:** the Tailscale Funnel was configured to expose `/` →
  `127.0.0.1:8081` (the OpenShorts/Caddy proxy), and the tailnet MagicDNS name
  was not resolving through public DNS.
- **Fix:** `tailscale funnel reset` then
  `tailscale funnel --bg --set-path /discord/interactions http://127.0.0.1:8090`.
  Only `/discord/interactions` is now publicly exposed; OpenShorts, `/discord/card`,
  `/health`, and everything else are unreachable publicly (404).
- **Public DNS evidence:** authoritative NS (`ns1.dnsimple.com`) returns
  `NOERROR` with A records `209.177.145.97` / `209.177.145.192` (Tailscale
  funnel ingress). Quad9 (`9.9.9.9`) resolves the same; `8.8.8.8`/`1.1.1.1` may
  still serve a stale NXDOMAIN negative cache (SOA minimum TTL 300s).
- **HTTPS certificate:** valid (Let's Encrypt `YE2`), subject/SAN
  `users-macbook-pro-1.taild000b2.ts.net`, valid 2026-09-06 → 2026-12-05.
- **Signature rejection verified via public URL:**
  - missing signature headers → 401
  - invalid `X-Signature-Ed25519` → 401 `{"error":"invalid signature"}`
- **Unrelated paths verified 404 via public URL:** `/discord/card`, `/`, `/health`,
  `/api/process`.
- **Node capability:** self node has `funnel` + `funnel-ports?ports=443,8443,10000`
  + `AllowFunnel: true` — the plan DOES permit Funnel; it was a config problem,
  not an account limitation.

## 2. Adapter — supervised, path-correct, display-safe

- **Path fix:** Tailscale `--set-path` strips the public prefix before proxying,
  so the adapter now accepts BOTH `/discord/interactions` and `/` for the
  interactions endpoint (verified 401 on both, not 404).
- **Supervision:** `infra/discord-review/supervise.sh` (restart loop, detach via
  `nohup ./supervise.sh > supervisor.log 2>&1 &`). launchd was attempted but macOS
  TCC blocks it from reading `~/Desktop` — documented; the restart loop is the
  supervision mechanism.
- **Card display fixes (no model, no rescore):**
  - `Source title` and `Proposed Shorts title` shown as separate fields; embed
    title = proposed Shorts title (or "LOOM Candidate"). Missing proposed title
    → "Not generated"/"Unavailable" — never invented.
  - Deterministic `mask_explicit()` masks explicit/slur terms on the card only
    (raw preserved in Supabase); adds a visible `⚠️ Explicit language` field.
  - `⚠️ Rights/policy` field shown only when already present (from
    `hard_failure_codes`).
  - `Publication` field → `disabled` (MVP always disabled unless explicitly enabled).
- **Existing card corrected in place** (message `1548515929871360020` in
  #clip-review): title now `N****, I'm Clownin' - Shots Fired at DDG`, separate
  Source/Proposed fields, masked hook, explicit-language warning, publication
  disabled. Buttons Approve/Reject/Hold still present.

## 3. Workflow threading (WF01/02/03/04 — all active)

`source_title`, `proposed_shorts_title`, `rights_policy` now threaded:
- WF03 `Attach Job and Tenant Context` carries `clip_title`
  (`video_title_for_youtube_short`).
- WF02 `Normalize Candidate Data` + WF01 `Return Validated Result` carry
  `proposed_shorts_title` / `source_title`.
- WF04 `Normalize Candidate` + `Format Candidate Card` pass
  `source_title` / `proposed_shorts_title` / `rights_policy` /
  `publication_enabled: false` into the adapter card payload.

All 5 LOOM workflows remain `active=True`. Jasmine/Derm untouched.

## 4. Caption visual evidence (frames extracted from existing master)

Frames extracted from
`subtitled_1789264697_hooked_1789264688_..._clip_2.mp4` (clip 1, spoken):
- 0.8s / 2.5s → hook overlay at top, exactly once (then absent).
- 11.0s → burned captions at bottom ("WHO THE FUCK IS") during spoken portion.
- Master unchanged (13.8 MB, 1080×1920 h264). Proxy corresponds to the
  renderer-declared final asset.

## 5. OpenShorts fix — preserved durably in Pettybytes/LOOM-Factory

Upstream `mutonby/openshorts` push still 403 (no fork). Recovery artifact in
`openshorts-recovery/`:
- `README.md` — upstream repo, base commit, branch, commit hash, purpose,
  reapplication instructions, exclusions.
- `0001-fix-caption-contract.patch` — sanitized git patch (app.py only; no
  cloud/media/secrets/credentials/env).

## Remaining (requires Bryan — do NOT proceed autonomously)

1. **Discord Developer Portal:** enter the Interactions Endpoint URL
   `https://users-macbook-pro-1.taild000b2.ts.net/discord/interactions` at
   **Discord Developer Portal → LOOM Clip Review application → General Information
   → Interactions Endpoint URL**. Discord's real validation PING/PONG must
   succeed (adapter already answers `{"type":1}` within 3s).
2. **One real click:** after the portal accepts the endpoint, Bryan clicks exactly
   ONE action (prefer **Hold** for the harmless first test) on a clearly labeled
   test card. Bentley must NOT click/decide on Bryan's behalf.
3. After that click, verify the full signed path: genuine Discord interaction →
   raw-body Ed25519 verify → ≤3s ack → resolve tenant/clip/asset → exactly one
   durable Supabase `loom_reviews` row → reviewer from Discord → card edited
   (decision/reviewer/timestamp) + controls disabled → duplicate re-delivery
   creates no new decision → no publication.
4. **Reject modal:** already confirmed to open a structured modal (controlled
   `rejection_category` + optional instructions) — verified via a throwaway
   Ed25519 keypair instance WITHOUT persisting a second terminal decision.

## Native interactions are NOT yet "fully runtime-verified"

They are fully verified locally and via a throwaway self-signed keypair, but the
completion standard requires Discord itself to accept the public endpoint AND
Bryan's one real button click to complete the signed path end-to-end.

## n8n execution IDs (this session)

- WF04 title-threaded updates pushed (HTTP 200 ×4).

## Commit / push status

- LOOM-Factory repo (this session's changes): pending commit/push below.
- OpenShorts: local commit `9c4450a` (unchanged); upstream push 403.
