# LOOM — Current State (handoff)

> ## 🚀 START HERE NEXT SESSION
>
> **Repository:** `/Users/user/Desktop/LOOM-Factory` · **Remote:** `Pettybytes/LOOM-Factory`
> **Nested frontend repo:** `/Users/user/Desktop/LOOM-Factory/openshorts` (upstream `mutonby/openshorts`, branch `loom/factory-mvp-baseline-fixes`)
>
> **⚠️ FIRST ACTION (read-only, do not mutate, do not retry the rerender):**
> Diagnose the missing post-render completion. A clip-specific Activity edit ran end-to-end through the **successful 200 rerender**, but `/discord/editor/complete` **never fired** — no `target_asset_version`, no correction row, no revised card, and the Activity went blank. See the "Latest attempt — exact evidence" section below before any code change.
>
> **Completed segments:** 3 (ingest) · 4 (Discord QC) · 5 (GHL media storage) · 6A (tenant mapping) · 6B (distribution, accepted) · Stage 2 editor loop (runtime-proven on fixtures render-1.1/1.2)
>
> **Migrations applied + live-verified:** 001–016 (see table below).

---

## 1. Repository status (as of handoff commit)

### LOOM-Factory — `master` — **clean**
- Remote: `origin https://github.com/Pettybytes/LOOM-Factory.git`
- HEAD: `8b2aecb` "Editor: auth diagnostics + draft persistence + 401 recovery"
- Recent commits (newest → oldest):
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

---

## 2. Migrations — 010–016 (all applied + live-verified)

| # | File | Change | Live status |
|---|------|--------|-------------|
| 010 | `010_tenant_distribution_publisher.sql` | publisher_user_id on tenant_distribution_profiles | ✅ applied, runtime-verified |
| 011 | `011_loom_media_content_type.sql` | `loom_media.content_type` | ✅ applied (NULL, fail-closed) |
| 012 | `012_loom_edit_sessions.sql` | `loom_edit_sessions` + `loom_edit_corrections` + lineage | ✅ applied |
| 013 | `013_register_edited_asset.sql` | atomic `register_edited_asset` RPC (service_role only) | ✅ applied |
| 014 | `014_loom_editor_session_token.sql` | `loom_edit_sessions.session_token_hash` | ✅ applied |
| 015 | `015_loom_editor_launch_context.sql` | `launch_user_id`/`launch_guild_id`/`launch_channel_id` (all `text`) | ✅ applied, columns verified |
| 016 | `016_loom_editor_draft.sql` | `loom_edit_sessions.draft` (`jsonb`) | ✅ applied, column verified |

Live read-only verification confirmed `loom_edit_sessions` exposes: `id, launch_user_id, launch_guild_id, launch_channel_id, draft, session_token_hash, status`.

---

## 3. Services, ports, processes, hostname, routing

**Public hostname (Tailscale Funnel):** `https://users-macbook-pro-1.taild000b2.ts.net` → `/` → `http://127.0.0.1:8081`

| Component | Container/process | Bind | Health |
|-----------|-------------------|------|--------|
| LOOM adapter | `adapter.py` (host, PID varies) | `127.0.0.1:8090` | ✅ 200 |
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

**Adapter supervision:** `infra/discord-review/supervise.sh` restart loop. Adapter launched via `infra/discord-review/run.sh` (sources secrets from `~/.openclaw/.env`).

---

## 4. Discord application / activity configuration (non-secret)

- **Application:** "LOOM Clip Review" · **App ID:** `1548188833386995742`
- **Guild:** "Clippy Factory" · **Guild ID:** `1546266463663423518`
- **Entry-point command:** global `launch` (type 4 = ENTRY_POINT) · id `1548851573944483851` · `handler: 2` (embedded app)
- **App flags:** `131072` = EMBEDDED (Activities enabled)
- **bot_public:** `False` · **integration_types_config:** `{"0": {}, "1": {}}` (guild + user install)
- **Activity URL mapping:** `/` → `users-macbook-pro-1.taild000b2.ts.net`
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

**Configured location `infra/tunnel-proxy/.env`:**

| Env var | Purpose |
|---------|---------|
| `LOOM_TUNNEL_TOKEN` | Card-send endpoint auth (Caddy `@card` gate) |
| `LOOM_RELAY_TOKEN` | `:8082` n8n relay header |
| `LOOM_GEMINI_KEY` | (reserved; LLM-assist path, not wired end-to-end) |

**Optional adapter env (defaults exist, absent from `.env` is fine):** `LOOM_EDITOR_CORS_ORIGINS`, `LOOM_EDITOR_BASE_URL`, `LOOM_EDITOR_ORIGIN`.

> ⚠️ **Never copy secret values into Git or the handoff.** Reference `~/.openclaw/.env` and `infra/tunnel-proxy/.env` only.

---

## 6. Editor / session architecture

