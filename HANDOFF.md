# LOOM — HANDOFF

> **Primary execution authority:** `CURRENT_STATE.md`. If this file and
> `CURRENT_STATE.md` ever disagree, `CURRENT_STATE.md` wins.
>
> Never put API keys, tokens, passwords, cookies, secrets, complete auth
> headers, or private customer data in this file.

---

## Last Updated
2026-09-14 02:05 EDT (America/Detroit)

## Session handoff — current objective

**Stop. The HTTPS editor is NOT complete and NOT accepted.** Do not rerender,
register a version, or run acceptance tests. This is a handoff checkpoint, not a
completion.

Next session, follow the **locked order** in `CURRENT_STATE.md` §START HERE:

1. Read-only verify both repos + runtime services.
2. Fix reload hydration (session restore must reload media preview, transcript,
   scenes while preserving the draft).
3. Normalize scenes/transcript responses before render.
4. Make Subtitles do its job, or remove the redundant button.
5. State + verify what Auto Edit actually does.
6. Automated browser tests: initial load, edit, refresh restoration, API failure.
7. Only then ask Bryan for one final manual rerender acceptance.
8. After that: verify one immutable version, one revised card, Download, and
   idempotent replay; freeze the editor; move to Automated Source Discovery V1.

## Repository state (authoritative summary — see CURRENT_STATE.md for detail)

- **LOOM-Factory** `master` · HEAD `5bed726` · clean · in sync with origin.
- **OpenShorts (nested)** `loom/factory-mvp-baseline-fixes` · HEAD `b6da9f4` ·
  dirty/untracked (pre-existing, do NOT touch): `dashboard/index.html`,
  `docker-compose.yml`, `security_utils.py` (modified); `aborted-runs/`,
  `security_utils.py.bak-20260908212436` (untracked).
- **Recovery artifacts:** `openshorts-recovery/0002-discord-editor.patch`
  (+ `EditorErrorBoundary.jsx`, `captionRealign.js`/`.test.mjs` reference files).
  Patch is byte-identical to the live editor diff — all unpushed OpenShorts work
  is represented there.

## Runtime (verified healthy at handoff)

- Adapter `127.0.0.1:8090` → `/health` 200, supervised by `supervise.sh`.
- Caddy `loom-tunnel-proxy` 8081–8082; backend 8000 (healthy); renderer 3100;
  frontend 5175→5173.
- Funnel: `https://users-macbook-pro-1.taild000b2.ts.net`.
- Adapter restart: `cd infra/discord-review && nohup ./supervise.sh > supervisor.log 2>&1 &`
- Adapter logs: `infra/discord-review/adapter.log`, `supervisor.log`.

## Credentials (names only — values live in secure source locations)

- `~/.openclaw/.env`: `LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`,
  `LOOM_DISCORD_BOT_TOKEN`, `LOOM_DISCORD_CLIENT_SECRET`,
  `LOOM_DISCORD_OAUTH_REDIRECT_URI`, `LOOM_EDITOR_PUBLIC_URL`,
  `LOOM_N8N_API_KEY`, `LOOM_SUPABASE_URL`, `LOOM_SUPABASE_SERVICE_ROLE_KEY`
  (all present). `LOOM_EDITOR_CORS_ORIGINS`, `LOOM_EDITOR_BASE_URL`,
  `LOOM_EDITOR_ORIGIN` are optional and currently MISSING (defaults exist).
- `infra/tunnel-proxy/.env`: `LOOM_TUNNEL_TOKEN`, `LOOM_RELAY_TOKEN`,
  `LOOM_GEMINI_KEY` (all present).

> Reference these locations only — never copy values into Git.

## Editor architecture (condensed)

Discord "Edit" → immediate ephemeral type-4 ack ("Preparing secure editor…",
flushed, zero network on the 3s path) → async single-use token creation →
PATCH to "Open Editor" link → `/edit?token=<single-use>` → exchange consumes
token → 4h session token → allowlisted `/api/*` gateway → full-field draft
autosave → `/discord/editor/complete` registers exactly one new asset version +
one revised card.

## Unresolved (see CURRENT_STATE.md §7 verbatim)

Media preview/transcript/scenes fail to rehydrate after refresh; scenes can be
non-array (`(scenes || []).map is not a function`); Subtitles button does
nothing; Auto Edit end-to-end is unverified; Download unproven; the full
rerender → version → card loop remains manually unproven; no render-1.3 exists.

---

## Superseded history

Prior "Prior Objective" / Segment 2A–3E planning and the abandoned session
`502b89e7` fixtures are **superseded** by `CURRENT_STATE.md`. Do not treat any
old instruction below a stale `Last Updated` date as current.
