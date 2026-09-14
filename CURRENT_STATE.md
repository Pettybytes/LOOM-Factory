# LOOM — Current State (handoff)

> **This file is the execution authority.** If `HANDOFF.md` disagrees, this file wins.

---

## 🛑 STOP STATE — 2026-09-14 ~03:23 AM EDT

**Manual acceptance did NOT run.** The attempt ended at the Discord doorway.

- At ~3:23 AM EDT (Sep 14), Bryan clicked **Edit** on the existing **render-1.2** review card.
- Discord displayed: **"The application didn't respond in time."**
- **No Open Editor button was received.**
- **No editor acceptance was performed. No rerender should be assumed. No `render-1.3` should be claimed.**
- No new asset version or revised card was created.

**Observed (logs preserved — diagnosis deliberately deferred to tomorrow):**
- `adapter.log`: no request entries around 03:20–03:29 EDT; last activity `GET /health` at 03:13:48. `editor/complete` count = **0**.
- `caddy.log`: `discord/interactions` count = **0**; `discord/editor` count = 2 (the OAuth + `pending` POSTs at 02:19:50 from the Activity launch). Earlier `dial tcp 192.168.65.254:8090: connect: connection refused` → 502 for `/api/config` (adapter was down at those moments), plus video-streaming aborts ("reading: context canceled" / "broken pipe").
- `supervisor.log`: adapter restarted several times (rc=143 SIGTERM) up to 01:49 EDT.
- Funnel: **ON**, `/` → `http://127.0.0.1:8081` (Caddy). Tailscale up.

**Log snapshots (local, NOT committed):** `/Users/user/.openclaw/workspace/loom-logs/2026-09-14-0323/` — `adapter.log`, `supervisor.log`, `caddy.log`, `funnel-status.txt`, `timestamp.txt`.

---

## 🚀 TOMORROW'S FIRST STEP

Determine whether the Edit interaction reached `/discord/interactions`:

