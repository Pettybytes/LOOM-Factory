# LOOM — HANDOFF

> **Primary execution authority:** `CURRENT_STATE.md`. If this file and
> `CURRENT_STATE.md` ever disagree, `CURRENT_STATE.md` wins.
>
> Never put API keys, tokens, passwords, cookies, secrets, complete auth
> headers, or private customer data in this file.

---

## Last Updated
2026-09-15 21:15 EDT (America/Detroit)

## Session handoff — STOP STATE (editor fixes complete, NOT manually accepted)

The four editor functional defects from the 2026-09-15 manual-acceptance report
were fixed and proven by the automated browser suite (9/9, mocked). **Manual
acceptance has NOT been re-run. No real rerender was performed. No `render-1.3`
exists.**

**Auto Edit no-op root cause:** `handleRerender` opened with
`if (!session || !edl) return;` — a silent no-op. `edl` was only ever set from
the live `/api/clip/{job_id}/{clip_index}/edl` endpoint, which 404s once the job
is evicted, so the button stayed disabled and the handler silently returned.
**Fix:** the durable bootstrap synthesizes the recipe into `edl`; the handler now
surfaces a recoverable error instead of silently returning; progress is the full
8-stage sequence `save → clean → render → hook → subtitle → register → card →
complete`.

**Hook hydration + replacement-not-stacking:** HookModal hydrates the current
burned hook and previews the new hook over the clean source. **Optional on-video
title/header overlay** added (Remotion `TitleOverlay`), separate from the metadata
title.

**⛔ CRITICAL BLOCKER — no durable clean pre-hook/pre-subtitle source.** The
durable `loom_media` lineage (`render-1.2 → render-1.1 → render-1.0`) has only the
already-burned `render-1.0` as its canonical asset. `clean_source_url` is `null`;
the clean master exists only transiently on the OpenShorts backend disk. The
automated hook-replacement proof used a **mock** clean source.

**Next action (do NOT begin this session):** persist/register an immutable clean
input artifact in `loom_media`; backfill the fixture only if identity is proven;
then re-test bootstrap against an evicted job before manual acceptance.

## Repository state

- **LOOM-Factory** `master` · HEAD `2398bbc` · clean · pushed to origin.
- **OpenShorts (nested)** `loom/factory-mvp-baseline-fixes` · HEAD `7f47de5` ·
  pre-existing dirty/untracked (do NOT touch): `dashboard/index.html`,
  `docker-compose.yml`, `security_utils.py` (modified); `aborted-runs/`,
  `security_utils.py.bak-20260908212436` (untracked).
- **Recovery artifacts** (`openshorts-recovery/`) refreshed: `0002-discord-editor.patch`
  md5 `b0867c9ec0e20d6550929ee8bce7a4b7`.

## Runtime (verified at stop)

- Adapter `127.0.0.1:8090` → `/health` 200; Caddy 8081/8082; backend 8000
  (root 404 normal); renderer 3100; frontend 5175→5173.
- Funnel: `https://users-macbook-pro-1.taild000b2.ts.net` → `:8081`.
- Adapter restart: `cd infra/discord-review && nohup ./supervise.sh > supervisor.log 2>&1 &`
- Frontend build check: `docker exec openshorts-frontend sh -c 'cd /app && npx vite build --logLevel error'`

## Credentials (names only — values in secure source locations)

- `~/.openclaw/.env`: `LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`,
  `LOOM_DISCORD_BOT_TOKEN`, `LOOM_DISCORD_CLIENT_SECRET`,
  `LOOM_DISCORD_OAUTH_REDIRECT_URI`, `LOOM_EDITOR_PUBLIC_URL`, `LOOM_N8N_API_KEY`,
  `LOOM_SUPABASE_URL`, `LOOM_SUPABASE_SERVICE_ROLE_KEY` (all present).
  `LOOM_EDITOR_CORS_ORIGINS`, `LOOM_EDITOR_BASE_URL`, `LOOM_EDITOR_ORIGIN`
  (optional, MISSING — defaults exist).
- `infra/tunnel-proxy/.env`: `LOOM_TUNNEL_TOKEN`, `LOOM_RELAY_TOKEN`,
  `LOOM_GEMINI_KEY` (all present).

## Editor work done this session (committed — NOT manually accepted)

Durable bootstrap hydration · Auto Edit no-op fix (8-stage progress, surfaced
errors) · hook hydration + replacement-not-stacking · on-video title/header
overlay. Automated Playwright suite passes **9/9 (mocked)**.

## Unresolved / NOT accepted

- **⛔ No durable clean pre-hook/pre-subtitle source** (`clean_source_url` null).
- Rerender → immutable version → exactly-one card loop: **manually unproven**.
- Download, Auto Edit end-to-end, subtitles: wired but not manually verified.
- Missing preview font `fonts/NotoSerif-Bold.ttf` — documented + deferred.
