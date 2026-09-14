# LOOM — Current State (handoff)

> **This file is the execution authority.** If `HANDOFF.md` disagrees, this file wins.

---

## 🚀 START HERE NEXT SESSION

> **Repository:** `/Users/user/Desktop/LOOM-Factory` · **Remote:** `Pettybytes/LOOM-Factory`
> **Nested frontend repo:** `/Users/user/Desktop/LOOM-Factory/openshorts` (upstream `mutonby/openshorts`, branch `loom/factory-mvp-baseline-fixes`)

**The HTTPS editor is NOT complete and NOT accepted.** Do not claim it complete. Do not rerender, register a version, or run acceptance tests until the locked order below is followed.

**Locked next-session order (do not reorder, do not skip):**
1. Read-only verify both repositories and runtime services (below).
2. Fix **reload hydration** so authenticated session restoration reloads media preview, transcript and scenes while preserving the draft.
3. **Normalize scenes/transcript responses before render** — non-array responses must safely become arrays or visible recoverable API errors.
4. Make the **Subtitles** action perform its intended function, or remove the redundant button if the inline spoken-subtitle controls already replace it.
5. State and verify what **Auto Edit** actually does (do not claim it works merely because the button exists).
6. Run automated browser tests for: initial load, edit, refresh restoration, API failure.
7. Only then ask Bryan for **one final manual rerender acceptance**.
8. After the manual pass: verify **one immutable version, one revised card, Download, and idempotent replay**; freeze the editor and move to Automated Source Discovery V1.

---

## 1. Repository state

### LOOM-Factory — `master`
- **Remote:** `origin https://github.com/Pettybytes/LOOM-Factory.git`
- **HEAD:** `5bed726a84d321a4e91bc5fe300154961abce62c`
- **ahead/behind:** `## master...origin/master` (in sync, no ahead/behind)
- **dirty/clean:** **clean** (no uncommitted changes)

**Relevant commits today (newest → oldest):**
- `5bed726` adapter: instant Edit ACK (type-4, flushed, zero network on 3s path)
- `f80c29e` recovery: refresh discord-editor patch + EditorErrorBoundary/captionRealign reference files
- `cc3072a` adapter: include persisted draft in editor token exchange (draft-survives-reload)
- `d11b8d1` recovery: refresh discord-editor patch (idempotent /edit bootstrap)
- `bd28f16` handoff: correct acceptance status — full rerender→version→card loop not yet manually proven
- `d60b192` recovery: refresh discord-editor patch + preserve captionRealign reference files
- `b38d95e` Editor: external HTTPS /edit critical path + full boundary + supervised adapter
- `18b0a61` Handoff repair: adapter DOWN + exact rerender evidence + draft caption-not-persisted

### OpenShorts (nested) — `loom/factory-mvp-baseline-fixes`
- **Remote:** `origin https://github.com/mutonby/openshorts.git` (upstream; push historically 403 — no Bryan-owned fork)
- **HEAD:** `b6da9f41a8495e7f84054e5bb9d81623c98b448f`
- **dirty / untracked (pre-existing — do NOT clean, reset, or overwrite):**
  - Modified: `dashboard/index.html`, `docker-compose.yml`, `security_utils.py`
  - Untracked: `aborted-runs/`, `security_utils.py.bak-20260908212436`
- **Editor commits (newest → oldest):** `b6da9f4`, `2358833`, `14462ce`, `7c8b038`, `cb9eb2a`, `564a02b`, `a7cb76d`, `159e8ad`

### Recovery-patch location + proof
- **Location:** `/Users/user/Desktop/LOOM-Factory/openshorts-recovery/`
  - `0001-fix-caption-contract.patch`
  - `0002-discord-editor.patch` — carries `dashboard/index.html`, `EditorErrorBoundary.jsx`, `ManualTrimEditor.jsx`, `lib/api.js`, `lib/captionRealign.js`, `lib/discordActivity.js`, `main.jsx`
  - `EditorErrorBoundary.jsx`, `captionRealign.js`, `captionRealign.test.mjs` (reference files)
  - `EDITOR_README.md`, `README.md`
- **Proof all unpushed OpenShorts work is represented:** the patch is generated as `git diff origin/main..HEAD` for the editor files; its md5 matches the live diff md5 exactly (`b7fb75d3811f61844633843c9179e315`), and a normalized comparison is byte-equal. No unpushed editor work is missing from the recovery artifacts.

---

## 2. Runtime + infrastructure

