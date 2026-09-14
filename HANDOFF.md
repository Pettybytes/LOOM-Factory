# LOOM — HANDOFF

> **Primary execution authority:** `CURRENT_STATE.md`. If this file and
> `CURRENT_STATE.md` ever disagree, `CURRENT_STATE.md` wins.
>
> Never put API keys, tokens, passwords, cookies, secrets, complete auth
> headers, or private customer data in this file.

---

## Last Updated
2026-09-14 00:55 EDT (America/Detroit)

## Session handoff — current objective

Stage 2 editor critical path is **moved to the external HTTPS `/edit` route**
and the editor boundary is **complete + E2E-verified**. Discord "Edit" now
posts an ephemeral **Open Editor** link button instead of launching the Discord
Activity.

**First action next session:** none blocking. The adapter is running under
`supervise.sh` supervision (auto-restart verified). Continue from
`CURRENT_STATE.md` §6/§8; the next natural step is product-level (real clip
through the full loop) or restoring the Discord Activity when Bryan wants it.

## Current engineering state (authoritative summary — see CURRENT_STATE.md for detail)

- **Migrations 001–016 applied + live-verified** (see CURRENT_STATE.md §2).
- **Adapter:** `adapter.py` supervised by `supervise.sh` (auto-restart loop,
  `adapter.log` tee), healthy at `127.0.0.1:8090` (`/health` → 200).
- **Services running:** Caddy funnel (loom-tunnel-proxy, 127.0.0.1:8081–8082),
  OpenShorts backend (127.0.0.1:8000 loopback-only), renderer (3100), frontend
  Vite dev (5175→5173).
- **Public hostname:** `https://users-macbook-pro-1.taild000b2.ts.net` → `/` →
  Caddy 8081.
- **Editor critical path:** external HTTPS `/edit?token=<single-use>` (NOT
  localhost, NOT the Discord Activity). Discord Activity implementation is
  preserved in code but not the critical path.

## Credentials (names only — values live in secure source locations)

- `~/.openclaw/.env`: `LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`,
  `LOOM_DISCORD_BOT_TOKEN`, `LOOM_DISCORD_CLIENT_SECRET`,
  `LOOM_DISCORD_OAUTH_REDIRECT_URI`, `LOOM_N8N_API_KEY`, `LOOM_SUPABASE_URL`,
  `LOOM_SUPABASE_SERVICE_ROLE_KEY`, `LOOM_EDITOR_PUBLIC_URL`.
- `infra/tunnel-proxy/.env`: `LOOM_TUNNEL_TOKEN`, `LOOM_RELAY_TOKEN`,
  `LOOM_GEMINI_KEY`.

> Reference these locations only — never copy values into Git.

## Editor architecture (condensed)

Single-use 15-min launch token → ephemeral **Open Editor** link → `/edit` reads
token → `/discord/editor/exchange` consumes it → mints 4h session token →
allowlisted `/api/*` (job/clip ownership enforced) → full-field draft persisted
via `/discord/editor/draft` → `/discord/editor/complete` registers exactly one
new asset version + one revised re-review card.

## Recovery / restart / build

- Adapter: `cd /Users/user/Desktop/LOOM-Factory/infra/discord-review && exec ./run.sh`
- Adapter supervision (detached): `cd /Users/user/Desktop/LOOM-Factory/infra/discord-review && nohup ./supervise.sh > supervisor.log 2>&1 &`
- Frontend build check: `docker exec openshorts-frontend sh -c 'cd /app && npx vite build --logLevel error'`
- Caddy reload: `docker exec loom-tunnel-proxy caddy reload --config /etc/caddy/Caddyfile`
- OpenShorts dashboard changes live in nested repo `openshorts/` on branch
  `loom/factory-mvp-baseline-fixes`; recovery patches in `openshorts-recovery/`.

## Scope decision — IMPLEMENTED

Edit opens a secure external HTTPS editor link (single-use token → exchange →
authenticated `/api/*` gateway). The Discord Activity implementation is
preserved for later, but removed from the Stage 2 critical path.

---

## Superseded history

Prior "Prior Objective" / Segment 2A–3E planning detail, and the abandoned
session `502b89e7` fixtures, are **superseded** by `CURRENT_STATE.md`. Do not
treat any old instruction below a stale `Last Updated` date as current.
