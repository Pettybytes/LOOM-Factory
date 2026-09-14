# LOOM — HANDOFF

> **Primary execution authority:** `CURRENT_STATE.md`. If this file and
> `CURRENT_STATE.md` ever disagree, `CURRENT_STATE.md` wins.
>
> Never put API keys, tokens, passwords, cookies, secrets, complete auth
> headers, or private customer data in this file.

---

## Last Updated
2026-09-14 00:20 EDT (America/Detroit)

## Session handoff — current objective

Stage 2 editor is **paused pending read-only diagnosis** of a missing
post-render completion. Do **not** retry the rerender until diagnosed.

**First action next session:** restart the **adapter** (it is DOWN — not
listening on `127.0.0.1:8090`, crash loop rc=1, `supervise.sh` stopped 20:54
EDT Sep 13; `cd infra/discord-review && exec ./run.sh`). Then read
`CURRENT_STATE.md` §7 (exact evidence) and diagnose why
`/discord/editor/complete` never fired after a successful 200 rerender (session
`502b89e7`, Activity went blank, no correction row, no card).

## Current engineering state (authoritative summary — see CURRENT_STATE.md for detail)

- **Migrations 001–016 applied + live-verified** (see CURRENT_STATE.md §2).
- **Services running:** Caddy funnel (loom-tunnel-proxy, 127.0.0.1:8081–8082),
  OpenShorts backend (127.0.0.1:8000 loopback-only), renderer (3100), frontend
  Vite dev (5175→5173). **Adapter is DOWN** (127.0.0.1:8090 not listening; crash
  loop rc=1; supervisor stopped 20:54 EDT Sep 13).
- **Public hostname:** `https://users-macbook-pro-1.taild000b2.ts.net` → `/` →
  Caddy 8081.
- **Discord app:** "LOOM Clip Review" id `1548188833386995742`, guild
  "Clippy Factory" id `1546266463663423518`, Activity URL `/` → tailnet host.

## Credentials (names only — values live in secure source locations)

- `~/.openclaw/.env`: `LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`,
  `LOOM_DISCORD_BOT_TOKEN`, `LOOM_DISCORD_CLIENT_SECRET`,
  `LOOM_DISCORD_OAUTH_REDIRECT_URI`, `LOOM_N8N_API_KEY`, `LOOM_SUPABASE_URL`,
  `LOOM_SUPABASE_SERVICE_ROLE_KEY`.
- `infra/tunnel-proxy/.env`: `LOOM_TUNNEL_TOKEN`, `LOOM_RELAY_TOKEN`,
  `LOOM_GEMINI_KEY`.

> Reference these locations only — never copy values into Git.

## Editor architecture (condensed)

Single-use 15-min launch token → LAUNCH_ACTIVITY (type 12) ack → async session
persist bound to `launch_user_id` → Activity boots SDK + OAuth → polls
`/discord/editor/pending` → 4-hour authenticated session token → allowlisted
`/api/*` (job/clip ownership enforced) → draft persisted via
`/discord/editor/draft` before render.

## Recovery / restart / build

- Adapter: `cd /Users/user/Desktop/LOOM-Factory/infra/discord-review && exec ./run.sh`
- Frontend build check: `docker exec openshorts-frontend sh -c 'cd /app && npx vite build --logLevel error'`
- Caddy reload: `docker exec loom-tunnel-proxy caddy reload --config /etc/caddy/Caddyfile`
- OpenShorts dashboard changes live in nested repo `openshorts/` on branch
  `loom/factory-mvp-baseline-fixes`; recovery patches in `openshorts-recovery/`.

## Proposed (NOT implemented) scope decision

Restore Edit as a secure external HTTPS editor link (single-use token/session
exchange + authenticated `/api/*` gateway), never localhost. Preserve Discord
Activity for later; remove it from the Stage 2 critical path. Recorded only —
awaiting Bryan's decision.

---

## Superseded history

Prior "Prior Objective" / Segment 2A–3E planning detail in earlier revisions of
this file is **superseded** by `CURRENT_STATE.md`. Do not treat any old
instruction below a stale `Last Updated` date as current.