**Funnel hostname:** `https://users-macbook-pro-1.taild000b2.ts.net` → `/` → Caddy `:8081`

| Component | Container/process | Bind | Health |
|-----------|-------------------|------|--------|
| LOOM adapter | `adapter.py` (host process, PID varies) | `127.0.0.1:8090` | ✅ 200 (`/health`) |
| Caddy funnel | `loom-tunnel-proxy` | `127.0.0.1:8081–8082` | ✅ Up 29h |
| OpenShorts backend | `openshorts-backend` | `127.0.0.1:8000` (loopback-only) | ✅ Up 9h (healthy) |
| OpenShorts renderer | `openshorts-renderer` | `0.0.0.0:3100` | ✅ Up 9h |
| OpenShorts frontend (Vite dev) | `openshorts-frontend` | `0.0.0.0:5175→5173` | ✅ Up 5h |

**Adapter supervision/logs:**
- Supervisor loop: `infra/discord-review/supervise.sh` (restart on exit, `supervisor.log`).
- Launcher: `infra/discord-review/run.sh` (sources secrets from `~/.openclaw/.env`, then `exec python3 adapter.py`).
- Adapter stdout/stderr → `infra/discord-review/adapter.log`.

**Safe restart commands:**
- Adapter (foreground): `cd /Users/user/Desktop/LOOM-Factory/infra/discord-review && exec ./run.sh`
- Adapter (detached, supervised): `cd /Users/user/Desktop/LOOM-Factory/infra/discord-review && nohup ./supervise.sh > supervisor.log 2>&1 &`
- Frontend build check: `docker exec openshorts-frontend sh -c 'cd /app && npx vite build --logLevel error'`
- Caddy reload: `docker exec loom-tunnel-proxy caddy reload --config /etc/caddy/Caddyfile`

**Caddy `:8081` route table (`infra/tunnel-proxy/Caddyfile`):**
- `/edit /edit/*` → CSP `frame-ancestors https://discord.com https://*.discord.com`
- `/discord/interactions*` → adapter 8090 (Ed25519 signature verified)
- `/discord/editor/*` → adapter 8090 (single-use token / session validated)
- `/discord/card*` → adapter 8090, requires `Authorization: Bearer {$LOOM_TUNNEL_TOKEN}`
- `/api/*` → adapter 8090 (session-token + ownership allowlist; backend never public)
- default → Vite 5175 (`Host` rewritten to `localhost`)

**Caddy `:8082`:** `/relay*` → n8n `loom-openshorts-callback` webhook (local only, `X-LOOM-Relay-Token`).

---

## 3. Discord application / channel / guild IDs

- **Application:** "LOOM Clip Review" · **App ID:** `1548188833386995742`
- **Guild:** "Clippy Factory" · **Guild ID:** `1546266463663423518`
- **Review channel** (card send target, resolved at runtime by name `clip-review`): recent `discord_message_id` seen `1548847578089521152`
- **Bryan's Discord user id (reviewer):** `688976899094872148`

**Current Edit interaction behavior:** clicking **Edit** sends an **immediate ephemeral type-4** ack (`⏳ Preparing secure editor…`), flushed synchronously with **zero DB/network on the 3s path**; the single-use editor token is then created asynchronously and the message is PATCHed to the **Open Editor** link button.

---

## 4. Migrations — 010–016 (all applied + live-verified)

| # | File | Change | Live status |
|---|------|--------|-------------|
| 010 | `010_tenant_distribution_publisher.sql` | publisher_user_id on tenant_distribution_profiles | ✅ applied |
| 011 | `011_loom_media_content_type.sql` | `loom_media.content_type` | ✅ applied (NULL, fail-closed) |
| 012 | `012_loom_edit_sessions.sql` | `loom_edit_sessions` + `loom_edit_corrections` + lineage | ✅ applied |
| 013 | `013_register_edited_asset.sql` | atomic `register_edited_asset` RPC (service_role only) | ✅ applied |
| 014 | `014_loom_editor_session_token.sql` | `loom_edit_sessions.session_token_hash` | ✅ applied |
| 015 | `015_loom_editor_launch_context.sql` | `launch_user_id`/`launch_guild_id`/`launch_channel_id` | ✅ applied |
| 016 | `016_loom_editor_draft.sql` | `loom_edit_sessions.draft` (`jsonb`) | ✅ applied |

Live read confirmed `loom_edit_sessions` exposes: `draft`, `launch_user_id`, `launch_guild_id`, `launch_channel_id`, `session_token_hash`, `status`. `loom_media` for the test clip has `render-1.0`, `render-1.1`, `render-1.2` only — **no `render-1.3`** (do not claim any).

