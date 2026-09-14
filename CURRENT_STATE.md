# LOOM — Current State (handoff)

> ## 🚀 START HERE NEXT SESSION
>
> **Repository:** `/Users/user/Desktop/LOOM-Factory` · **Remote:** `Pettybytes/LOOM-Factory`
> **Nested frontend repo:** `/Users/user/Desktop/LOOM-Factory/openshorts` (upstream `mutonby/openshorts`, branch `loom/factory-mvp-baseline-fixes`)
>
> **Editor critical path is now the external HTTPS `/edit` route.** Discord "Edit" no longer launches the Discord Activity — it posts an ephemeral **Open Editor** link button to `{LOOM_EDITOR_PUBLIC_URL}/edit?token=<single-use>`. The Activity implementation is **preserved in code** (LoomActivityRoot, `/discord/editor/oauth`, `/discord/editor/pending`) but is **not** the critical path and is not tested.
>
> **Completed:** segments 3/4/5/6A/6B · migrations 001–016 · Stage 2 editor boundary (preview, trim, title, hook, social caption vs spoken subtitles, crop/reframe, save→render→register→card progress, secure download, full-field persistent draft, recoverable errors, idempotent retry, exactly-one revised card, "Return to Discord Review").
>
> **Acceptance status (corrected):** token exchange, authorized `/api/*` gateway, and full-field draft persistence are **E2E-verified** (fresh session, 2026-09-14). The **complete rerender → new immutable version → exactly-one revised card** loop has **NOT yet been manually proven** through the new HTTPS `/edit` path — Bryan's manual test is the acceptance gate. Session `502b89e7` and its rendered artifacts are **abandoned disposable fixtures** — do not recover or register them.

---

## 1. Repository status

