# LOOM — Current State (handoff)

> **This file is the execution authority.** If `HANDOFF.md` disagrees, this file wins.

---

## STOP STATE — 2026-09-15 ~21:15 EDT

**Editor functional-defect fixes are COMPLETE and committed. Manual acceptance has NOT been re-run. No real rerender was performed. No `render-1.3` exists.**

The four defects from the 2026-09-15 manual-acceptance report were fixed and proven by the automated browser suite (9/9, mocked):

1. **Hook replacement — not stacking.** HookModal now hydrates the current burned hook (text/position/size/style/duration) and previews the new hook over the **clean source**, never stacking on the already-burned derivative. The bootstrap exposes `has_burned_hook` / `has_burned_captions` (derived from the lineage source filename's `subtitled_` / `hooked_` prefixes) and a `clean_source_url` field.
2. **Optional on-video title/header overlay** — a new Remotion `TitleOverlay` layer, distinct from the metadata Shorts title; persisted in the draft.
3. **Hook styling** — hydrates current hook; the 6 presets + S/M/L + position + duration are the real controls. No free font selection or per-word color (stated plainly, not faked).
4. **Auto Edit no-op — ROOT CAUSE + FIX.** See below.

---

## 🎯 Auto Edit no-op — root cause (and the fix)

**Root cause:** `handleRerender` began with `if (!session || !edl) return;` — a silent no-op. `edl` was **only ever populated from the live `/api/clip/{job_id}/{clip_index}/edl` endpoint**, which returns 404 "Job not found" once the render job is evicted. In the durable path `edl` stayed `null`, so the button (`disabled={… || !edl || …}`) stayed disabled and the handler silently returned.

**Fix (committed):** the durable bootstrap synthesizes the recipe (`canonical_range` + `framing` + `source.duration`) into `edl`, and the handler no longer silently returns — it surfaces a recoverable error when the recipe is genuinely absent. Progress expanded to the full 8 stages: `save → clean → render → hook → subtitle → register → card → complete`.

---

## ⛔ CRITICAL BLOCKER — no durable clean pre-hook/pre-subtitle source

The durable `loom_media` lineage for the test clip is `render-1.2 → render-1.1 → render-1.0`. `render-1.0`'s `canonical_url` is **already burned** (`subtitled_…_hooked_…_clip_2.mp4`). **There is no durable clean input ancestor in LOOM** — the clean master exists only transiently on the OpenShorts backend disk (`<base>_clip_<n>.mp4`, resolved there via `_strip_burned_captions` / `_strip_burned_hook`).

- `resolve_durable_bootstrap` returns `clean_source_url: None` — honest, not fabricated.
- The automated hook-replacement preview used a **mocked** clean source URL.

**Next action (do NOT begin this session):**
1. Persist/register an **immutable clean input artifact** in `loom_media` (a new role/column, e.g. `clean_master_url` or a `storage_role='clean_input'` row) at ingest time.
2. Backfill the existing fixture **only if its clean master identity can be proven** (do not guess).
3. Then re-test bootstrap with an **evicted job** and confirm `clean_source_url` resolves before any manual acceptance.

---

## 1. Repository state

### LOOM-Factory — `master`
- **HEAD:** `2398bbc` · clean · pushed to `origin/master` (`origin https://github.com/Pettybytes/LOOM-Factory.git`).
- Latest commits: `2398bbc` (adapter burned-hook/captions flags + clean_source_url + patch refresh) → `46488e2` (durable bootstrap endpoint) → `c4008fc` (prior stop-state handoff).

### OpenShorts (nested) — `loom/factory-mvp-baseline-fixes`
- **HEAD:** `7f47de5` (committed locally; upstream push historically 403 — no Bryan-owned fork).
- Latest commits: `7f47de5` (hook replacement + title overlay + Auto Edit fix) → `dee6da5` (durable bootstrap hydration) → `66e8119` (subtitle/hook styling).
- **Pre-existing dirty/untracked (do NOT clean/reset/overwrite):**
  - Modified: `dashboard/index.html`, `docker-compose.yml`, `security_utils.py`
  - Untracked: `aborted-runs/`, `security_utils.py.bak-20260908212436`

### Recovery artifacts (`openshorts-recovery/`) — refreshed + pushed
- `0002-discord-editor.patch` regenerated to cover the editor file set (md5 `b0867c9ec0e20d6550929ee8bce7a4b7`), including the new `TitleOverlay.tsx`, `HookModal.jsx`, `ManualTrimEditor.jsx`, `RemotionPreview.jsx`, `ShortVideo.tsx`, `types.ts`.
- Reference files (`captionRealign.js`, `captionRealign.test.mjs`, `EditorErrorBoundary.jsx`) unchanged.

### Automated test harness (NOT in either repo)
- `/Users/user/.openclaw/workspace/loom-editor-test/editor-browser-test.cjs` + `fixtures/tiny.mp4` — Playwright, mocked endpoints, runs against `127.0.0.1:5175`. **9/9 pass, three consecutive runs, zero JS/page errors.** Covers: initial load, field edits, hook hydration+replacement, SubtitleModal, refresh restoration, Auto Edit chain, durable-bootstrap-with-404s, title overlay, forced-failure draft preservation.

---

## 2. Runtime + infrastructure

**Funnel hostname:** `https://users-macbook-pro-1.taild000b2.ts.net` → `/` → Caddy `:8081`

| Component | Container/process | Bind | Health |
|-----------|-------------------|------|--------|
| LOOM adapter | `adapter.py` (host process) | `127.0.0.1:8090` | ✅ `/health` 200 |
| Caddy funnel | `loom-tunnel-proxy` | `127.0.0.1:8081–8082` | ✅ Up |
| OpenShorts backend | `openshorts-backend` | `127.0.0.1:8000` (loopback-only) | ✅ Up (root 404 is normal) |
| OpenShorts renderer | `openshorts-renderer` | `0.0.0.0:3100` | ✅ Up |
| OpenShorts frontend (Vite dev) | `openshorts-frontend` | `0.0.0.0:5175→5173` | ✅ Up |

**Adapter supervision/logs:**
- Supervisor: `infra/discord-review/supervise.sh` (restart on exit → `supervisor.log`).
- Launcher: `infra/discord-review/run.sh` (sources secrets from `~/.openclaw/.env`, then `exec python3 adapter.py`).
- Adapter stdout/stderr → `infra/discord-review/adapter.log`.

**Safe restart commands:**
- Adapter (foreground): `cd infra/discord-review && exec ./run.sh`
- Adapter (detached, supervised): `cd infra/discord-review && nohup ./supervise.sh > supervisor.log 2>&1 &`
- Frontend build check: `docker exec openshorts-frontend sh -c 'cd /app && npx vite build --logLevel error'`
- Caddy reload: `docker exec loom-tunnel-proxy caddy reload --config /etc/caddy/Caddyfile`

**Caddy `:8081` route table (`infra/tunnel-proxy/Caddyfile`):** `/edit /edit/*` (CSP frame-ancestors Discord) · `/discord/interactions*` → 8090 (Ed25519) · `/discord/editor/*` → 8090 (session/token validated) · `/discord/card*` → 8090 (Bearer tunnel token) · `/api/*` → 8090 (session token + allowlist) · default → Vite 5175.
**Caddy `:8082`:** `/relay*` → n8n `loom-openshorts-callback` (local, `X-LOOM-Relay-Token`).

---

## 3. Discord application / channel / guild IDs

- **Application:** "LOOM Clip Review" · **App ID:** `1548188833386995742`
- **Guild:** "Clippy Factory" · **Guild ID:** `1546266463663423518`
- **Review channel:** resolved at runtime by name `clip-review`.
- **Bryan's Discord user id (reviewer):** `688976899094872148`

---

## 4. Migrations — 010–016 (all applied + live-verified)

| # | File | Change | Status |
|---|------|--------|--------|
| 010 | `010_tenant_distribution_publisher.sql` | `publisher_user_id` on `tenant_distribution_profiles` | ✅ applied |
| 011 | `011_loom_media_content_type.sql` | `loom_media.content_type` | ✅ applied |
| 012 | `012_loom_edit_sessions.sql` | `loom_edit_sessions` + `loom_edit_corrections` + lineage | ✅ applied |
| 013 | `013_register_edited_asset.sql` | atomic `register_edited_asset` RPC (service_role only) | ✅ applied |
| 014 | `014_loom_editor_session_token.sql` | `loom_edit_sessions.session_token_hash` | ✅ applied |
| 015 | `015_loom_editor_launch_context.sql` | `launch_user_id`/`launch_guild_id`/`launch_channel_id` | ✅ applied |
| 016 | `016_loom_editor_draft.sql` | `loom_edit_sessions.draft` (`jsonb`) | ✅ applied |

Test clip `6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1` currently has `render-1.0`, `render-1.1`, `render-1.2` only — **no `render-1.3`** (do not claim any).

---

## 5. Credential inventory (names + locations + present/missing only — never values)

**`~/.openclaw/.env`:** `LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`, `LOOM_DISCORD_BOT_TOKEN`, `LOOM_DISCORD_CLIENT_SECRET`, `LOOM_DISCORD_OAUTH_REDIRECT_URI`, `LOOM_EDITOR_PUBLIC_URL` (present); `LOOM_EDITOR_CORS_ORIGINS`, `LOOM_EDITOR_BASE_URL`, `LOOM_EDITOR_ORIGIN` (optional, MISSING — defaults exist); `LOOM_N8N_API_KEY`, `LOOM_SUPABASE_URL`, `LOOM_SUPABASE_SERVICE_ROLE_KEY` (present).

**`infra/tunnel-proxy/.env`:** `LOOM_TUNNEL_TOKEN`, `LOOM_RELAY_TOKEN`, `LOOM_GEMINI_KEY` (present).

> ⚠️ Never record or print secret values. Reference the files only.

---

## 6. Editor work completed this session (committed — NOT manually accepted)

1. **Durable bootstrap hydration** (prior session) — editor hydrates from `GET /discord/editor/bootstrap` (durable media/version records), not transient `/api/status`.
2. **Auto Edit no-op fix** — `edl` synthesized from durable recipe; silent `!edl` return replaced with surfaced error; 8-stage progress.
3. **Hook hydration + replacement-not-stacking** — current hook hydrated into HookModal; preview over clean source.
4. **On-video title/header overlay** — Remotion `TitleOverlay` + draft persistence, separate from metadata title.

Automated browser test passes **9/9 (mocked)** — this is **not** manual acceptance.

---

## 7. UNRESOLVED / NOT accepted

- **⛔ No durable clean pre-hook/pre-subtitle source** — `clean_source_url` is `null`; the clean master lives only transiently on the OpenShorts backend disk. Hook replacement against a fully-evicted job still depends on that transient file.
- The complete **rerender → immutable version → exactly-one revised card** loop remains **manually unproven**.
- **Download** manually unproven. **Auto Edit end-to-end** unverified against the real backend (automation used mocks). **Subtitles** wired, not manually verified. **Refresh hydration / scenes normalization** passed automation, not manually verified.

---

## 8. Acceptance status (do not overstate)

- **NOT accepted.** Nothing was manually proven this session. **No `render-1.3` exists. No real rerender performed.**
- **Committed (unaccepted):** the four editor fixes above + refreshed recovery artifacts.
- Missing preview font (`fonts/NotoSerif-Bold.ttf`) remains documented + deferred (Remotion preview only; server burn unaffected).