---

## 5. Credential inventory (names + location + present/missing only — never values)

**`~/.openclaw/.env`:**

| Var | Purpose | Status |
|-----|---------|--------|
| `LOOM_DISCORD_APP_ID` | Discord app id (public) | present |
| `LOOM_DISCORD_PUBLIC_KEY` | Interactions Endpoint Ed25519 verification | present |
| `LOOM_DISCORD_BOT_TOKEN` | Bot API calls (card send, channel resolve) | present |
| `LOOM_DISCORD_CLIENT_SECRET` | Server-side OAuth2 code exchange | present |
| `LOOM_DISCORD_OAUTH_REDIRECT_URI` | OAuth2 redirect (registered) | present |
| `LOOM_EDITOR_PUBLIC_URL` | External HTTPS editor origin for `/edit` deep-link | present |
| `LOOM_EDITOR_CORS_ORIGINS` | Optional CORS allowlist (default exists in code) | **MISSING (optional)** |
| `LOOM_EDITOR_BASE_URL` | Optional editor base URL (default exists) | **MISSING (optional)** |
| `LOOM_EDITOR_ORIGIN` | Optional editor CORS origin (default exists) | **MISSING (optional)** |
| `LOOM_N8N_API_KEY` | n8n review-decision webhook auth | present |
| `LOOM_SUPABASE_URL` | Supabase REST base URL | present |
| `LOOM_SUPABASE_SERVICE_ROLE_KEY` | Supabase service_role (RPC + session writes) | present |

**`infra/tunnel-proxy/.env`:**

| Var | Purpose | Status |
|-----|---------|--------|
| `LOOM_TUNNEL_TOKEN` | Card-send endpoint auth (Caddy `@card` gate) | present |
| `LOOM_RELAY_TOKEN` | `:8082` n8n relay header | present |
| `LOOM_GEMINI_KEY` | Reserved (LLM-assist path, not wired end-to-end) | present |

> ⚠️ Never record or print secret values, tokens, passwords, or hashes. Reference the files only.

---

## 6. Proven working (verified this session)

- Instant Discord Edit acknowledgement (immediate type-4, flushed) + asynchronous Open Editor link — 5/5 runs, ack path < 3 ms.
- Secure HTTPS `/edit` single-use token exchange.
- Server-side clip identity resolution (job_id/clip_index derived from the token, never trusted from a URL).
- Authenticated allowlisted `/api/*` gateway with `job_id` + `clip_index` ownership enforcement; backend loopback-only.
- Four-hour authenticated editor session (minting extends `expires_at`).
- Full-field draft autosave (title/hook/social-caption/spoken-subtitles/trim/framing/instruction) to server + session-scoped memory.
- Trim changes remain visible without immediately blanking (defensive `start<end` guard).
- Caption unchanged state correctly reports **preserved** (`changedCount === 0` short-circuits, independent of trim window).
- Error boundary displays a recoverable error instead of a blank page.
- Adapter supervision/restart behavior (kill → auto-relaunch in ~3s; verified multiple times).

---

## 7. Unresolved defects from the latest manual test (verbatim)

- After refresh, saved form values/trim survived, but the media preview became **"preview unavailable"**.
- Transcript and scenes disappeared after refresh.
- Clicking the visible **T · Subtitles** action produced no visible action.
- One reload produced the error boundary message: `(scenes || []).map is not a function`.
- Response normalization is unsafe: **scenes is sometimes not an array**.
- The complete **HTTPS rerender → immutable version → exactly-one revised-card loop remains manually unproven**.
- **Download remains manually unproven.**
- **Auto Edit's actual end-to-end behavior remains unverified**; do not claim it works merely because the button exists.
- **No render-1.3 acceptance should be claimed.**

---

## 8. UI status

- The current UI visually follows the OpenShorts Generated Shorts layout and **is acceptable as the Stage 2 visual baseline**.
- **No more visual polish is required now.**
- Functional stability and completion remain **unfinished**.
- **Post is excluded; Return to Review is retained.**

---

## 9. Acceptance status (do not overstate)

- **Verified:** instant ACK, token exchange, authorized `/api/*` gateway, 4h session, full-field draft autosave, preserved caption state, error boundary, supervision.
- **NOT manually proven:** the complete rerender → immutable version → exactly-one revised card loop; Download; Auto Edit end-to-end behavior. No `render-1.3` exists.