### LOOM-Factory — `master`
- Remote: `origin https://github.com/Pettybytes/LOOM-Factory.git`
- Recent commits (newest → oldest):
  - `becfed8` Handoff: authoritative CURRENT_STATE + pruned HANDOFF
  - `8b2aecb` Editor: auth diagnostics + draft persistence + 401 recovery
  - `ef0322c` adapter: respond LAUNCH_ACTIVITY immediately (fix timeout) + async persist + 4h session TTL
  - `1979cb2` Stage 2: Edit launches Discord Activity (type 12) + pending-context resolution
  - `eb422e9` adapter: fix OAuth token exchange to form-encode
  - `715c743` Editor Activity: embedded SDK + server-side OAuth + scoped CSP/CORS
  - `113d785` Editor boundary security: authenticated allowlisted /api/* gateway; migration 014

### OpenShorts (nested) — `loom/factory-mvp-baseline-fixes` — **dirty (pre-existing, NOT editor work)**
- Remote: `origin https://github.com/mutonby/openshorts.git` (upstream; no Bryan-owned fork — push historically 403)
- HEAD: `7c8b038` "Editor: restore persisted trim draft on reload"
- Editor commits (newest → oldest): `7c8b038`, `cb9eb2a`, `564a02b`, `a7cb76d`, `159e8ad`
- **Dirty / untracked (pre-existing, exclude from editor work):**
  - Modified: `dashboard/index.html`, `docker-compose.yml`, `security_utils.py`
  - Untracked: `aborted-runs/`, `dashboard/src/lib/captionRealign.js`, `dashboard/src/lib/captionRealign.test.mjs`, `security_utils.py.bak-20260908212436`
- **Editor work lives in `dashboard/src/components/ManualTrimEditor.jsx`** and is preserved via the recovery-patch mechanism (see §9).

---

## 2. Migrations — 001–016 (all applied + live-verified)

| # | File | Change | Live status |
|---|------|--------|-------------|
| 001–009 | (segments 1–6A) | ledger, media, lineage, reviews, tokens, distribution, receipts, entitlement | ✅ applied |
| 010 | `010_tenant_distribution_publisher.sql` | publisher_user_id on tenant_distribution_profiles | ✅ applied, runtime-verified |
| 011 | `011_loom_media_content_type.sql` | `loom_media.content_type` | ✅ applied (NULL, fail-closed) |
| 012 | `012_loom_edit_sessions.sql` | `loom_edit_sessions` + `loom_edit_corrections` + lineage | ✅ applied |
| 013 | `013_register_edited_asset.sql` | atomic `register_edited_asset` RPC (service_role only) | ✅ applied |
| 014 | `014_loom_editor_session_token.sql` | `loom_edit_sessions.session_token_hash` | ✅ applied |
| 015 | `015_loom_editor_launch_context.sql` | `launch_user_id`/`launch_guild_id`/`launch_channel_id` (all `text`) | ✅ applied |
| 016 | `016_loom_editor_draft.sql` | `loom_edit_sessions.draft` (`jsonb`) | ✅ applied |

`loom_edit_sessions` exposes: `id, tenant_id, clip_id, source_asset_version, target_asset_version, reviewer, token_hash, session_token_hash, status, consumed_at, expires_at, launch_user_id, launch_guild_id, launch_channel_id, draft`.

---

## 3. Services, ports, processes, hostname, routing

**Public hostname (Tailscale Funnel):** `https://users-macbook-pro-1.taild000b2.ts.net` → `/` → `http://127.0.0.1:8081`

| Component | Container/process | Bind | Health |
|-----------|-------------------|------|--------|
| LOOM adapter | `adapter.py` (host, supervised) | `127.0.0.1:8090` | ✅ 200 (`/health`) |
| Caddy funnel | `loom-tunnel-proxy` | `127.0.0.1:8081–8082` | ✅ |
| OpenShorts backend | `openshorts-backend` | `127.0.0.1:8000` (loopback-only) | ✅ healthy |
| OpenShorts renderer | `openshorts-renderer` | `0.0.0.0:3100` | running |
| OpenShorts frontend (Vite dev) | `openshorts-frontend` | `0.0.0.0:5175→5173` | ✅ |

**Caddy `:8081` routing (`infra/tunnel-proxy/Caddyfile`):**
- `/edit*` → `Content-Security-Policy: frame-ancestors https://discord.com https://*.discord.com` (scoped to editor only)
- `/discord/interactions*` → adapter 8090 (Ed25519 signature verified)
- `/discord/editor/*` → adapter 8090 (single-use token / session validated)
- `/discord/card*` → adapter 8090, requires `Authorization: Bearer {$LOOM_TUNNEL_TOKEN}`
- `/api/*` → adapter 8090 (session-token + ownership allowlist; backend never public)
- default → Vite 5175 (`Host` rewritten to `localhost`)

**Caddy `:8082`:** `/relay*` → n8n `loom-openshorts-callback` webhook (local only, `X-LOOM-Relay-Token`).

**Adapter supervision:** `infra/discord-review/supervise.sh` restart loop (`while true`; adapter stdout/stderr tee'd to `adapter.log`; supervisor events to `supervisor.log`). Launched detached with `nohup ./supervise.sh > supervisor.log 2>&1 &`. Launch via `infra/discord-review/run.sh` (sources secrets from `~/.openclaw/.env`). Auto-restart is verified: kill the adapter PID → `supervise.sh` relaunches within ~3s.

---

## 4. Discord application / activity configuration (non-secret)

- **Application:** "LOOM Clip Review" · **App ID:** `1548188833386995742`
- **Guild:** "Clippy Factory" · **Guild ID:** `1546266463663423518`
- **Entry-point command:** global `launch` (type 4 = ENTRY_POINT) · id `1548851573944483851` · `handler: 2` (embedded app) — *preserved but not the critical path*
- **App flags:** `131072` = EMBEDDED (Activities enabled)
- **bot_public:** `False` · **integration_types_config:** `{"0": {}, "1": {}}` (guild + user install)
- **Activity URL mapping:** `/` → `users-macbook-pro-1.taild000b2.ts.net` — *preserved but not the critical path*
- **Bryan's Discord user id (reviewer):** `688976899094872148`

---

## 5. Credential inventory — names + secure locations only (NO values)

**Configured location `~/.openclaw/.env` (all present):**

| Env var | Purpose |
|---------|---------|
| `LOOM_DISCORD_APP_ID` | Discord application id (public) |
| `LOOM_DISCORD_PUBLIC_KEY` | Interactions Endpoint Ed25519 verification |
| `LOOM_DISCORD_BOT_TOKEN` | Bot API calls (card send, channel resolve) |
| `LOOM_DISCORD_CLIENT_SECRET` | Server-side OAuth2 code exchange (never in browser) |
| `LOOM_DISCORD_OAUTH_REDIRECT_URI` | OAuth2 redirect (registered in Developer Portal) |
| `LOOM_N8N_API_KEY` | n8n review-decision webhook auth |
| `LOOM_SUPABASE_URL` | Supabase REST base URL |
| `LOOM_SUPABASE_SERVICE_ROLE_KEY` | Supabase service_role (RPC + session writes) |
| `LOOM_EDITOR_PUBLIC_URL` | External HTTPS editor origin for the `/edit` deep-link (e.g. `https://users-macbook-pro-1.taild000b2.ts.net`) |

**Configured location `infra/tunnel-proxy/.env`:**

| Env var | Purpose |
|---------|---------|
| `LOOM_TUNNEL_TOKEN` | Card-send endpoint auth (Caddy `@card` gate) |
| `LOOM_RELAY_TOKEN` | `:8082` n8n relay header |
| `LOOM_GEMINI_KEY` | (reserved; LLM-assist path, not wired end-to-end) |

**Optional adapter env (defaults exist, absent from `.env` is fine):** `LOOM_EDITOR_CORS_ORIGINS`, `LOOM_EDITOR_BASE_URL`, `LOOM_EDITOR_ORIGIN`.

> ⚠️ **Never copy secret values into Git or the handoff.** Reference `~/.openclaw/.env` and `infra/tunnel-proxy/.env` only.

---

## 6. Editor / session architecture (critical path = external HTTPS `/edit`)

**Launch (clip-specific Edit):**
1. Reviewer clicks **Edit** → adapter verifies Ed25519, parses `custom_id`, ACKs with `DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE` (type 5, ephemeral) — no DB/network work on the ack path.
2. Adapter resolves the durable review token → identity, registers a **single-use editor token** (hash-only in DB), then PATCHes the deferred placeholder (`messages/@original`) into an ephemeral **Open Editor** link button → `{LOOM_EDITOR_PUBLIC_URL}/edit?token=<single-use>`.

**Editor boot (deep-link):**
1. Frontend `/edit` route reads `token` from the URL, strips it via `history.replaceState`, and POSTs it to `/discord/editor/exchange` (single-use: first exchange consumes it).
2. Exchange returns `job_id`, `clip_index`, `source_asset_version`, `launch_guild_id`, `launch_channel_id`, and mints the **short-lived editor session token** (4h) that authenticates all subsequent `/api/*` calls.
3. `ManualTrimEditor` loads status/EDL/transcript/scenes through the authenticated `/api/*` gateway.

**Session TTLs:**
- `EDITOR_TOKEN_TTL_SECONDS = 15 * 60` — single-use **launch** window.
- `EDITOR_SESSION_TTL_SECONDS = 4 * 3600` — **authenticated** editor session (draft resumable; minting extends `expires_at`).

**API allowlist + ownership boundary** (`adapter._authorize_editor_path`): session must match `job_id` + `clip_index`. Allowed: `GET /api/status/{job}`, `GET /api/source/{job}`, `GET /api/clip/{job}/{idx}/(transcript|edl|scenes)`, `GET /api/render/{id}`, `POST /api/subtitle`, `POST /api/clip/rerender`, `POST /api/clip/reframe`. Everything else → 403; unauthenticated → 401; backend loopback-only.

**Draft persistence:** `POST /discord/editor/draft` (session-token authed) writes the **full** editable draft (`trim_start`, `trim_end`, `framing`, `title`, `hook`, `social_caption`, `caption_text`, `caption_position`, `caption_style`, `caption_uppercase`, `instruction`) before render; restored on reload.

**Completion:** `POST /discord/editor/complete` → atomic `register_edited_asset` RPC (new `asset_version`, lineage, immutable correction log, session completed) → exactly one revised `final_publication` re-review card (dedup'd by `check_existing_card`).

---

## 7. Abandoned disposable fixtures (do NOT recover or register)

**Session `502b89e7-1c15-4b49-8ab4-773a5a2ef667`** (tenant `dreemzy`, clip `6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1`) and its rendered artifacts (`recut_1789357887_51b854_…_clip_2.mp4`, `subtitled_1789358004_…`) are **abandoned disposable test fixtures**. No `render-1.3` was registered; no correction row; no card. Do not spend further time reconstructing or investigating them — a fresh-session E2E has superseded this path.

---

## 8. Editor completion boundary (Stage 2 acceptance contract)

The editor now covers: **preview, trim, title, hook, separate social caption + spoken subtitles, crop/reframe (auto/full/track), save→render→register→card progress, secure download, persistent draft for every editable field, recoverable errors, idempotent retry, exactly one revised re-review card, "Return to Discord Review" link.** Post creation/distribution remains **excluded** from the editor.

**Verification status:** token exchange, authorized `/api/*`, and draft persistence are E2E-verified. The full **rerender → immutable version → revised card** loop has **not yet been manually proven** through the new HTTPS path.

---

## 9. Recovery-patch status (OpenShorts dashboard changes)

- `openshorts-recovery/0001-fix-caption-contract.patch`
- `openshorts-recovery/0002-discord-editor.patch`
- `openshorts-recovery/EDITOR_README.md`, `openshorts-recovery/README.md`

The OpenShorts dashboard changes live in the nested repo on `loom/factory-mvp-baseline-fixes`; upstream `mutonby/openshorts` push is 403 (no Bryan-owned fork). Editor work is preserved via the recovery patch mechanism.

**Restart / build commands:**
- Adapter: `cd /Users/user/Desktop/LOOM-Factory/infra/discord-review && exec ./run.sh`
- Adapter supervision (detached): `cd /Users/user/Desktop/LOOM-Factory/infra/discord-review && nohup ./supervise.sh > supervisor.log 2>&1 &`
- Frontend (dev, HMR already running): `docker exec openshorts-frontend sh -c 'cd /app && npm run dev -- --host'`
- Frontend build check: `docker exec openshorts-frontend sh -c 'cd /app && npx vite build --logLevel error'`
- Caddy reload: `docker exec loom-tunnel-proxy caddy reload --config /etc/caddy/Caddyfile`

---

## 10. Scope decision — IMPLEMENTED

The Stage 2 fallback is **implemented**: Edit opens a **secure external HTTPS editor link** (single-use token → `/discord/editor/exchange` → authenticated `/api/*` gateway). Never `localhost`; job identity is resolved server-side from the token, never trusted from the URL. The **Discord Activity implementation is preserved for later** but removed from the Stage 2 critical path.