**Launch (clip-specific Edit):**
1. Reviewer clicks Edit → adapter verifies Ed25519, parses `custom_id`, responds **`LAUNCH_ACTIVITY` (callback type 12) immediately** (within Discord's 3s deadline; no DB work on the ack path).
2. Adapter persists the pending session **asynchronously** (resolve durable review token → identity → bind `launch_user_id` + guild/channel).

**Activity boot (generic or clip-specific):**
1. Frontend root (`LoomActivityRoot`) boots the Embedded App SDK → `ready()`.
2. OAuth: `authorize({response_type:'code'})` → `/discord/editor/oauth` (server-side code exchange, client secret backend-only) → `authenticate`.
3. Poll `/discord/editor/pending` (500ms × 8s, "Preparing editor…") for the pending session bound to the authenticated Discord user (user id resolved server-side via Discord `/users/@me`).

**Session TTLs:**
- `EDITOR_TOKEN_TTL_SECONDS = 15 * 60` — single-use **launch** window (pending → authenticated).
- `EDITOR_SESSION_TTL_SECONDS = 4 * 3600` — **authenticated** editor session (draft stays resumable; minting extends `expires_at`).

**API allowlist + ownership boundary** (`adapter._authorize_editor_path`): session must match `job_id` + `clip_index`. Allowed: `GET /api/status/{job}`, `GET /api/source/{job}`, `GET /api/clip/{job}/{idx}/(transcript|edl|scenes)`, `GET /api/render/{id}`, `POST /api/subtitle`, `POST /api/clip/rerender`, `POST /api/clip/reframe`. Everything else → 403; unauthenticated → 401; backend loopback-only.

**Draft persistence:** `POST /discord/editor/draft` (session-token authed) writes `loom_edit_sessions.draft` before render; restored on reload.

---

## 7. Latest attempt — exact evidence (do NOT retry until diagnosed)

**Session:** `502b89e7-1c15-4b49-8ab4-773a5a2ef667` (tenant `dreemzy`, clip `6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1`)

**Timeline (adapter + backend + Supabase, 2026-09-13 23:50–23:51 EDT):**

| Time | Event | Result |
|------|-------|--------|
| 23:50:43 | `POST /discord/interactions` → `[edit] LAUNCH_ACTIVITY` | 200, session persisted |
| 23:50:48–52 | Activity boot: `/api/config` (401×2, expected) → OAuth OK → `/discord/editor/pending` resolved | editor loaded; status/edl/transcript/scenes **all 200**, `fp=3d892a7ad0b4` |
| 23:51:19 | `POST /discord/editor/draft` | 200 saved — draft `{trim_start:26, trim_end:51.74, framing:full}` |
| 23:51:19 | `POST /api/subtitle` | **200** (user made a small caption/subtitle addition) |
| 23:51:26 | `POST /api/clip/rerender` | **200** — `fp=3d892a7ad0b4` (token fingerprint consistent across all calls) |
| — | `POST /discord/editor/complete` | **NEVER FIRED** |
| — | Activity UI | **became blank** |

**Resulting state (verified):**
- Session `502b89e7` still `status=active`, `target_asset_version=null`, `draft` present (trim 26→51.74).
- **No** `loom_edit_corrections` row for this session.
- **No** revised re-review card sent.
- No `register_edited_asset` RPC invocation (no new `render-1.3`).

**Most likely gap (hypothesis, to be confirmed read-only):** a frontend exception between `rrRes.json()` (rerender 200) and the `fetch(EDITOR_COMPLETE_URL)` call — likely in constructing the completion payload (`outputBasename(rrData?.new_video_url)` or a throw inside `handleRerender`), which is swallowed by the `catch` → `setError` but then the editor blanked. Confirm by reproducing with browser console open, or add completion-payload logging.

---

## 8. Editor completion boundary (Stage 2 acceptance contract)

The editor must cover: **title, hook, separate social caption + spoken subtitles, preview, trim, crop/reframe, rerender, secure download, visible progress, recoverable errors, persistent drafts, idempotent retry, exactly one revised re-review card.** Post creation/distribution remains **excluded** from the editor.

---

## 9. Recovery-patch status (OpenShorts dashboard changes)

- `openshorts-recovery/0001-fix-caption-contract.patch`
- `openshorts-recovery/0002-discord-editor.patch`
- `openshorts-recovery/EDITOR_README.md`, `openshorts-recovery/README.md`

The OpenShorts dashboard changes live in the nested repo on `loom/factory-mvp-baseline-fixes`; upstream `mutonby/openshorts` push is 403 (no Bryan-owned fork). Editor work is preserved via the recovery patch mechanism.

**Restart / build commands:**
- Adapter: `cd /Users/user/Desktop/LOOM-Factory/infra/discord-review && exec ./run.sh`
- Frontend (dev, HMR already running): `docker exec openshorts-frontend sh -c 'cd /app && npm run dev -- --host'`
- Frontend build check: `docker exec openshorts-frontend sh -c 'cd /app && npx vite build --logLevel error'`
- Caddy reload: `docker exec loom-tunnel-proxy caddy reload --config /etc/caddy/Caddyfile`

---

## 10. Proposed scope decision (RECORDED — NOT implemented)

**Recommended Stage 2 fallback:** restore Edit as a **secure external HTTPS editor link** using the existing single-use token/session exchange + authenticated `/api/*` gateway. Never use `localhost`; never expose job identity without authorization. **Preserve the Discord Activity implementation for later**, but remove it from the Stage 2 critical path.

*Status: proposed only. Awaiting Bryan's decision before any change.*