1. **If it did** → fix the acknowledgement path (the type-4 ACK / type-12 LAUNCH_ACTIVITY must be flushed inside Discord's 3-second deadline).
2. **If it did not** → verify Discord → Funnel routing and endpoint availability (Interactions Endpoint URL, Tailscale funnel, Caddy `@editor` + `/discord/interactions*` route).

Do not proceed to editor acceptance until the Edit button reliably opens the editor.

---

## 1. Repository state

### LOOM-Factory — `master`
- **Remote:** `origin https://github.com/Pettybytes/LOOM-Factory.git`
- **HEAD:** `853f313` · **clean** · in sync with `origin/master`.
- Tonight: editor change committed in the nested repo (below), then recovery artifacts committed + pushed here (`853f313`).

### OpenShorts (nested) — `loom/factory-mvp-baseline-fixes`
- **Remote:** `origin https://github.com/mutonby/openshorts.git` (upstream; push historically 403 — no Bryan-owned fork).
- **HEAD:** `66e8119` (tonight's editor change, committed locally).
- **Pre-existing dirty/untracked (do NOT clean/reset/overwrite):**
  - Modified: `dashboard/index.html`, `docker-compose.yml`, `security_utils.py`
  - Untracked: `aborted-runs/`, `security_utils.py.bak-20260908212436`
- **Tonight's committed editor change:** `dashboard/src/components/ManualTrimEditor.jsx`.

### Recovery artifacts (`openshorts-recovery/`) — refreshed + pushed
- `0002-discord-editor.patch` regenerated; md5 `f39b3901a4f5ff497048878bf5ae4183` == live `git diff origin/main..HEAD` over the 7 editor files.
- Reference files `captionRealign.js`, `captionRealign.test.mjs`, `EditorErrorBoundary.jsx` verified byte-identical to the repo.
- `EDITOR_README.md` boundary updated for subtitle/hook styling + Auto Edit pipeline.

### Automated test harness (NOT in either repo)
- `/Users/user/.openclaw/workspace/loom-editor-test/` — Playwright regression suite (`editor-browser-test.cjs` + `fixtures/tiny.mp4`). Runs against `127.0.0.1:5175` with mocked endpoints. 7/7 passes, zero JS/page errors (mock only).

---

## 2. Runtime + infrastructure

**Funnel hostname:** `https://users-macbook-pro-1.taild000b2.ts.net` → `/` → Caddy `:8081`

| Component | Container/process | Bind | Health |
|-----------|-------------------|------|--------|
| LOOM adapter | `adapter.py` (host process) | `127.0.0.1:8090` | ✅ `/health` 200 (last activity 03:13:48) |
| Caddy funnel | `loom-tunnel-proxy` | `127.0.0.1:8081–8082` | ✅ Up |
| OpenShorts backend | `openshorts-backend` | `127.0.0.1:8000` (loopback-only) | ✅ Up (healthy) |
| OpenShorts renderer | `openshorts-renderer` | `0.0.0.0:3100` | ✅ Up |
| OpenShorts frontend (Vite dev) | `openshorts-frontend` | `0.0.0.0:5175→5173` | ✅ Up |

**Adapter supervision/logs:**
- Supervisor: `infra/discord-review/supervise.sh` (restart on exit → `supervisor.log`).
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
- **Review channel** (resolved at runtime by name `clip-review`).
- **Bryan's Discord user id (reviewer):** `688976899094872148`

---

## 4. Migrations — 010–016 (all applied + live-verified)

| # | File | Change | Live status |
|---|------|--------|-------------|
| 010 | `010_tenant_distribution_publisher.sql` | publisher_user_id on tenant_distribution_profiles | ✅ applied |
| 011 | `011_loom_media_content_type.sql` | `loom_media.content_type` | ✅ applied (NULL, fail-closed) |
| 012 | `012_loom_edit_sessions.sql` | `loom_edit_sessions` + `loom_edit_corrections` + lineage | ✅ applied |
| 013 | `013_register_edited_asset.sql` | atomic `register_edited_asset` RPC (service_role only, idempotent) | ✅ applied |
| 014 | `014_loom_editor_session_token.sql` | `loom_edit_sessions.session_token_hash` | ✅ applied |
| 015 | `015_loom_editor_launch_context.sql` | `launch_user_id`/`launch_guild_id`/`launch_channel_id` | ✅ applied |
| 016 | `016_loom_editor_draft.sql` | `loom_edit_sessions.draft` (`jsonb`) | ✅ applied |

Test clip `6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1` currently has `render-1.0`, `render-1.1`, `render-1.2` only — **no `render-1.3`** (do not claim any).

---

## 5. Credential inventory (names + location + present/missing only — never values)

**`~/.openclaw/.env`:** `LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`, `LOOM_DISCORD_BOT_TOKEN`, `LOOM_DISCORD_CLIENT_SECRET`, `LOOM_DISCORD_OAUTH_REDIRECT_URI`, `LOOM_EDITOR_PUBLIC_URL` (present); `LOOM_EDITOR_CORS_ORIGINS`, `LOOM_EDITOR_BASE_URL`, `LOOM_EDITOR_ORIGIN` (optional, MISSING — defaults exist); `LOOM_N8N_API_KEY`, `LOOM_SUPABASE_URL`, `LOOM_SUPABASE_SERVICE_ROLE_KEY` (present).

**`infra/tunnel-proxy/.env`:** `LOOM_TUNNEL_TOKEN`, `LOOM_RELAY_TOKEN`, `LOOM_GEMINI_KEY` (present).

> ⚠️ Never record or print secret values. Reference the files only.

---

## 6. Editor work completed tonight (committed — NOT manually accepted)

1. **Reload hydration fix** — session identity/auth/preview/transcript/scenes/draft survive refresh.
2. **scenes/transcript normalization** — non-array responses safely become arrays; recoverable errors on status/edl/transcript/scenes.
3. **SubtitleModal wired** — full styling (font/size/colors/opacity/position/casing/presets) → draft + `/api/subtitle`.
4. **HookModal wired** — text/position/S·M·L/6 styles/duration → draft + `/api/hook`.
5. **Auto Edit pipeline corrected** — `rerender → hook → subtitle → complete` (reapply_captions gated to avoid double-burn; `input_filename` chaining).
6. **Title** — confirmed metadata-only (persists → `proposed_title` → card).

Automated browser test (Playwright, **mocked endpoints**) passes 7/7 with zero JS/page errors. **This does NOT constitute acceptance.**

---

## 7. UNRESOLVED / NOT accepted (verbatim — do not mark accepted from automation)

- The complete HTTPS **rerender → immutable version → exactly-one revised card** loop remains **manually unproven** (blocked at the Discord doorway tonight).
- **Download** remains manually unproven.
- **Auto Edit** end-to-end remains unverified against the real backend (automation used mocks).
- **Subtitles** action — wired to SubtitleModal in code; NOT manually verified in the real editor.
- **Refresh hydration / media restoration + scenes normalization** — fixed in code + passed automation; NOT manually verified.
- **NEW (2026-09-14 ~03:23):** Discord "The application didn't respond in time" on the Edit button.

---

## 8. Acceptance status (do not overstate)

- **NOT accepted.** Nothing was manually proven tonight. No `render-1.3` exists. No new card was sent.
- **Committed at code level (unaccepted):** the six editor corrections above + recovery artifacts.
- **Missing preview font** (`fonts/NotoSerif-Bold.ttf`) remains **documented + deferred** (Remotion preview only; server burn unaffected).
